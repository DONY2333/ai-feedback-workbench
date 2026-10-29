# Langfuse C2 r3 正式 Dev Experiment 报告

## 执行与服务端核验

- Dataset：`feedback-dev-20`；每版严格 L001–L020，共 20 条。
- 模型：`gpt-6.1-sol`；timeout=45 秒；OpenAI SDK `max_retries=0`，禁止 Mock。
- 系统性错误（401/403、配置、代码、解析/Schema 等）立即停止整个执行。
- timeout、429、5xx 最多显式重试一次；两次失败保留该条失败结果并继续后续条目。
- 每条最多 2 次真实请求，全局最多 120 次；每次发送前持久化 attempt 与总请求计数。
- 本轮实际模型请求 **60 次**；成功 60 次；重试 **0 次**（无对应 Gold ID）；失败 **0 条**（无失败原因清单）。
- 本地回执最终状态 `server_verified`。三组服务端 Item、input、Gold、输出、Generation、版本信息、三层输出和全部 Scores 均回读核验一致。
- 每组恰有 20 个不同 Dataset Item、20 个成功逻辑结果、20 个正式 Generation、98 个 Score；合计 60 个 Generation、294 个 Score。
- Gold 严重程度不适用的 L001、L013 不写 severity Score；核心痛点保存但不作字符串评分。

| Experiment | 服务端 ID | 成功/失败 |
|---|---|---:|
| feedback-dev-v1-final-r3 | f43d00fd-40fb-4ff3-a073-d4b070a24ebf | 20/0 |
| feedback-dev-v2-final-r3 | 9aa95e03-a634-477e-8541-b622168810d9 | 20/0 |
| feedback-dev-v3-final-r3 | a4d76bf9-20d6-4f77-b8e4-af17bd335611 | 20/0 |

## Accuracy

字段比较继续复用 C1 历史 Exact Match，不对核心痛点评分。若未来有失败 Item，字段 accuracy 只计成功预测的适用样本，同时必须报告失败与覆盖率；本轮全部成功，无缺失预测。

| 版本 | Valid | Feedback Type | Product Module | Severity | All-fields |
|---|---:|---:|---:|---:|---:|
| V1 | 19/20 = 95% | 17/20 = 85% | 0/20 = 0% | 12/18 = 66.67% | 0/20 = 0% |
| V2 | 19/20 = 95% | 17/20 = 85% | 17/20 = 85% | 12/18 = 66.67% | 12/20 = 60% |
| V3 | 20/20 = 100% | 19/20 = 95% | 18/20 = 90% | 13/18 = 72.22% | 14/20 = 70% |

V1 模块保持自由文本，不映射为“其他”或现行 19 类 taxonomy；0/20 为真实 Exact Match 结果，不是归一化污染后的成绩。
所有记录独立保存 raw_output、normalized_output、score_input，并记录 prompt_version、normalization_version、scoring_version、normalization_reproduction、attempt_count、attempts、retry_reason、final_status。

## Latency / token usage

Latency 为模型 API 往返，不包含 Langfuse 上传和核验。Item latency 累加该条所有 attempt；本轮没有 retry，因此 Item 与单次 attempt 均值相同。
Token 来自模型 API 自报，校验为非负整数且 input + output = total；覆盖每版 20/20、本轮 60/60 请求。

| 版本 | 平均 latency（秒） | Input tokens | Output tokens | Total tokens |
|---|---:|---:|---:|---:|
| V1 | 9.725 | 19,067 | 3,706 | 22,773 |
| V2 | 7.805 | 48,787 | 3,237 | 52,024 |
| V3 | 8.793 | 57,567 | 3,374 | 60,941 |
| 总计 / 总平均 | 8.774 | 125,421 | 10,317 | 135,738 |

本轮没有失败或超时请求的未知用量。API 自报 token 不等同于独立核验的费用账单。

## 与历史本地 Evaluation 对比

历史逐字段 match 与 summary 在实际调用前已离线回放一致；历史 all-fields 使用同一比较规则从保存预测计算，没有重新请求模型。

| 历史版本 | Valid | Feedback Type | Product Module | Severity | All-fields |
|---|---:|---:|---:|---:|---:|
| V1 baseline | 19/20 | 18/20 | 1/20 | 12/18 | 1/20 |
| V2 | 19/20 | 16/20 | 18/20 | 12/18 | 12/20 |
| V3 | 20/20 | 19/20 | 18/20 | 14/18 | 15/20 |

r3 相对各自历史结果的变化（百分点）：

| 版本 | Valid | Feedback Type | Product Module | Severity | All-fields |
|---|---:|---:|---:|---:|---:|
| V1 | 0 | -5 | -5 | 0 | -5 |
| V2 | 0 | +5 | -5 | 0 | 0 |
| V3 | 0 | 0 | 0 | -5.56 | -5 |

这些是重新调用的差异，不意味着执行稳定性策略改变了评分规则。
V1 自由文本模块与 V2/V3 固定 taxonomy 的模块准确率不能直接解读为纯模型能力提升。
三版均明确 `normalization_reproduction=partial`：缺少完整历史源码及原始响应，回放一致只证明存档可观察评分一致，不证明所有旧异常分支完全复原。

## 文件和测试

- 修改 `evaluation/langfuse_dev_experiment.py`：错误分类、45 秒 timeout、最多一次显式 retry、逐次计数、失败继续、三层输出与状态记录、带失败覆盖的汇总及服务端核验。
- 更新 `tests/test_langfuse_dev_experiment.py`：401/403/400 停止；timeout、429、500、503 一次失败后成功或两次失败后继续；无第三次请求；版本隔离、历史回放和回读关联继续验证。
- 更新 `README.md`：r3 执行策略和只读核验命令。
- 新增本目录 `receipt.json` 与本报告，旧运行文件不覆盖。
- 相关离线测试 **93 passed**；全量 **203 passed，1 个既有 pandas FutureWarning**。本轮无 SciPy/sklearn DLL 环境失败；测试没有连接真实模型。

Prompt 历史快照、Gold Set、taxonomy、历史 Evaluation 结果、旧 C0、r2 本地与服务端记录均未修改。
本轮完成后停止；未调整 V3、未创建 V4、未运行 Holdout。
