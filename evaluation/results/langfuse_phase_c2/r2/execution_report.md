# C2 r2：部分完成后超时中止

本轮模型为 `gpt-6.1-sol`。计划 60 次，实际请求 **14 次**，成功 13 次，失败 1 次。
V1 / L014 返回 `APITimeoutError`，耗时 20.010 秒；未获得 HTTP 状态码、模型 JSON 或 token usage。
没有重试、没有 Mock、没有补跑。L015–L020 和 V2/V3 未调用模型。

## 服务端核验

`feedback-dev-v1-final-r2` 服务端 ID：`821c93c0-eede-4a6d-82df-e1c3b5b53165`。
服务端存在 20 条 Item，但只有 **13 条成功预测 + 7 条错误 Item**，不能宣称完成 20 条评测。
逐一回读 L001–L013 的 input、expected_output、raw output、Generation、三层输出、
版本元数据和全部 Scores，均与本地回执一致。
L014 没有 Score；其余 6 条由首错安全闩锁拒绝，未调用模型。
服务端按名称回读确认 `feedback-dev-v2-final-r2`、`feedback-dev-v3-final-r2` 均不存在。

## 部分结果（仅 V1 L001–L013，不是完整 Dev 成绩）

| 指标 | 正确/适用样本 | 一致率 |
|---|---:|---:|
| valid_correct | 12/13 | 92.31% |
| feedback_type_correct | 11/13 | 84.62% |
| product_module_correct | 0/13 | 0% |
| severity_correct | 10/11 | 90.91% |
| all_fields_correct | 0/13 | 0% |

Gold 严重程度不适用的 L001、L013 未写 severity Score。
V1 产品模块保留自由文本 Exact Match，未映射为现行 taxonomy。
三版 normalization 的历史复现局限仍为 partial，没有变更 C1 策略。

## 耗时与 token

- 成功 13 次的平均 API 往返耗时：8.127 秒。
- 包括超时在内的 14 次请求平均耗时：8.976 秒。
- 成功响应可校验 token：input 12,593；output 2,435；total 15,028。
- 用量覆盖 13/13 成功响应、13/14 请求；失败请求是否消耗 token 不可确认，不能据此宣称全轮精确用量。
- 不将 Langfuse 上传或回读耗时计入模型 latency。

## 测试和保护

调用前相关离线测试 82 项通过；全量测试 192 项通过，1 个既有 pandas FutureWarning。
本轮没有 SciPy/sklearn DLL 失败。
仅新增轮次参数及其测试、文档与 r2 结果；Prompt、Schema、Gold、taxonomy、历史结果、旧失败 Experiment 均未修改。
旧回执仍在上一层目录，新回执独立保存在本目录。

未完成完整三版比较，不能将上述部分分数与历史 20 条结果直接比较。
保留当前失败 Experiment 和回执；不自动扩大 timeout、不自动重试或继续其他版本。
