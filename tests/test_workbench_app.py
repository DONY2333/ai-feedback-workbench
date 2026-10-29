"""四页离线工作台的必要验收，不创建网络客户端。"""

from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import app
from src import workbench_data as data
from src.exporter import export_results

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["overview", "feedback_analysis", "evaluation_lab", "product_insights"]


@pytest.fixture(autouse=True)
def forbid_api(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("演示或测试不能调用模型 API")
    monkeypatch.setattr(app.FeedbackModelClient, "_call_api", forbidden)


def open_page(page):
    test = AppTest.from_file(str(ROOT / "app.py")).run(timeout=20)
    if page != "overview":
        test.switch_page(f"app_pages/{page}.py").run(timeout=20)
    return test


@pytest.mark.parametrize("page", PAGES)
def test_four_pages_load_without_api(page, monkeypatch):
    monkeypatch.setattr(app.FeedbackModelClient, "__init__", Mock(side_effect=AssertionError("浏览不能创建模型客户端")))
    test = open_page(page)
    assert not test.exception
    if page == "overview":
        assert [m.value for m in test.metric[:6]] == ["82", "63", "31", "32", "54", "5"]
        assert [m.value for m in test.metric[6:]] == ["98.39%", "87.10%", "80.65%", "70.45%", "59.68%"]
    elif page == "evaluation_lab":
        assert any(m.label == "Scores" and m.value == "294" for m in test.metric)


def test_e2_and_complete_issue_evidence():
    package = data.read_result(f"{data.E1}/issue_clusters.json")
    audit = data.read_result(f"{data.E2}/review_audit.json")
    top = data.read_result(f"{data.E2}/top_issues.csv")
    assert len(top) == len(audit["top_issue_cards"]) == 5
    for card in audit["top_issue_cards"]:
        members = data.issue_evidence(package, card["issue_id"])
        assert [r["feedback_id"] for r in members] == card["evidence_ids"]
        assert len(members) == card["evidence_count"]
        assert all(r["comment"] for r in members)
    test = open_page("feedback_analysis")
    issue = audit["top_issue_cards"][0]
    test.selectbox(key="evidence_issue_id").select(issue["issue_id"]).run()
    assert not test.exception
    shown = [t.value for t in test.text]
    assert all(r["comment"] in shown for r in data.issue_evidence(package, issue["issue_id"]))
    test.selectbox(key="feedback_filter_product").select("飞书/Lark").run()
    assert len(test.dataframe[0].value) == 43


def test_saved_evaluation_metrics_and_error_samples():
    frame = data.read_result(f"{data.HOLDOUT}/metrics_overall.csv")
    assert data.holdout_table(frame)["Accuracy"].tolist() == ["98.39%", "87.10%", "80.65%", "70.45%"]
    summaries = {v: data.read_result(f"evaluation/results/{folder}/evaluation_summary.csv") for v, folder in [("v1", "v1_baseline"), ("v2", "v2"), ("v3", "v3")]}
    table = data.historical_dev_table(summaries)
    assert table["Product Module"].tolist() == ["5.00%", "90.00%", "90.00%"]
    assert table["Severity"].tolist() == ["66.67%", "66.67%", "77.78%"]
    detail = data.read_result("evaluation/results/v3_holdout/evaluation_detail.xlsx")
    errors = data.failed_samples(detail, "严重程度")
    assert len(errors) == 13
    assert "不适用" not in errors["Gold"].tolist()


@pytest.mark.parametrize("page", PAGES)
def test_missing_results_are_friendly(page, monkeypatch, tmp_path):
    monkeypatch.setattr(data, "ROOT", tmp_path)
    test = open_page(page)
    assert not test.exception
    assert test.warning
    assert any("对应离线分析结果未找到" in w.value for w in test.warning)


def test_partial_file_loss_only_affects_one_section(monkeypatch):
    original = app.get_result
    def missing_e2(relative, required=()):
        if relative.endswith("review_audit.json"):
            app.st.warning("对应离线分析结果未找到：E2 卡片")
            return None
        return original(relative, required)
    monkeypatch.setattr(app, "get_result", missing_e2)
    test = open_page("feedback_analysis")
    assert not test.exception
    assert len(test.dataframe[0].value) == 82


@pytest.mark.parametrize("page", PAGES)
def test_html_fallback_on_all_pages(page, monkeypatch):
    monkeypatch.setattr(app.st, "dataframe", Mock(side_effect=ImportError("Windows DLL policy")))
    test = open_page(page)
    assert not test.exception
    assert any("<table" in element.value for element in test.markdown)


def test_mock_batch_and_export_still_work(tmp_path, monkeypatch):
    # 真正运行本地 mock 与已有导出函数，输出仅写 pytest 临时目录。
    monkeypatch.setattr(app, "export_results", lambda frame: export_results(frame, tmp_path))
    test = open_page("feedback_analysis")
    test.toggle(key="batch_enabled").set_value(True).run(timeout=20)
    assert not test.exception
    next(b for b in test.button if "仅本地 Mock" in b.label).click().run(timeout=20)
    assert not test.exception
    next(b for b in test.button if b.label == "保存 CSV 和 Excel").click().run(timeout=20)
    assert not test.exception
    assert len(list(tmp_path.glob("*.csv"))) == len(list(tmp_path.glob("*.xlsx"))) == 1
