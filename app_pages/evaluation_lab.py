"""只读实验档案；既不重算指标，也不连接 Langfuse。"""

import pandas as pd
import streamlit as st

from app import get_result, page_intro, show_dataframe, show_metrics
from src.workbench_data import FIELDS, HOLDOUT, R3, historical_dev_table, holdout_table, failed_samples

page_intro("Evaluation Lab", "这一页解决：V1 为什么不好，如何根据错误迭代，V3 的质量与局限是什么？")
st.subheader("Evaluation Setup")
receipt = get_result(R3, ["model", "dataset", "summary", "runs", "model_api_calls", "status"])
st.write(f"Gold Set：82 · Dev：20 · Holdout：62 · Model：{receipt['model'] if receipt else 'gpt-6.1-sol（项目配置元数据）'}")
st.caption("Dev：L001–L020；Holdout：L021–L043 + W001–W039。评价是否有效、反馈类型、产品模块、严重程度。")
st.caption("核心痛点为自由文本，只保存，不做 Exact Match 正式评分；Gold 严重程度为不适用时跳过，不能计成错误或正确。")

st.subheader("Prompt Iteration · 历史本地 Dev")
summaries = {}
for version, folder in [("v1", "v1_baseline"), ("v2", "v2"), ("v3", "v3")]:
    frame = get_result(f"evaluation/results/{folder}/evaluation_summary.csv", ["字段名", "一致率"])
    if frame is not None:
        summaries[version] = frame
if summaries:
    show_dataframe(historical_dev_table(summaries), hide_index=True, width="stretch", alt="历史本地 V1 至 V3 Dev 准确率")
left, right = st.columns(2)
with left.container(border=True):
    st.markdown("#### V1 → V2：统一输出空间")
    st.markdown("- 固定19类产品模块 taxonomy\n- 对齐正式反馈类型\n- 统一严重程度标签；正向及无效反馈不适用\n- 明确 Bug / 使用困难等基础规则")
with right.container(border=True):
    st.markdown("#### V2 → V3：修正判定边界")
    st.markdown("- 强化 Bug vs 使用困难边界\n- 后文最新修正优先\n- 短文本／混合语言只要具体仍可有效\n- 补充少量模块映射\n- 严重程度关注实际影响")
st.caption("V1 模块为自由文本，V2/V3 为固定 taxonomy：5%→90% 不可直接解释为纯模型能力提升。历史 normalization 为 partial reproduction，未完整保存原始源码。")

st.subheader("V3 Holdout Metrics")
metrics = get_result(f"{HOLDOUT}/metrics_overall.csv", ["field", "accuracy", "macro_f1", "weighted_f1", "n_samples", "correct"])
if metrics is not None:
    show_dataframe(holdout_table(metrics), hide_index=True, width="stretch", alt="冻结 V3 的 Holdout Accuracy 与 F1")
    all_fields = metrics[metrics["field"] == "all_fields_correct"]
    if not all_fields.empty:
        row = all_fields.iloc[0]
        st.metric("All-fields Correct", f"{int(row['correct'])}/{int(row['n_samples'])} = {float(row['accuracy']):.2%}", border=True)
st.caption("严重程度仅评价适用样本（见 Samples）；类别不均衡，Holdout 的低类别 Support=0。模块 Macro-F1 按固定19类计算，未覆盖类不能视为已有表现。")

st.subheader("Error Analysis")
patterns = get_result(f"{HOLDOUT}/error_patterns.csv", ["field", "gold", "pred", "count", "sample_ids"])
if patterns is not None:
    show_dataframe(patterns.sort_values("count", ascending=False, kind="stable").head(3).rename(columns={"field": "错误字段", "gold": "Gold", "pred": "Prediction", "count": "数量", "sample_ids": "反馈ID"}), hide_index=True, width="stretch", alt="Holdout 最常见的三类错误")
