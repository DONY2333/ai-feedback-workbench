# AI Feedback Workbench 实验总结

本项目将用户评论转为结构化产品反馈，以人工 Gold、版本化 Prompt、离线评价和 Langfuse Experiment 建立可追溯的验证链。本文用于项目 README、案例复盘和面试展示，区分三个证据系列：历史本地 Dev、冻结 V3 后的本地 Holdout，以及 Langfuse r3 的 Dev 重新生成。

Phase D 仅整理已有文件，模型 API 请求数为 0；没有运行新的 Evaluation、查询或修改 Langfuse，也没有修改任何历史预测、Prompt、Gold 或 taxonomy。

## 1. Evaluation Setup

| 项目 | 设置 |
| --- | --- |
| Model | `gpt-6.1-sol`；各历史明细的模型列及 r3 回执均有记录 |
| Gold Set | 82 条人工标注：L001–L043、W001–W039 |
| Dev | L001–L020，共 20 条，用于 Prompt iteration |
| Holdout | L021–L043 + W001–W039，共 62 条；V3 冻结后首次正式测试 |
| 自动评价字段 | 是否为有效产品反馈、反馈类型、产品模块、严重程度 |
| 自由文本 | 核心痛点保留人工标签和预测，不计算字符串 Exact Match |
| 严重程度适用性 | Gold=`不适用` 时跳过，不作为正确或错误样本 |
| All-fields | 有效性、反馈类型、模块和所有适用的严重程度全部正确才计 1 |

正式反馈类型为 Bug / 功能建议 / 使用困难 / 正向评价 / 情绪/无效反馈；正式严重程度为高 / 中 / 低 / 不适用。V2/V3 模块固定为 19 类。模型输出保持 8 字段 JSON；用户场景、情绪倾向、证据质量暂不纳入上述分类成绩。

四份历史明细 ID 均与相应 split 一致、无重复；分析来源均为 api，错误信息为空。Dev 严重程度适用 18 条（高 10、中 7、低 1），Holdout 适用 44 条（高 31、中 13、低 0）。样本按 ID 选取，不依赖 Excel 行顺序。

## 2. Evidence Index

| 证据 | 原始文件 / 作用 |
| --- | --- |
| Gold | [gold_set_82.xlsx](../gold_set_82.xlsx)，82 条人工标签与原始 ID |
| Prompt V1 | [prompt_v1_baseline.md](../prompts/prompt_v1_baseline.md)，历史 System、User 模板、Schema |
| Prompt V2 | [prompt_v2.md](../prompts/prompt_v2.md)，正式 taxonomy 与边界定义 |
| Prompt V3 | [prompt_v3.md](../prompts/prompt_v3.md)，小修后的冻结版本 |
| 历史 Dev V1 | [明细](v1_baseline/evaluation_detail.xlsx)、[summary](v1_baseline/evaluation_summary.csv) |
| 历史 Dev V2 | [明细](v2/evaluation_detail.xlsx)、[summary](v2/evaluation_summary.csv) |
| 历史 Dev V3 | [明细](v3/evaluation_detail.xlsx)、[summary](v3/evaluation_summary.csv) |
| V3 Holdout | [明细](v3_holdout/evaluation_detail.xlsx)、[summary](v3_holdout/evaluation_summary.csv)、[既有完整报告](v3_holdout/final_evaluation_report.md) |
| Holdout 分类指标 | [metrics_overall.csv](v3_holdout/analysis/metrics_overall.csv)，各字段 Accuracy、Macro-F1、Weighted-F1 |
| Holdout 类别覆盖 | [模块](v3_holdout/analysis/metrics_product_module.csv)、[严重程度](v3_holdout/analysis/metrics_severity.csv)，Support 与未覆盖标记 |
| Holdout 逐条 / 错误方向 | [all_fields_correct.csv](v3_holdout/analysis/all_fields_correct.csv)、[error_patterns.csv](v3_holdout/analysis/error_patterns.csv) |
| C1 复现记录 | [replay_report.json](langfuse_phase_c1/replay_report.json)，历史 match / summary 回放与局限 |
| Langfuse r3 | [receipt.json](langfuse_phase_c2/r3/receipt.json)、[执行报告](langfuse_phase_c2/r3/execution_report.md)，真实输出、版本、Scores、latency、usage 与先前服务端核验记录 |
| 文件完整性 | [experiment_evidence_manifest.json](experiment_evidence_manifest.json)，冻结输入证据的 SHA-256，不包含凭据 |

本阶段的 Langfuse 结论引用已有 `server_verified` 回执，不是 Phase D 新进行的在线核验。旧 C0 与失败 C2 / r2 保留作技术验证及失败排障记录，不混入正式 r3 成绩。

