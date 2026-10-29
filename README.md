# AI 用户反馈与产品需求分析工作台

一个面向产品经理和运营的本地分析工具：把用户评论转为结构化反馈，归并问题，并保留每个产品建议的原始评论证据。

项目使用 82 条飞书/Lark 与企业微信公开评论，完成了三版 Prompt 对比、人工标签评测、问题归并和 Top 5 需求分析。它是个人原型项目，尚未上线。

## 可以做什么

| 页面 | 功能 |
| --- | --- |
| Overview | 查看样本规模、主要问题与模型质量 |
| Feedback Analysis | 筛选历史反馈、追溯原文；导入 CSV/XLSX/XLS 进行 Mock 演示 |
| Evaluation Lab | 对比历史 Dev、Holdout 和 Langfuse 实验结果，查看错误样本 |
| Product Insights | 对比两产品的样本问题分布，查看 Issue、Top 5 与证据 |

新评论流程：**导入 → 结构化分析 → 人工确认 → 手动更新问题与洞察**。网页导入入口当前仅使用 Mock，上传不会触发模型请求。新批次独立保存在 `analysis/results/user_runs/<batch_id>/`，不会改动历史 82 条成果。

真实模型分析使用独立客户端，输出有效性、反馈类型、产品模块、用户场景、核心痛点、情绪、严重程度和原始证据八个 JSON 字段。模型调用与数据处理代码分开。

## 本地运行

Python、pandas、Streamlit；真实调用使用 OpenAI-compatible API，实验记录使用 Langfuse。

在项目根目录执行（Windows PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

默认无需 API Key，启动页面只读取本地结果。仓库包含脱敏输入和历史演示成果；Windows pyarrow DLL 加载失败时，表格使用 HTML 展示。

独立真实模型脚本需要按 `.env.example` 配置 `.env`，系统环境变量优先。请勿提交真实密钥。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 模型评测

模型为 `gpt-6.1-sol`。人工 Gold 共 82 条：Dev 为 L001–L020（20 条），用于迭代；Holdout 为 L021–L043 + W001–W039（62 条），在 V3 冻结后测试。

| V3 Holdout | Accuracy | Macro-F1 |
| --- | ---: | ---: |
| 有效性 | 98.39% | 97.49% |
| 反馈类型 | 87.10% | 85.68% |
| 产品模块 | 80.65% | 55.39% |
| 严重程度 | 70.45% | 47.10% |

所有适用字段均正确：**37/62 = 59.68%**。Gold 严重程度为“不适用”时跳过，该字段分母为 44。核心痛点保留对照，未做 Exact Match 评价。

V1 使用自由文本模块，V2 统一为 19 类，V3 补充 Bug/使用困难边界、后文修正优先和短文本有效性规则。V1 模块 5% 与 V2 的 90% 包含输出空间变化，不能全部归因于模型能力提升。

Langfuse 的三版 `feedback-dev-v*-final-r3` 实验各重新生成 20 条，共 60 次请求、294 个 Scores、0 失败。它们与历史本地 Dev 是不同次生成。具体结果、用量和回执见[实验总结](evaluation/results/experiment_summary.md)。

## 从反馈到需求

历史 V3 预测中有 63 条问题型反馈：飞书/Lark 31 条，企业微信 32 条。按产品和模块分组，再用对象/症状规则及相同痛点归并，得到 54 个候选 Issue，其中 4 个包含多条证据。此方法依赖规则，尚未实现通用语义聚类。

| Top | 问题 | 证据数 |
| --- | --- | ---: |
| 1 | 飞书：消息通知未送达或通知栏显示异常 | 5 |
| 2 | 飞书：登录或注册验证码未送达 | 2 |
| 3 | 企业微信：安装更新附加文件下载失败或循环提示 | 3 |
| 4 | 企业微信：界面语言无法切换或选择后不生效 | 3 |
| 5 | 企业微信：换机登录陷入旧设备验证循环 | 1 |

Top 5 的排序和方案是阅读原文后的离线编辑判断，保存在脚本中；新批次不会套用这五条固定结论。完整依据见[产品洞察](analysis/results/e2_product_insights/product_insights.md)，实现说明见[分析流程](analysis/README.md)。

Top 1（`ISS-3955d4f6ed9d`）对应 L002、L003、L010、L016、L038。基于这五条证据提出“通知状态诊断”，形成 [Mini PRD](docs/top1_notification_mini_prd.md) 和[三帧原型规格](docs/top1_notification_figma_spec.md)。目前只有设计文档，没有实际 Figma 文件或通知诊断功能。

## 结果如何解读

- 样本较小，Dev 全为飞书，结论限于当前样本；54 个 Issue 尚未全部深度复核。
- 模块 Macro-F1 按固定 19 类计算，5 类未在 Holdout 出现。严重程度“低”support=0，且准确率与始终预测“高”的基线相同，仍需人工判断。
- 历史 normalization 仅部分复现；Holdout 已被查看，后续调优需要新的独立测试集。
- 产品建议尚未经过真实用户和技术可行性验证，没有已证实的业务收益。

更多决策与失败过程见[项目案例](docs/project_case.md)；[项目状态](docs/project_status.md)列出实现边界。

公开副本已清除两个输入 Excel 的用户名，保留评论 ID、标签和实验结果；具体变更见[公开数据说明](docs/publication_notes.md)。
