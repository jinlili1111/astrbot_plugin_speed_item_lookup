# QQ飞车物品图鉴查询

AstrBot 插件：玩家发送 `/物品ID`、`/名称`，或 `/itemid 物品ID`、`/itemid 名称` 时，机器人回复 QQ 飞车物品名称、类型和 `https://iips.speed.qq.com/images/<ID>.png` 对应图片。

## 功能

- 使用 `/物品ID`、`/名称` 或 `/itemid` 指令触发，不需要 @ 机器人。
- 支持物品 ID 精确查询，所有类型都会回复，不再限制赛车/皮肤。
- 支持名称模糊搜索。
- 多个匹配时返回候选列表，并支持第二轮 `/序号` 或 `/itemid 序号` 筛选。
- 支持限制允许群。
- 支持图片缺失时仍回复文字信息。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `allowed_group_ids` | `[]` | 允许查询的群号列表，留空表示全部群可用 |
| `silent_on_image_404` | `false` | 物品 ID 未收录且图片不存在时静默 |
| `reply_when_not_found` | `true` | 本地名称表未收录时是否回复提示 |
| `image_base_url` | `https://iips.speed.qq.com/images` | 图片基础地址 |
| `image_timeout_sec` | `6` | 图片 HEAD 检查超时秒数 |
| `max_search_results` | `10` | 名称模糊搜索最多展示多少条候选 |
| `selection_ttl_sec` | `300` | 二轮序号筛选有效期秒数 |

## 数据

`data/item_names.json` 由 QQSpeed `Config/Item.yml` 抽取生成，保留字段：

```json
{
  "74362": {
    "name": "爆天甲",
    "type": "EAIT_CAR",
    "mess": "S级赛车"
  }
}
```

## 本地验证

```bash
python -m py_compile main.py
python -m json.tool data/item_names.json > NUL
```

## 使用示例

精确查询：

```text
/74362
```

机器人回复：

```text
爆天甲
ID: 74362
类型: S级赛车
```

并附带 `https://iips.speed.qq.com/images/74362.png`。

名称模糊搜索：

```text
/爆天
```

如果有多个匹配，机器人会回复：

```text
找到 30 个匹配「爆天」的物品，显示前 10 个：
1. 爆天甲 / ID: 74362 / S级赛车
2. 爆天-曜影 / ID: 120246 / 赛车皮肤
...
继续发送 /序号 查看，例如：/1；也可用 /itemid 1
```
