"""现有两页中的新批次组件；无自动分析或自动洞察更新。"""

from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from src import user_runs as runs
from src.model_client import FeedbackModelClient, ModelConfig
from src.workbench_data import issue_evidence


@st.cache_data(show_spinner=False, max_entries=5)
def upload_preview(data, filename, sheet):
    return runs.read_upload(data, filename, sheet)


@st.cache_data(show_spinner=False, max_entries=5)
def upload_sheets(data):
    with pd.ExcelFile(BytesIO(data)) as workbook:
        return workbook.sheet_names


def clear_removed_upload():
    if st.session_state.get("new_feedback_upload") is None:
        for key in ("import_file", "import_token", "import_draft"):
            st.session_state.pop(key, None)


def show_import_workflow():
    from app import show_dataframe
    st.subheader("Import New Feedback")
    st.info("当前模式：Demo / Mock。上传不调用模型；Analyze Feedback 仅运行本地规则，不代表真实模型质量。")
    uploaded = st.file_uploader("上传新评论（不会覆盖历史82条）", type=["csv", "xlsx", "xls"], key="new_feedback_upload", max_upload_size=20,
                                on_change=clear_removed_upload)
    if uploaded is not None:
        data, filename = uploaded.getvalue(), uploaded.name
        token = sha256(filename.encode() + data).hexdigest()
        if st.session_state.get("import_token") != token:
            st.session_state["import_token"] = token
            st.session_state["import_file"] = (filename, data)
            st.session_state.pop("import_draft", None)
    # 跨页仍可查看当前上传；不依赖 file_uploader widget 的临时状态。
    stored = st.session_state.get("import_file")
    if stored is None:
        st.caption("CSV 可用 UTF-8 / GB18030；Excel 可选择工作表。字段确认后才分析。")
        return
    filename, data = stored
    try:
        sheet = 0
        if Path(filename).suffix.lower() in {".xlsx", ".xls"}:
            sheet = st.selectbox("工作表", upload_sheets(data), key="new_feedback_sheet")
        frame = upload_preview(data, filename, sheet)
    except Exception as error:
        st.warning(f"文件无法读取（{type(error).__name__}）。请检查格式、编码或工作表。")
        return
    st.caption(f"{filename} · 原始行数 {len(frame)} · 预览前8条")
    show_dataframe(frame.head(8), hide_index=True, width="stretch", alt="新上传评论预览")
    detected = runs.detect_columns(frame)
    options = [None] + frame.columns.tolist()
    selected = {}
    for field, label in [("comment", "评论原文列"), ("product", "产品列"), ("id", "ID列（可选）")]:
        selected[field] = st.selectbox(label, options, index=options.index(detected[field]),
                                       format_func=lambda c: "未选择 / 不存在" if c is None else c,
                                       key=f"new_col_{field}_{st.session_state['import_token']}_{sheet}")
    default = st.text_input("缺失产品默认值（仅填补空产品，不改非空值）", key="new_default_product")
    config_key = (st.session_state["import_token"], str(sheet), tuple(selected.values()), default)
    draft = st.session_state.get("import_draft")
    if not draft or draft["config_key"] != config_key:
        draft = {"batch_id": runs.batch_id(), "config_key": config_key}
        st.session_state["import_draft"] = draft
    identifier = draft["batch_id"]
    try:
        cleaned, stats = runs.prepare_feedback(frame, identifier, selected["comment"], selected["product"], selected["id"], default)
    except ValueError as error:
        st.warning(str(error))
        return
    st.caption(f"batch_id：{identifier} · 移除空评论 {stats['空评论数']} 条、重复 {stats['重复评论数']} 条；待分析 {len(cleaned)} 条")
    if st.button("新建独立批次（相同文件）", key="new_batch_again"):
        st.session_state.pop("import_draft", None)
        st.rerun()
    already = "metadata" in draft
    if st.button("Analyze Feedback", type="primary", key="import_analyze", disabled=already):
        try:
            # 客户端只能在主动点击之后创建，不读取环境自动切 API。
            client = FeedbackModelClient(ModelConfig(mode="mock", allow_mock_fallback=False))
            with st.spinner("正在本地分析新评论…"):
                _, draft["metadata"] = runs.analyze_batch(cleaned, identifier, filename, client)
        except Exception as error:
            st.error(f"结构化分析未完成（{type(error).__name__}）。成功写入的数据仍保留在该 batch 目录；请新建批次再试。")
    if not runs.run_path(identifier).joinpath("metadata.json").exists():
        return
    try:
        meta, result = runs.load_batch(identifier)
    except (OSError, ValueError):
        st.warning("该批次尚无可读取的结构化结果，已落盘文件不会删除。")
        return
    st.subheader("Review Structured Feedback")
    st.caption("先检查结果，再确认是否更新洞察。确认不等于已完成完整人工标注或 Issue 复核。")
    columns = ["ID", "产品", "评论原文", "是否为有效产品反馈", "反馈类型", "产品模块", "严重程度", "核心痛点", "分析来源", "错误信息"]
    show_dataframe(result.reindex(columns=columns), hide_index=True, width="stretch", alt="待人工确认的结构化反馈")
    with st.expander("查看单条完整字段与原文"):
        selected_id = st.selectbox("新批次反馈 ID", result["ID"].tolist(), key=f"new_review_id_{identifier}")
        st.json(result.loc[result["ID"] == selected_id].iloc[0].to_dict())
    st.download_button("下载本批次结构化 CSV", (runs.run_path(identifier) / "structured_feedback.csv").read_bytes(),
                       file_name=f"{identifier}.csv", mime="text/csv", key=f"new_download_{identifier}")
    if meta["analysis_status"] != "completed":
        st.error("结构化分析包含失败行；已保留成功结果，不允许进入 Issue 更新。请查看错误信息。")
        return
    excluded = st.multiselect("排除本次 Issue 更新的 Feedback ID（可选）", result["ID"].tolist(), key=f"new_exclude_{identifier}")
    confirmed = st.checkbox("我已查看结构化结果，确认将未排除的反馈用于本次洞察更新", key=f"new_confirm_{identifier}")
    st.caption("将基于当前已确认的分析结果重新运行 Issue Clustering 和 Product Insight 生成流程。只处理本批次，不混入历史82条。")
    if st.button("Update Issue & Product Insights", key="import_update", disabled=not confirmed or meta["issue_analysis_status"] == "completed"):
        try:
            with st.spinner("正在复用 E1 / E2 生成本批次候选洞察…"):
                meta = runs.update_insights(identifier, excluded, confirmed=confirmed)
        except Exception:
            st.error("Structured analysis completed, but product insight update failed.")
    if meta["issue_analysis_status"] == "completed":
        st.success(f"本批次洞察已保存：{meta['issue_count']} 个候选 Issue。前往 Product Insights 的 Analysis Run 选择此批次。")
        st.caption("本次排除 IDs：" + ", ".join(meta.get("excluded_ids", [])) + "；洞察已固定，若需调整请新建独立批次。")
    elif meta["issue_analysis_status"] == "failed":
        st.error("Structured analysis completed, but product insight update failed.")


