import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star


class SpeedItemLookupPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.items: dict[str, dict[str, str]] = {}

    async def initialize(self):
        await asyncio.to_thread(self._load_items)
        logger.info(
            f"SpeedItemLookupPlugin initialized, loaded {len(self.items)} item names"
        )

    @filter.regex(r"^\s*\d+\s*$")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def lookup_speed_item(self, event: AstrMessageEvent):
        item_id = event.get_message_str().strip()
        group_id = str(event.get_group_id() or "").strip()

        if not self._group_allowed(group_id):
            return

        if not self._valid_id_length(item_id):
            event.should_call_llm(False)
            return

        event.should_call_llm(False)
        item = self.items.get(item_id)
        if not item:
            if self._get_bool("reply_when_not_found", False):
                yield event.plain_result(f"未收录物品ID：{item_id}").stop_event()
            return

        if self._get_bool("only_cars_and_skins", True) and item.get("type") not in {
            "EAIT_CAR",
            "EAIT_SKIN",
        }:
            return

        image_url = self._image_url(item_id)
        image_exists = await asyncio.to_thread(self._url_exists, image_url)
        if not image_exists and self._get_bool("silent_on_image_404", True):
            return

        title = item.get("name") or f"物品 {item_id}"
        mess = item.get("mess") or item.get("type") or "未知类型"
        lines = [title, f"ID: {item_id}", f"类型: {mess}"]
        if not image_exists:
            lines.append("图片: 未找到")

        chain = [Comp.Plain("\n".join(lines))]
        if image_exists:
            chain.append(Comp.Image.fromURL(image_url))
        yield event.chain_result(chain).stop_event()

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

    def _group_allowed(self, group_id: str) -> bool:
        allowed = self._get_list("allowed_group_ids")
        if not allowed:
            return True
        return group_id in {str(value).strip() for value in allowed if str(value).strip()}

    def _valid_id_length(self, item_id: str) -> bool:
        min_digits = max(1, self._get_int("id_min_digits", 5))
        max_digits = max(min_digits, self._get_int("id_max_digits", 6))
        return min_digits <= len(item_id) <= max_digits

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