st.write("Severity 是主要弱项；Bug 判断仍有过宽情况；模块总体准确率较高，但 Macro-F1 暴露长尾类别问题。")
st.caption("Holdout 错误已被查看分析，未据此创建 V4；未来针对这些错误调 Prompt 后，不能继续把该集称为完全未见测试集。")

st.subheader("Failed Sample Explorer")
versions = {"V1 · 历史 Dev": "v1_baseline", "V2 · 历史 Dev": "v2", "V3 · 历史 Dev": "v3", "V3 · Holdout": "v3_holdout"}
version = st.selectbox("Prompt Version / Split", list(versions), key="failed_version")
field = st.selectbox("错误字段", FIELDS, key="failed_field")
detail = get_result(f"evaluation/results/{versions[version]}/evaluation_detail.xlsx", ["ID", "评论原文"])
if detail is not None:
    required = ["gold_" + field, "pred_" + field, "match_" + field]
    if set(required) - set(detail.columns):
        st.info("该历史文件未保存所选字段的完整对比，不补造。")
    else:
        failed = failed_samples(detail, field)
        show_dataframe(failed, hide_index=True, width="stretch", alt="保存的历史错误样本，不重新评分")
        if not failed.empty:
            chosen = st.selectbox("展开错误反馈 ID", failed["ID"].tolist(), key="failed_id")
            row = failed[failed["ID"] == chosen].iloc[0]
            st.text(row["原始评论"])
            st.write(f"Gold：{row['Gold']} · Prediction：{row['Prediction']} · Match：{row['Match']}")
        else:
            st.info("所选历史字段没有已保存的错误样本。")

st.subheader("Langfuse Experiment · r3 重新生成 Dev")
if receipt is not None:
    st.caption(f"Dataset：{receipt['dataset']} · 本地保存的服务端核验状态：{receipt['status']}（本页不连接服务端）")
    summaries = receipt["summary"]
    total_scores = sum(len(record.get("scores", {})) for run in receipt["runs"] for record in run["records"])
    show_metrics([("Model calls", receipt["model_api_calls"]), ("Successful Generations", sum(s["successful"] for s in summaries)), ("Failures", sum(s["failed"] for s in summaries)), ("Scores", total_scores)])
    rows, resources = [], []
    for summary in summaries:
        rows.append({"Version": summary["prompt_version"].upper(), "Experiment": summary["experiment"], **{
            label: f"{summary['metrics'][key]['accuracy']:.2%}"
            for key, label in [("valid_correct", "Valid"), ("feedback_type_correct", "Type"), ("product_module_correct", "Module"), ("severity_correct", "Severity"), ("all_fields_correct", "All-fields")]
        }})
        resources.append({"Version": summary["prompt_version"].upper(), "Avg Latency": f"{summary['mean_api_latency_seconds']:.3f}s", "Total Token": f"{summary['tokens']['total']:,}"})
    show_dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", alt="Langfuse r3 三版重新生成 Dev 对比")
    st.info("Langfuse r3为重新调用模型得到的Experiment结果，与历史本地Dev不是同一次生成，因此存在小幅随机波动。")
    st.markdown("#### Latency / Token")
    show_dataframe(pd.DataFrame(resources), hide_index=True, width="stretch", alt="Langfuse r3 latency 与 token 用量")
    total_samples = sum(s["samples"] for s in summaries)
    mean = sum(s["mean_api_latency_seconds"] * s["samples"] for s in summaries) / total_samples if total_samples else None
    st.caption(f"总体 token：{sum(s['tokens']['total'] for s in summaries):,} · Avg API latency：{mean:.3f}s" if mean is not None else "未保存可用耗时统计")
    st.write("随着Prompt规则更完整，token使用明显增加，模型质量与调用资源之间存在权衡。")

with st.expander("正式实验总结与限制"):
    report = get_result("evaluation/results/experiment_summary.md")
    if report is not None:
        st.markdown(report)
