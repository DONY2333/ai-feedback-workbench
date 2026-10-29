"""AI 用户反馈与产品需求分析工作台 MVP。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.analyzer import analyze_comments
from src.dashboard import comments_for_issue, count_by, frequent_issues, product_comparison
from src.data_loader import find_excel_files, load_excel_comments
from src.exporter import export_results
from src.model_client import FeedbackModelClient, ModelConfig


def show_dataframe(data: pd.DataFrame, **kwargs) -> None:
    """优先使用交互表格；pyarrow/DLL 导入失败时降级为转义的 HTML。"""
    try:
        import pyarrow  # noqa: F401  在展示时检查，避免 DLL 异常阻止页面加载。

        st.dataframe(data, **kwargs)
    except ImportError:
        # 对比表的产品在命名索引中；仅在展示副本中转成列，避免降级后丢失产品名。
        if not kwargs.get("hide_index", False) and data.index.name is not None:
            data = data.reset_index()
        st.markdown(data.to_html(index=False, escape=True), unsafe_allow_html=True)


def get_result(relative: str, required=()):
    """一个结果缺失只影响该区块，不影响其他页。"""
    from src.workbench_data import OfflineResultError, read_result

    try:
        return read_result(relative, required)
    except OfflineResultError as error:
        st.warning(str(error))
        return None


def page_intro(title: str, purpose: str) -> None:
    st.title(title)
    st.caption(purpose)
    st.caption("以下结论基于当前采集样本，不代表全部用户或整体市场表现。")


def show_metrics(items) -> None:
    with st.container(horizontal=True):
        for label, value in items:
            st.metric(label, value, border=True)


def show_bar_chart(data: pd.DataFrame) -> None:
    try:
        import pyarrow  # noqa: F401
        st.bar_chart(data)
    except ImportError:
        show_dataframe(data, width="stretch")


@st.cache_data(show_spinner=False)
def load_data(data_dir: str) -> tuple[pd.DataFrame, dict[str, int]]:
    return load_excel_comments(data_dir)


def run_analysis(data: pd.DataFrame, requested_mode: str) -> pd.DataFrame:
    config = ModelConfig.from_environment(requested_mode)
    client = FeedbackModelClient(config)
    progress = st.progress(0, text="准备分析评论…")

    def update(current: int, total: int) -> None:
        percent = current / total if total else 1
        progress.progress(percent, text=f"正在分析 {current}/{total} 条评论")

    result = analyze_comments(data, client, update)
    progress.empty()
    st.session_state["model_mode"] = config.mode
    return result


def show_dashboard(result: pd.DataFrame) -> None:
    st.subheader("分析概览")
    valid_count = int(result["是否为有效产品反馈"].sum())
    negative_count = int(result["情绪倾向"].eq("负向").sum())
    serious_count = int(result["严重程度"].isin(["高", "严重"]).sum())
    metric_columns = st.columns(4)
    metric_columns[0].metric("总评论数", len(result))
    metric_columns[1].metric("有效产品反馈", valid_count)
    metric_columns[2].metric("负向评论", negative_count)
    metric_columns[3].metric("高/严重问题", serious_count)

    left, right = st.columns(2)
    with left:
        st.markdown("#### 各反馈类型数量")
        type_counts = count_by(result, "反馈类型").set_index("反馈类型")
        show_bar_chart(type_counts)
    with right:
        st.markdown("#### 各产品模块数量")
        module_counts = count_by(result, "产品模块").set_index("产品模块")
        show_bar_chart(module_counts)

    st.markdown("#### 高频问题")
    issues = frequent_issues(result)
    if issues.empty:
        st.info("暂未识别到可聚合的具体问题。")
    else:
        show_dataframe(issues, width="stretch", hide_index=True)
        issue = st.selectbox("选择一个问题，查看对应原始评论", issues["核心痛点"].tolist())
        show_dataframe(comments_for_issue(result, issue), width="stretch", hide_index=True)

    st.markdown("#### 飞书 vs 企业微信对比")
    comparison = product_comparison(result)
    if comparison.empty:
        st.info("数据中没有可对比的产品字段。")
    else:
        show_dataframe(comparison, width="stretch")
        chart_data = comparison.drop(columns=["总计"], errors="ignore")
        show_bar_chart(chart_data)

    with st.expander("查看全部结构化分析结果"):
        show_dataframe(result, width="stretch", hide_index=True)


def show_batch_analysis() -> None:
    """保留原本地分析/导出，演示中不提供可触发真实请求的按钮。"""
    st.caption("可选流程测试，不进入历史成果。仅 Mock；演示界面禁用真实 API，原模型与批量处理代码保持不变。")
    data_dir = st.text_input("批量分析数据文件夹", value=str(Path(__file__).resolve().parent / "data"))
    files = find_excel_files(data_dir)
    if not files:
        st.error(f"在 {Path(data_dir).resolve()} 中没有找到 .xlsx 或 .xls 文件。")
        return

    try:
        comments, stats = load_data(data_dir)
    except (ValueError, OSError, ImportError) as error:
        st.warning(f"本地 Excel 无法读取：{type(error).__name__}。请检查文件及 Excel 读取依赖。")
        return
    st.caption(f"读取 {stats['读取行数']} 行，清洗后 {stats['清洗后行数']} 行")
    if stats["空评论数"] or stats["重复评论数"]:
        st.caption(f"移除空评论 {stats['空评论数']} 条、重复评论 {stats['重复评论数']} 条")

    if comments.empty:
        st.warning("Excel 中未找到评论数据。请确认存在“评论原文 / 评论 / review”等列。")
        return

    if st.button("开始 / 重新分析（仅本地 Mock）", type="primary"):
        st.session_state["analysis_result"] = run_analysis(comments, "mock")

    result = st.session_state.get("analysis_result")
    if result is None:
        show_dataframe(comments.head(20), width="stretch", hide_index=True)
        return

    mode = st.session_state.get("model_mode", "mock")
    st.info(f"当前结果使用 **{mode}** 模式。Mock 结果仅用于流程测试，不代表真实模型分析质量。")
    show_dashboard(result)

    st.sidebar.divider()
    if st.button("保存 CSV 和 Excel"):
        try:
            paths = export_results(result)
            st.success(f"已保存：{paths['csv'].name} / {paths['excel'].name}")
        except (OSError, ImportError, ValueError):
            st.warning("文件保存失败，请检查 outputs 目录写权限及 openpyxl 依赖；仍可下载 CSV。")

    csv_bytes = result.to_csv(index=False).encode("utf-8-sig")
    st.download_button("下载 CSV", csv_bytes, "feedback_analysis.csv", "text/csv")


def main() -> None:
    st.set_page_config(page_title="AI Feedback Workbench", page_icon=":material/forum:", layout="wide")
    st.sidebar.markdown("### Feedback Workbench")
    st.sidebar.caption("用户证据 → 问题洞察 → 产品建议")
    page = st.navigation([
        st.Page("app_pages/overview.py", title="Overview", icon=":material/home:", default=True),
        st.Page("app_pages/feedback_analysis.py", title="Feedback Analysis", icon=":material/forum:"),
        st.Page("app_pages/evaluation_lab.py", title="Evaluation Lab", icon=":material/science:"),
        st.Page("app_pages/product_insights.py", title="Product Insights", icon=":material/lightbulb:"),
    ], position="sidebar")
    st.sidebar.caption("默认历史成果 · 新上传仅手动 Mock 分析 · 不调用模型 API")
    page.run()


if __name__ == "__main__":
    main()