## 3. 历史本地 Dev Results

以下引用各版本保存的本地明细和 summary；不是 Langfuse r3 的输出。

| 版本 | Valid | Feedback Type | Product Module | Severity |
| --- | ---: | ---: | ---: | ---: |
| V1 | 19/20 = 95.00% | 18/20 = 90.00% | 1/20 = 5.00% | 12/18 = 66.67% |
| V2 | 19/20 = 95.00% | 16/20 = 80.00% | 18/20 = 90.00% | 12/18 = 66.67% |
| V3 | 20/20 = 100.00% | 19/20 = 95.00% | 18/20 = 90.00% | 14/18 = 77.78% |

V1 最大问题之一是输出空间与 Gold 不统一：模块允许自由文本，反馈类型使用旧的“情绪或无效反馈”拼写，严重程度枚举为“低 / 中 / 高 / 严重”。V2 才统一为正式 taxonomy。V1 模块 5% 与 V2/V3 的 90% 不能直接解读为纯模型能力提升，严重程度也存在版本定义差异。

## 4. Prompt Iteration：V1 → V2 → V3

### V1 → V2：统一输出契约

- 将产品模块从自由文本收敛为 Gold 的 19 个固定标签，禁止生成新模块。
- 对齐正式 5 类反馈类型，将旧无效反馈标签改为“情绪/无效反馈”。
- 严重程度统一为高 / 中 / 低 / 不适用；正向评价和情绪/无效反馈必须为不适用。
- 明确 Bug、使用困难、功能建议、正向评价和无效反馈的基础定义。
- 增加有效性及严重程度的判断标准和例子；保持 JSON 的 8 个字段及 User 模板。

V2 主要解决模块结构化和评价可比较性，并非所有字段都同步提升：历史反馈类型从 90% 降至 80%，严重程度未变。

### V2 → V3：针对边界错误做小修

- 强化 Bug vs 使用困难：完全失效或异常优先判 Bug；功能可用但难找、难操作或体验较差优先判使用困难。
- 增加“后文最新修正优先”：已明确撤销或排除的早期问题不继续作为 Bug 依据。
- 短文本、语法不完整、混合语言只要能定位具体功能或问题，仍可判有效。
- 仅补充易混淆的模块映射：网络/运行异常、消息管理、通知提醒、账号验证等；没有扩充 taxonomy。
- 严重程度基于实际任务影响，不因情绪或“用户想要”自动升级；非核心增强通常为低。

V3 没有为 L001–L020 写死答案，也没有改变 JSON 字段、User 模板或正式 taxonomy。

## 5. 三轮 Error Analysis

| 阶段 | 复盘发现 | 对应处理 |
| --- | --- | --- |
| Error Analysis 1：V1 后 | taxonomy mismatch；Prompt category boundary unclear；validity criteria unclear；severity threshold unclear | V2 固定输出空间，补基础边界和严重程度标准 |
| Error Analysis 2：V2 后 | Bug definition too broad；latest-user-correction rule missing；短/混合语言评论过易判无效；module mapping examples 不足 | V3 只补对应规则，不重写整体 Prompt |
| Error Analysis 3：V3 Holdout | 高→中 11；使用困难→Bug 5；安装/更新→性能/稳定性 3 | 仅形成分析结论，未用于创建 V4 |

前两轮是结合历史输出和 Prompt 差异形成的定性复盘，不是经过消融实验确认的因果归因。第三轮次数可从已有 `error_patterns.csv` 逐项核对，按字段统计；同一评论可以贡献多个字段错误，不能直接相加当作独立错误评论数。

## 6. V3 Holdout：正式泛化结果

本节是已有冻结 V3 的本地 Holdout 运行，不是 r3 Experiment。指标直接引用已保存的离线分析文件，本阶段没有重算或覆盖这些结果。

| 字段 | 正确/适用样本 | Accuracy | Macro-F1 | Weighted-F1 |
| --- | ---: | ---: | ---: | ---: |
| Valid | 61/62 | 98.39% | 97.49% | 98.36% |
| Feedback Type | 54/62 | 87.10% | 85.68% | 86.45% |
| Product Module | 50/62 | 80.65% | 55.39% | 81.35% |
| Severity | 31/44 | 70.45% | 47.10% | 73.83% |

All-fields correct：37/62 = 59.68%。单字段较高准确率不代表整条结构化反馈可以无需复核直接使用。

Macro-F1 沿用既有计算口径：有效性 2 类、反馈类型 5 类、模块 19 类、严重程度 3 类等权平均，零分母按 0 处理；Weighted-F1 按 Gold Support 加权。Support=0 的单类 P/R/F1 在明细中留空标为未覆盖，不将其解释为已测试的分类能力。

