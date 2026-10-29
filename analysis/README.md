# Phase E1：离线候选 Issue

此流程只读取历史 V3 Dev 与 Holdout 预测，不调用 API，不读取 Gold 标签纠正预测，不改变单条 taxonomy。它不是已验证的产品事实，所有自动 Issue 都需要人工复核。Holdout 在此作为产品分析输入，不进行新的模型评测。

## 运行

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe analysis\run_issue_clustering.py
```

默认读取 `evaluation/results/v3/evaluation_detail.xlsx` 和 `evaluation/results/v3_holdout/evaluation_detail.xlsx`。82 条中 63 条预测为有效 Bug / 使用困难 / 功能建议，按产品与预测模块分成 24 组。不混入 Langfuse r3 的重新生成结果。

输出到独立目录 `analysis/results/e1_offline/`，已存在时拒绝覆盖，可用 `--output` 指定新目录：

- `issue_clusters.csv`：Issue 明细。
- `feedback_mapping.csv`：全部反馈（含排除原因）、原始评论和主 Issue 映射。
- `summary.csv`：样本数、分组数、Issue 数、复核状态等。
- `issue_clusters.json`：完整结构、原文与来源的权威机器可读记录。
- `review.json`：独立人工复核输入。

CSV 的列表/分布字段使用 JSON 字符串。潜在 Excel 公式文本加单引号防护；完整 JSON 原文不变。输出不含优先级、解决方案、商业价值或货币成本。

## 方法与限制

先以 `product + product_module` 隔离，功能建议另开归并通道。组内少量模块约束的对象/症状锚点合并通知送达、通知音量、验证码、附加文件、语言切换等同类候选；未命中时仅合并足够长的完全相同痛点，否则保留单条。不使用宽泛关键词或传递式相似度串联，避免“收不到验证码”混入通知。优先分析已保存痛点而非重新提取原文，避免重新引入已撤销诉求。

锚点只能生成候选，不是完整语义聚类：可能漏合并，也可能把不同原因的相同症状合并。每条只能有一个主 Issue，多问题评论暂不拆分为多标签。产品模块沿用原预测，即使预测可能有错也不自动纠正。标题未命中锚点时引用具体痛点，可能冗长，需人工编辑；不会凭常识补原因或解决方案。

历史结果未保存用户场景，因此 `main_user_scenarios=[]`、`scenario_source=not_saved`，不补造。代表评论取按 ID 排序的前 1–3 条；完整证据可按 `evidence_ids` 在 mapping 中查询。

## 人工复核

编辑 `review.json` 的 `issue_title`、`evidence_ids`、`review_note` 和 `review_status`（`needs_review` / `reviewed`）：

- 改标题：修改 `issue_title`。
- 合并：把成员移到同一个 Issue 并删除空 Issue。
- 拆分：新建唯一 `issue_id`，把部分成员移过去。
- 移动：从旧 Issue 移除 ID，再加入目标 Issue。
- 查看证据：在完整 JSON / mapping 中按 ID 查看原文及来源行号。

```powershell
.\.venv\Scripts\python.exe analysis\run_issue_clustering.py --review analysis\results\e1_offline\review.json --output analysis\results\e1_reviewed
```

所有符合准入条件的反馈必须正好覆盖一次；重复、遗漏、未知 ID、跨产品或跨模块、建议混入故障均拒绝。计数、分布和代表原文重新从源数据计算，不信任手改统计。该简化复核入口不支持跨模块合并或修改单条预测。

## 可选后续 LLM 方案（本轮不实现、不调用）

离线候选不需要 LLM。若后续授权更强语义归并，可只处理 13 个多成员粗分组（13 次初始请求），11 个单成员组直接保留；若所有 24 组都需生成语义标题，则为 24 次初始请求。请求按产品/模块隔离、结构化 JSON 输出，并沿用成员覆盖校验。上述数字不含重试；本轮实际模型调用为 0。

## Phase E2：产品洞察与 Top 5 建议

```powershell
.\.venv\Scripts\python.exe analysis\build_product_insights.py
```

只读上述 E1 快照，校验五份输入后输出到 `analysis/results/e2_product_insights/`：产品报告、Top 5 CSV、产品模块对比 CSV、原文证据 CSV、独立复核审计 JSON。不重跑归并或模型，不改变 E1 review。报告包含本轮固定编辑决策、证据依据及方案建议，并不代表自动或最终产品签核。

输出目录已存在则拒绝覆盖。复现可用 `--output analysis/results/e2_reproduced` 指定新目录；输入变化时应重新核对编辑决策，不应把该脚本当成泛化的自动产品分析模型。为快速收尾跳过可选 XLSX。
