# QQ飞车物品图鉴查询

AstrBot 插件：群内玩家直接发送 QQ 飞车物品 ID 时，机器人回复物品名称、类型和 `https://iips.speed.qq.com/images/<ID>.png` 对应图片。

## 功能

- 群消息纯数字触发，不需要 `/命令`。
- 支持限制允许群。
- 支持配置触发 ID 位数。
- 支持只查询赛车 `EAIT_CAR` 和赛车皮肤 `EAIT_SKIN`。
- 支持图片 404 时静默不回复，减少群里刷屏。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `allowed_group_ids` | `[]` | 允许查询的群号列表，留空表示全部群可用 |
| `id_min_digits` | `5` | 触发查询的最小 ID 位数 |
| `id_max_digits` | `6` | 触发查询的最大 ID 位数 |
| `only_cars_and_skins` | `true` | 只回复赛车和赛车皮肤 |
| `silent_on_image_404` | `true` | 图片不存在时静默 |
| `reply_when_not_found` | `false` | 本地名称表未收录时是否回复提示 |
| `image_base_url` | `https://iips.speed.qq.com/images` | 图片基础地址 |
| `image_timeout_sec` | `6` | 图片 HEAD 检查超时秒数 |

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

群里发送：

```text
74362
```

机器人回复：

```text
爆天甲
ID: 74362
类型: S级赛车
```

并附带 `https://iips.speed.qq.com/images/74362.png`。