Holdout 模块只覆盖 19 类中的 14 类。55.39% Macro-F1 既暴露类别间差异、长尾薄弱，也受到 5 个未覆盖类别进入固定分母的影响，不能全部归因为已覆盖长尾类的错分。

严重程度的“低”support=0；其两条预测实际来自 Gold=中。44 条适用样本为高 31、中 13，若始终预测高，Accuracy 同样为 31/44=70.45%。因此不能只依据 Accuracy 判断严重程度能力，更不能宣称已验证低严重程度识别效果。

## 7. Langfuse r3 Dev Experiment

Dataset：`feedback-dev-20`。每版重新对 L001–L020 调用模型，分别读取真实历史 Prompt / Schema 和 C1 版本化 normalization / scoring。不是上传历史预测或继续旧失败运行。

| Experiment | 成功 | 失败 | 先前已回读核验的服务端 ID |
| --- | ---: | ---: | --- |
| feedback-dev-v1-final-r3 | 20 | 0 | f43d00fd-40fb-4ff3-a073-d4b070a24ebf |
| feedback-dev-v2-final-r3 | 20 | 0 | 9aa95e03-a634-477e-8541-b622168810d9 |
| feedback-dev-v3-final-r3 | 20 | 0 | a4d76bf9-20d6-4f77-b8e4-af17bd335611 |

总真实模型请求：60；retry：0；失败样本：无。每版有 20 个不同 Dataset Item、20 个 Generation、98 个正式 Scores，合计 294 个 Scores。模型 timeout=45 秒、SDK max_retries=0；执行器仅对临时错误允许一次显式重试，本轮没有触发。

| 版本 | Valid | Feedback Type | Product Module | Severity | All-fields |
| --- | ---: | ---: | ---: | ---: | ---: |
| V1 | 19/20 = 95.00% | 17/20 = 85.00% | 0/20 = 0.00% | 12/18 = 66.67% | 0/20 = 0.00% |
| V2 | 19/20 = 95.00% | 17/20 = 85.00% | 17/20 = 85.00% | 12/18 = 66.67% | 12/20 = 60.00% |
| V3 | 20/20 = 100.00% | 19/20 = 95.00% | 18/20 = 90.00% | 13/18 = 72.22% | 14/20 = 70.00% |

每条独立保存 `raw_output`、`normalized_output`、`score_input`；只有 score_input 参与正式 Score。V1 自由文本模块不强制映射为“其他”，0/20 是保留历史自由文本口径后的真实匹配结果。C0 曾发生的 V1 标准化污染不计入正式 r3。

## 8. Langfuse r3 Latency / Token Usage

| 版本 | Avg API latency | Input tokens | Output tokens | Total tokens |
| --- | ---: | ---: | ---: | ---: |
| V1 | 9.725s | 19,067 | 3,706 | 22,773 |
| V2 | 7.805s | 48,787 | 3,237 | 52,024 |
| V3 | 8.793s | 57,567 | 3,374 | 60,941 |

总 token：135,738；总体平均 API latency：8.774s。

Token 直接来自本轮 60/60 响应的 usage，校验 input + output = total；不是按文本长度估算。Latency 是模型 API 往返，不含 Langfuse 上传与回读；本轮没有 retry，因此 Item 与 attempt 平均耗时相同。

历史本地明细没有同等完整的 latency / usage 证据，不补造历史性能数据。项目中未找到可信、固定的实际计费价格来源，本报告不计算货币成本。

## 9. 历史本地 Dev 与 r3 必须分开解释

同一 Dev 集与版本，并不代表同一次生成。r3 是重新调用 LLM；即使参数相同，生成结果也不保证完全确定。小幅差异可能来自重新生成的非完全确定性或服务端变化，不能仅凭本次差值认定 Prompt 或后处理发生变化。

相对各自历史本地结果，r3 的变化为：V1 反馈类型、模块各下降 5 个百分点；V2 反馈类型上升 5 个百分点、模块下降 5 个百分点；V3 严重程度下降 5.56 个百分点。其余上述四个字段不变。

已有 r3 执行报告另记录历史 All-fields 为 V1 1/20、V2 12/20、V3 15/20；对应 r3 为 0/20、12/20、14/20，分别变化 -5、0、-5 个百分点。它们不是新跑的 Evaluation。

不能将 r3 Dev 与 62 条本地 Holdout 拼成一个“V3 总体准确率”，也不能用 Dev 迭代增益单独证明泛化提升。Dev 全部是 L 系列，Holdout 包含 W 系列，数据分布亦不同。

## 10. Limitations

