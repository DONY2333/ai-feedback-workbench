# Evaluation Prompt Snapshot

- Prompt Version：`v1-baseline`
- Model：从环境变量 `OPENAI_MODEL` 读取；本快照不固定模型名称。
- Evaluation Sample：`L001-L020`
- Source：`src/model_client.py`（枚举定义来源：`src/schemas.py`）

## System Prompt

```text
你是资深产品用户反馈分析师。请只依据评论原文提取信息，不要臆测。
输出一个 JSON 对象，字段必须为：是否为有效产品反馈（布尔值）、反馈类型、产品模块、用户场景、核心痛点、情绪倾向、严重程度、原始评论证据。
反馈类型只能是：Bug、功能建议、使用困难、正向评价、情绪或无效反馈。
情绪倾向只能是：正向、中性、负向。严重程度只能是：低、中、高、严重。
原始评论证据必须直接摘自原评论，尽量简短。
```

## User Prompt 模板

以下为当前调用中使用的 Python f-string 原文，`\n` 表示换行，产品为空时使用“未知”：

```python
f"产品：{product or '未知'}\n评论原文：{comment}"
```

## JSON Schema

以下保存当前 `JSON_SCHEMA` 的完整结构，枚举已展开为具体值。

```json
{
  "name": "feedback_analysis",
  "strict": true,
  "schema": {
    "type": "object",
    "properties": {
      "是否为有效产品反馈": {
        "type": "boolean"
      },
      "反馈类型": {
        "type": "string",
        "enum": ["Bug", "功能建议", "使用困难", "正向评价", "情绪或无效反馈"]
      },
      "产品模块": {
        "type": "string"
      },
      "用户场景": {
        "type": "string"
      },
      "核心痛点": {
        "type": "string"
      },
      "情绪倾向": {
        "type": "string",
        "enum": ["正向", "中性", "负向"]
      },
      "严重程度": {
        "type": "string",
        "enum": ["低", "中", "高", "严重"]
      },
      "原始评论证据": {
        "type": "string"
      }
    },
    "required": [
      "是否为有效产品反馈",
      "反馈类型",
      "产品模块",
      "用户场景",
      "核心痛点",
      "情绪倾向",
      "严重程度",
      "原始评论证据"
    ],
    "additionalProperties": false
  }
}
```
