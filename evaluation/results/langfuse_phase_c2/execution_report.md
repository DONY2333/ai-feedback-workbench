# Phase C2 执行中止记录

日期：2026-10-23。计划 60 次真实请求，实际发出 **1 次**；未自动重试、未补跑、未使用 Mock。

## 失败原因

首条 `v1 / L001` 的模型 API 返回 HTTP **401 AuthenticationError**。
API 往返耗时约 1.311 秒。没有返回可解析预测，没有可靠 token usage，没有写入任何正式 Score。
因此没有解析失败样本，也没有可计算的本次准确率或成功调用平均 latency。
401 只能确认鉴权被拒，不能据此断定是 Key 失效、权限不足或中转站配置问题。
没有输出或保存 API Key。

## Langfuse 只读回查

| Experiment | 服务端 Item 数 | 有效预测数 | 实际模型请求数 |
|---|---:|---:|---:|
| feedback-dev-v1-final | 20 | 0 | 1 |
| feedback-dev-v2-final | 未创建 | 0 | 0 |
| feedback-dev-v3-final | 未创建 | 0 | 0 |

V1 服务端 ID：`049ef9a3-dc2a-4155-80a1-46a9d179ae78`。
SDK 返回的内部 ID：`71ec17ce3e2df9d0`。二者不能混作服务端查询 ID。
20 个 Item 的 output 均为错误文本：1 条首条失败、19 条首错闩锁拒绝执行。
L001 Generation `8c22413c893a9257` 可回读，level 为 ERROR，无模型输出；关联 Score 数为 0。
Trace：`b906cd16c88036ad9ba63d09e11030dc`。

## 与历史结果的关系

三版历史回放在真实请求前全部通过，逐字段 match 与 summary 一致：

| 历史版本 | valid | type | module | severity |
|---|---:|---:|---:|---:|
| V1 | 19/20 | 18/20 | 1/20 | 12/18 |
| V2 | 19/20 | 16/20 | 18/20 | 12/18 |
| V3 | 20/20 | 19/20 | 18/20 | 14/18 |

这些仅为存档历史结果，不是本次正式 Experiment 成绩。本次无有效预测，无法计算差异。
没有更改旧 C0、Prompt、Gold、生产 normalization 或本地历史结果。

## 后续需要用户确认

检查项目 `.env` / 系统环境中模型 API 的 Key、base URL 与权限；系统环境优先。
`--langfuse-env` 只导入 Langfuse 凭据，不会替换正式模型配置。
当前回执与同名失败 Experiment 均保留，不应直接重跑或删除。
恢复执行须另行确认同名失败 Experiment 的处理方式和剩余请求预算，避免超过本轮 60 次上限。