- 历史 normalization 原始源码和完整原始响应未完整保存；V1/V2/V3 均为 `normalization_reproduction=partial`，即 partial reproduction。C1 存档回放一致，不等于恢复了所有历史异常分支。
- Langfuse r3 是重新生成，不是导入历史输出；一次重跑不能量化生成波动的分布。
- Dev 只有 20 条，Gold 总计 82 条；没有统计显著性或置信区间结论。
- Holdout 已被查看和分析，未来不能继续称为 pristine unseen test set；若据此调 Prompt，应另采独立未见测试集。本轮没有创建 V4。
- 严重程度类别不均衡，Holdout 的“低”support=0；模块有 5 类未覆盖，多类仅 1–2 条，不能仅看总体 Accuracy 或把未覆盖类的零值当作实测能力。
- 尚未测量标注者间一致性，也没有对全部模糊案例进行独立裁决。
- 核心痛点尚未建立正式自由文本 Evaluation；用户场景、证据质量和情绪倾向也未得到本轮分类指标验证。
- 尚未进行可信货币成本比较；API 自报 usage 不等于经过独立核对的账单。
- Phase D 仅检查已保存的服务端核验回执，没有连接 Langfuse，不能据此保证当前页面状态未变。

## 11. 最终结论与案例表述

1. V1 的主要工程问题是 taxonomy / 输出空间不统一，直接 Exact Match 的模块结果很差；应先统一任务契约，再比较模型效果。
2. V2 主要解决模块结构化：历史本地模块从 1/20 到 18/20，但包含标签空间变化，不能包装成纯模型准确率提升 85 个百分点。
3. V3 在历史 Dev 和 r3 Experiment 中整体表现最好，尤其有效性、类型和 All-fields；并非每个指标都严格领先所有版本，例如历史 V1 类型 90% 高于 V2 的 80%。
4. 冻结 V3 的 Holdout 在本批数据上对有效性（98.39%）和反馈类型（87.10%）泛化较好；仍不应外推为任意来源评论的表现保证。
5. 模块总体 Accuracy 为 80.65%，但 Macro-F1 为 55.39%，提示类别覆盖和长尾能力问题，需要同时看 Support 与单类 Recall。
6. 严重程度仍是主要弱项：高→中 11 条，Accuracy 与多数类基线相同，且低类完全未覆盖，适合保留人工复核。
7. Prompt 迭代改善了本项目的结构化表现，同时 Prompt 长度与输入 token 同步增加。r3 输入 token 为 19,067 → 48,787 → 57,567；总 token 为 22,773 → 52,024 → 60,941。质量、长度与资源使用需要共同考量；没有证明每条新增规则都具有独立因果收益。

面试中可概括为：先用人工 Gold 暴露输出空间和边界问题，迭代并冻结 V3，再用独立 Holdout 检验泛化；随后把三版真实 Prompt、版本化后处理、Gold Scores 和用量统一记录到 Langfuse。保留不利结果、复现局限和已见测试集边界，不把 Dev 提升、重新生成或 taxonomy 对齐混称为模型能力提升。

## 12. 离线复核与交付边界

本报告所有定量表格均可从 Evidence Index 的现有 CSV / Excel / JSON 核对；Error Analysis 1/2 是定性复盘。没有发现需求数字与原始文件冲突。重要差别是实验批次、标签空间、严重程度分母、固定类别 Macro-F1 和 Support=0 约定，而不是数值抄录错误。

证据清单保存 21 个输入文件的 SHA-256。新增文档测试只读取清单、现有 CSV / JSON 和本报告，检查原文件未变、表格数字、r3 调用与 Score 计数、链接和关键口径，不创建客户端、不重跑分类分析、不写历史结果：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_experiment_summary.py
```

它验证的是固定结果与文档的可复核性，不承诺重新调用 LLM 能得到相同预测。完整预测重新生成会产生真实请求，不属于 Phase D，也不应为验证文档而执行。

Phase D 验证结果：新增证据/文档测试 9 passed；全量本地测试 212 passed，1 条既有 pandas FutureWarning，无 SciPy/sklearn DLL 环境失败。新增测试不依赖模型或 Langfuse SDK，也不要求加载任何 API Key。

仓库 `.gitignore` 默认忽略结果 CSV / Excel；只克隆代码或复制 README 不等于已获得全部复现证据。请在获准范围内保留清单所列原文件，随交付提供受控证据副本；不要擅自公开 Gold 中的用户名、原始评论或来源信息。缺少证据时复核应报错，不应伪造通过。清单不包含 `.env`、API Key 或 Langfuse Secret Key。本阶段未更改 `.gitignore` 或复制原始数据。
