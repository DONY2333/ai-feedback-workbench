# Project Status

## Completed

- 本地 Excel 清洗、独立模型客户端、八字段 JSON 与容错；Mock 和严格 API Evaluation 分开。
- Streamlit 四页：Overview、Feedback Analysis、Evaluation Lab、Product Insights；筛选、双向证据追溯与 DLL 表格降级。
- 82 条人工 Gold，Dev 20 / Holdout 62，三版快照、本地指标和错误分析。
- Langfuse Generation、Score、Dev Dataset、三版正式 r3 Experiment；60 成功、294 Scores，有保存的服务端核验回执。
- 版本化历史处理、回放验证及三层输出隔离。
- 63 条问题反馈的离线规则归并、54 个候选 Issue、竞品样本画像与 Top 5。
- 通知 Top 1 Mini PRD、三帧 Figma 页面规格（非实际 Figma 文件）。
- 实验总结、案例、简历、口述稿、面试问答与截图清单；本地自动化测试。

## Known Limitations

- Gold 小，Dev 全为飞书；无完整独立标注裁决与标注者一致性测量。
- Holdout 已被查看，不再是未来 pristine unseen test set。
- 严重程度弱，低类 support=0；模块有未覆盖类。
- 核心痛点、场景、情绪与证据质量未有完整自由文本评价。
- 三版历史 normalization 均 partial reproduction，r3 是重新生成。
- 54 个候选未全部深度人工复核，Top 5 离线核对不等于负责人签核。
- 样本对比不能推断总体质量、用户规模或市场。
- 诊断方案待用户及技术验证，无法保证定位权限或服务端故障。
- 自动化页面测试不等于完整视觉验收，演示前需实际检查界面。
- 部分结果被 Git 忽略，交付应保留获准证据；公开前审查隐私、授权，不能含密钥。
- 无可信货币成本比较或已证实业务收益。

## Not Built

Production 数据库、用户权限系统、完整人工标注后台、自动 Production 反馈闭环、V4、RAG、Agent、SQL 数据层、正式线上部署。
也未实现 embedding/LLM 语义聚类、真实通知诊断或实际 Figma 高保真原型。

本文件记录第一版边界，不是新阶段开发计划。