def show_user_insights(identifier):
    from app import show_dataframe
    st.info("独立用户批次 · Demo / Mock · 自动候选 needs_review，不是历史 E2 或真实模型质量结论。")
    try:
        meta, package, comparison, audit = runs.read_insights(identifier)
        st.caption(f"{identifier} · 来源 {meta['source_filename']} · 输入 {meta['feedback_count']} 条 · 确认纳入 {meta['confirmed_feedback_count']} 条")
        st.subheader("Product Problem Profile")
        show_dataframe(comparison, hide_index=True, width="stretch", alt="本批次产品问题画像")
        st.caption("percentage 是各产品问题反馈内占比（0–100），不是用户或市场比例。")
        st.subheader("Top Issues & Product Suggestions")
        for card in audit["top_issue_cards"]:
            with st.expander(f"{card['rank']}. {card['product']} · {card['issue_title']}"):
                st.write(card["problem"])
                st.write(f"证据 {card['evidence_count']} 条：" + ", ".join(card["evidence_ids"]))
                st.write(f"候选优先级 {card['priority']}：{card['priority_reason']}")
                st.write(card["competitor_summary"])
                for suggestion in card["proposed_solution"]:
                    st.write(suggestion)
                st.caption("未来验证指标（无当前值）：" + "；".join(card["validation_metrics"]))
        st.subheader("Issue → Evidence")
        issues = {i["issue_id"]: i for i in package["issues"]}
        if issues:
            chosen = st.selectbox("本批次 Issue", list(issues), format_func=lambda i: f"{issues[i]['product']} · {issues[i]['issue_title']}", key=f"batch_issue_{identifier}")
            for row in issue_evidence(package, chosen):
                with st.container(border=True):
                    st.write(f"{row['feedback_id']} · {row['product']} · {row['product_module']} · {row['feedback_type']} · {row['severity']}")
                    st.text(row["comment"])
                    st.write("核心痛点：" + row["core_user_pain"])
                    st.write("用户场景：" + row["user_scenario"])
        else:
            st.info("本批次没有有效 Bug / 使用困难 / 功能建议，未生成问题簇。")
        with st.expander("Feedback → Structured Analysis → Issue Mapping"):
            show_dataframe(pd.DataFrame(package["feedback_mapping"]), hide_index=True, width="stretch", alt="本批次完整反馈与 Issue 映射")
    except Exception:
        st.warning("该批次结果无法完整读取，请检查本地 batch 文件；历史成果不受影响。")
