import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star


class WakeCommandFilter(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        return bool(getattr(event, "is_at_or_wake_command", False))


class SpeedItemLookupPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.items: dict[str, dict[str, str]] = {}
        self.mysql_items: dict[str, dict[str, str]] = {}
        self.mysql_name_match_counts: dict[str, int] = {}
        self.name_index: list[tuple[str, str, str, str, str]] = []
        self.pending_choices: dict[str, dict[str, Any]] = {}

    async def initialize(self):
        await asyncio.to_thread(self._load_items)
        logger.info(
            f"SpeedItemLookupPlugin initialized, loaded {len(self.items)} item names"
        )

    @filter.command("itemid", alias={"物品", "道具"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def lookup_speed_item(self, event: AstrMessageEvent):
        query = self._extract_query(event.get_message_str())
        result = await self._lookup_query(
            event,
            query,
            reply_on_empty=True,
            reply_on_name_miss=True,
        )
        if result:
            yield result.stop_event()

    @filter.regex(r"^/?\s*\S.*")
    @filter.custom_filter(WakeCommandFilter, False)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def lookup_speed_item_short_slash(self, event: AstrMessageEvent):
        query = self._extract_short_slash_query(event.get_message_str())
        result = await self._lookup_query(
            event,
            query,
            reply_on_empty=False,
            reply_on_name_miss=False,
        )
        if result:
            yield result.stop_event()

    async def _lookup_query(
        self,
        event: AstrMessageEvent,
        query: str,
        *,
        reply_on_empty: bool,
        reply_on_name_miss: bool,
    ):
        group_id = str(event.get_group_id() or "").strip()
        if not self._group_allowed(group_id):
            return None

        if not query:
            if not reply_on_empty:
                return None
            event.should_call_llm(False)
            return event.plain_result(self._usage_text())

        selected_id = self._resolve_pending_choice(event, query)
        if selected_id:
            result = await self._build_item_result(event, selected_id)
            if result:
                event.should_call_llm(False)
            return result

        if query.isdigit():
            result = await self._build_item_result(event, query)
            if result:
                event.should_call_llm(False)
            return result

        matches = self._search_by_name(query)
        if not matches:
            matches = await asyncio.to_thread(self._search_mysql_by_name, query)
        if not matches:
            if not reply_on_name_miss:
                return None
            event.should_call_llm(False)
            return event.plain_result(f"没有找到名称包含「{query}」的物品。")

        if len(matches) == 1:
            result = await self._build_item_result(event, matches[0])
            if result:
                event.should_call_llm(False)
            return result

        self._store_pending_choices(event, query, matches)
        event.should_call_llm(False)
        return event.plain_result(self._format_choices(query, matches))

    async def _build_item_result(self, event: AstrMessageEvent, item_id: str):
        item = self._get_cached_item(item_id)
        if not item:
            item = await asyncio.to_thread(self._query_mysql_item_by_id, item_id)
        if not item:
            image_url = self._image_url(item_id)
            image_exists = await asyncio.to_thread(self._url_exists, image_url)
            if not image_exists and self._get_bool("silent_on_image_404", False):
                return None
            if not image_exists and not self._get_bool("reply_when_not_found", True):
                return None
            chain = [Comp.Plain(f"未收录物品ID：{item_id}")]
            if image_exists:
                chain.append(Comp.Image.fromURL(image_url))
            return event.chain_result(chain)

        image_url = self._image_url(item_id)
        image_exists = await asyncio.to_thread(self._url_exists, image_url)

        title = item.get("name") or f"物品 {item_id}"
        mess = item.get("mess") or item.get("type") or "未知类型"
        item_type = item.get("type") or "UNKNOWN"
        lines = [title, f"ID: {item_id}", f"类型: {mess}", f"Type: {item_type}"]
        if item.get("source") == "mysql":
            lines.append("来源: MySQL itemallnew")
        if not image_exists:
            lines.append("图片: 未找到")

        chain = [Comp.Plain("\n".join(lines))]
        if image_exists:
            chain.append(Comp.Image.fromURL(image_url))
        return event.chain_result(chain)

    def _load_items(self):
        data_path = Path(__file__).with_name("data") / "item_names.json"
        with data_path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
        if not isinstance(raw, dict):
            raise ValueError("data/item_names.json must be a JSON object")
        self.items = {
            str(item_id): {
                "name": str(info.get("name") or ""),
                "type": str(info.get("type") or ""),
                "mess": str(info.get("mess") or ""),
            }
            for item_id, info in raw.items()
            if isinstance(info, dict)
        }
        self.name_index = [
            (
                item_id,
                info.get("name", ""),
                info.get("type", ""),
                info.get("mess", ""),
                info.get("name", "").casefold(),
            )
            for item_id, info in self.items.items()
            if info.get("name")
        ]

    def _group_allowed(self, group_id: str) -> bool:
        allowed = self._get_list("allowed_group_ids")
        if not allowed:
            return True
        return group_id in {str(value).strip() for value in allowed if str(value).strip()}

    def _extract_query(self, message: str) -> str:
        text = re.sub(r"\s+", " ", (message or "").strip())
        if not text:
            return ""
        commands = ("itemid", "/itemid", "物品", "/物品", "道具", "/道具")
        lowered = text.casefold()
        for command in commands:
            command_lower = command.casefold()
            if lowered == command_lower:
                return ""
            if lowered.startswith(command_lower + " "):
                return text[len(command) :].strip()
        parts = text.split(" ", 1)
        if parts[0].casefold().lstrip("/") in {"itemid", "物品", "道具"}:
            return parts[1].strip() if len(parts) > 1 else ""
        return text

    def _extract_short_slash_query(self, message: str) -> str:
        text = re.sub(r"\s+", " ", (message or "").strip())
        query = text[1:].strip() if text.startswith("/") else text
        if not query:
            return ""
        command = query.split(" ", 1)[0].casefold()
        if command in {"itemid", "物品", "道具"}:
            return ""
        return query

    def _search_by_name(self, query: str) -> list[str]:
        needle = query.casefold().strip()
        if not needle:
            return []
        ranked: list[tuple[int, int, int, str]] = []
        for item_id, name, _item_type, _mess, haystack in self.name_index:
            if needle not in haystack:
                continue
            if haystack == needle:
                score = 0
            elif haystack.startswith(needle):
                score = 1
            else:
                score = 2
            ranked.append((score, len(name), int(item_id), item_id))
        ranked.sort()
        limit = max(1, self._get_int("max_search_results", 10))
        return [item_id for *_prefix, item_id in ranked[:limit]]

    def _search_mysql_by_name(self, query: str) -> list[str]:
        if not self._mysql_enabled():
            return []
        needle = query.strip()
        if not needle:
            return []
        limit = max(1, self._get_int("max_search_results", 10))
        table = self._mysql_identifier("mysql_table", "itemallnew")
        id_column = self._mysql_identifier("mysql_id_column", "ID")
        type_column = self._mysql_identifier("mysql_type_column", "Type")
        mess_column = self._mysql_identifier("mysql_mess_column", "Mess")
        name_column = self._mysql_identifier("mysql_name_column", "Name")
        sql = (
            f"SELECT `{id_column}` AS item_id, `{type_column}` AS item_type, "
            f"`{mess_column}` AS mess, `{name_column}` AS name "
            f"FROM `{table}` "
            f"WHERE `{name_column}` LIKE %s "
            f"ORDER BY (`{name_column}` = %s) DESC, "
            f"(`{name_column}` LIKE %s) DESC, CHAR_LENGTH(`{name_column}`), `{id_column}` "
            f"LIMIT {limit}"
        )
        count_sql = f"SELECT COUNT(*) AS match_count FROM `{table}` WHERE `{name_column}` LIKE %s"
        like = f"%{needle}%"
        prefix_like = f"{needle}%"
        try:
            with self._mysql_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(count_sql, (like,))
                    count_row = cursor.fetchone() or {}
                    self.mysql_name_match_counts[needle.casefold()] = int(
                        count_row.get("match_count") or 0
                    )
                    cursor.execute(sql, (like, needle, prefix_like))
                    rows = cursor.fetchall()
        except Exception as exc:
            logger.warning(f"SpeedItemLookupPlugin MySQL name fallback failed: {exc}")
            return []
        item_ids = []
        for row in rows:
            item = self._mysql_row_to_item(row)
            if not item:
                continue
            item_id = str(row.get("item_id") or "").strip()
            self.mysql_items[item_id] = item
            item_ids.append(item_id)
        return item_ids

    def _query_mysql_item_by_id(self, item_id: str) -> dict[str, str] | None:
        if not self._mysql_enabled() or not str(item_id).isdigit():
            return None
        cached = self.mysql_items.get(str(item_id))
        if cached:
            return cached
        table = self._mysql_identifier("mysql_table", "itemallnew")
        id_column = self._mysql_identifier("mysql_id_column", "ID")
        type_column = self._mysql_identifier("mysql_type_column", "Type")
        mess_column = self._mysql_identifier("mysql_mess_column", "Mess")
        name_column = self._mysql_identifier("mysql_name_column", "Name")
        sql = (
            f"SELECT `{id_column}` AS item_id, `{type_column}` AS item_type, "
            f"`{mess_column}` AS mess, `{name_column}` AS name "
            f"FROM `{table}` WHERE `{id_column}` = %s LIMIT 1"
        )
        try:
            with self._mysql_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, (int(item_id),))
                    row = cursor.fetchone()
        except Exception as exc:
            logger.warning(f"SpeedItemLookupPlugin MySQL ID fallback failed: {exc}")
            return None
        item = self._mysql_row_to_item(row)
        if item:
            self.mysql_items[str(item_id)] = item
        return item

    def _mysql_row_to_item(self, row: dict[str, Any] | None) -> dict[str, str] | None:
        if not row:
            return None
        item_id = str(row.get("item_id") or "").strip()
        if not item_id:
            return None
        return {
            "name": str(row.get("name") or ""),
            "type": str(row.get("item_type") or ""),
            "mess": str(row.get("mess") or ""),
            "source": "mysql",
        }

    def _mysql_enabled(self) -> bool:
        if not self._get_bool("mysql_fallback_enabled", False):
            return False
        return bool(
            str(self._get("mysql_host", "") or "").strip()
            and str(self._get("mysql_user", "") or "").strip()
            and str(self._get("mysql_password", "") or "").strip()
            and str(self._get("mysql_database", "") or "").strip()
        )

    def _mysql_connection(self):
        try:
            import pymysql
            import pymysql.cursors
        except ImportError as exc:
            raise RuntimeError("PyMySQL is not installed in AstrBot runtime") from exc
        return pymysql.connect(
            host=str(self._get("mysql_host", "") or "").strip(),
            port=self._get_int("mysql_port", 3306),
            user=str(self._get("mysql_user", "") or "").strip(),
            password=str(self._get("mysql_password", "") or ""),
            database=str(self._get("mysql_database", "player") or "player").strip(),
            charset=str(self._get("mysql_charset", "utf8mb4") or "utf8mb4").strip(),
            connect_timeout=max(1, self._get_int("mysql_timeout_sec", 3)),
            read_timeout=max(1, self._get_int("mysql_timeout_sec", 3)),
            write_timeout=max(1, self._get_int("mysql_timeout_sec", 3)),
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _mysql_identifier(self, key: str, default: str) -> str:
        value = str(self._get(key, default) or default).strip()
        if re.fullmatch(r"[A-Za-z0-9_]+", value):
            return value
        logger.warning(
            f"SpeedItemLookupPlugin ignored unsafe MySQL identifier {key}={value!r}"
        )
        return default

    def _get_cached_item(self, item_id: str) -> dict[str, str] | None:
        return self.items.get(str(item_id)) or self.mysql_items.get(str(item_id))

    def _count_cached_mysql_name_matches(self, query: str) -> int:
        needle = query.casefold().strip()
        if needle in self.mysql_name_match_counts:
            return self.mysql_name_match_counts[needle]
        return sum(
            1
            for item in self.mysql_items.values()
            if needle and needle in str(item.get("name") or "").casefold()
        )

    def _format_choices(self, query: str, item_ids: list[str]) -> str:
        total_matches = self._count_name_matches(query)
        lines = [f"找到 {total_matches} 个匹配「{query}」的物品，显示前 {len(item_ids)} 个："]
        for index, item_id in enumerate(item_ids, 1):
            item = self._get_cached_item(item_id) or {}
            name = item.get("name") or "未命名"
            mess = item.get("mess") or item.get("type") or "未知类型"
            lines.append(f"{index}. {name} / ID: {item_id} / {mess}")
        lines.append("继续发送 /序号 查看，例如：/1；也可用 /itemid 1")
        lines.append("也可以继续输入更精确的名称缩小范围。")
        return "\n".join(lines)

    def _count_name_matches(self, query: str) -> int:
        needle = query.casefold().strip()
        local_count = sum(1 for *_prefix, haystack in self.name_index if needle in haystack)
        if local_count:
            return local_count
        return self._count_cached_mysql_name_matches(query)

    def _store_pending_choices(
        self,
        event: AstrMessageEvent,
        query: str,
        item_ids: list[str],
    ) -> None:
        self.pending_choices[self._selection_key(event)] = {
            "query": query,
            "item_ids": item_ids,
            "created_at": time.time(),
        }

    def _resolve_pending_choice(
        self,
        event: AstrMessageEvent,
        query: str,
    ) -> str | None:
        choice = query.strip()
        choice = choice.removeprefix("#")
        match = re.fullmatch(r"第?(\d+)(?:个)?", choice)
        if not match:
            return None
        selected_index = int(match.group(1))
        pending = self.pending_choices.get(self._selection_key(event))
        if not pending:
            return None
        ttl = max(10, self._get_int("selection_ttl_sec", 300))
        if time.time() - float(pending.get("created_at") or 0) > ttl:
            self.pending_choices.pop(self._selection_key(event), None)
            return None
        item_ids = pending.get("item_ids") or []
        if 1 <= selected_index <= len(item_ids):
            return str(item_ids[selected_index - 1])
        return None

    def _selection_key(self, event: AstrMessageEvent) -> str:
        group_id = str(event.get_group_id() or "private")
        sender_id = str(event.get_sender_id() or "unknown")
        return f"{group_id}:{sender_id}"

    @staticmethod
    def _usage_text() -> str:
        return "\n".join(
            [
                "用法：/itemid 物品ID 或 /itemid 名称",
                "也可直接发送 /物品ID 或 /名称，例如：/74362、/爆天",
                "示例：/itemid 74362",
                "示例：/itemid 爆天",
                "多个匹配时，再发送 /1 或 /itemid 1 查看对应条目。",
            ]
        )

    def _image_url(self, item_id: str) -> str:
        base_url = str(
            self._get("image_base_url", "https://iips.speed.qq.com/images") or ""
        ).strip()
        base_url = base_url.rstrip("/") or "https://iips.speed.qq.com/images"
        return f"{base_url}/{item_id}.png"

    def _url_exists(self, url: str) -> bool:
        timeout = max(1, self._get_int("image_timeout_sec", 6))
        try:
            request = Request(url, method="HEAD", headers={"User-Agent": "AstrBot"})
            with urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                content_type = response.headers.get("Content-Type", "")
                return 200 <= int(status) < 300 and "image" in content_type.lower()
        except HTTPError as exc:
            if exc.code in {403, 405}:
                return self._url_exists_by_get(url, timeout)
            return False
        except (OSError, URLError, ValueError):
            return False

    @staticmethod
    def _url_exists_by_get(url: str, timeout: int) -> bool:
        try:
            request = Request(
                url,
                method="GET",
                headers={"User-Agent": "AstrBot", "Range": "bytes=0-0"},
            )
            with urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                content_type = response.headers.get("Content-Type", "")
                return 200 <= int(status) < 300 and "image" in content_type.lower()
        except (HTTPError, OSError, URLError, ValueError):
            return False

    def _get_list(self, key: str) -> list[Any]:
        value = self._get(key, [])
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [part.strip() for part in re.split(r"[,，\s]+", value) if part.strip()]
        return []

    def _get_bool(self, key: str, default: bool) -> bool:
        value = self._get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "y"}
        return bool(value)

    def _get_int(self, key: str, default: int) -> int:
        try:
            return int(self._get(key, default))
        except (TypeError, ValueError):
            return default

    def _get(self, key: str, default: Any = None) -> Any:
        try:
            return self.config.get(key, default)
        except AttributeError:
            try:
                return self.config[key]
            except Exception:
                return default
