"""上传闭环离线测试：所有文件写 tmp_path，真实 API 禁止。"""

from io import BytesIO
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import app
from src import user_runs as runs
from src.model_client import FeedbackModelClient, ModelConfig
from src.workbench_data import issue_evidence

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path / "user_runs")
    monkeypatch.setattr(FeedbackModelClient, "_call_api", Mock(side_effect=AssertionError("禁止真实 API")))


def upload_frame():
    return pd.DataFrame({"product": ["飞书", "飞书", "企业微信"],
                         "comment": ["经常收不到消息通知", "后台消息通知收不到", "登录验证码一直失败"]})


def prepared():
    identifier = runs.batch_id()
    frame, _ = runs.prepare_feedback(upload_frame(), identifier, "comment", "product")
    return identifier, frame


def analyze(identifier, frame):
    return runs.analyze_batch(frame, identifier, "comments.csv", FeedbackModelClient(ModelConfig(mode="mock")))


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_csv_xlsx_upload_without_model(extension, monkeypatch):
    frame = upload_frame()
    client = Mock(side_effect=AssertionError("上传不能创建模型"))
    monkeypatch.setattr(FeedbackModelClient, "__init__", client)
    if extension == "csv":
        data = frame.to_csv(index=False).encode("utf-8-sig")
    else:
        output = BytesIO()
        frame.to_excel(output, index=False)  # 仅构造测试输入，不修改项目证据。
        data = output.getvalue()
    parsed = runs.read_upload(data, "comments." + extension)
    assert parsed.equals(frame)
    assert runs.detect_columns(parsed) == {"comment": "comment", "product": "product", "id": None}
    client.assert_not_called()
    assert not runs.RUNS_DIR.exists()


def test_cleaning_columns_ids_and_missing_product():
    identifier = runs.batch_id()
    with pytest.raises(ValueError, match="缺少评论列"):
        runs.prepare_feedback(upload_frame(), identifier, None, "product")
    frame = pd.DataFrame({"c": ["", "通知失败", "通知失败", "登录失败"], "p": ["飞书", "飞书", "飞书", ""]})
    with pytest.raises(ValueError, match="缺失产品"):
        runs.prepare_feedback(frame, identifier, "c", "p")
    first, stats = runs.prepare_feedback(frame, identifier, "c", "p", default_product="企微")
    second, _ = runs.prepare_feedback(frame, identifier, "c", "p", default_product="企微")
    assert first.equals(second) and first["ID"].is_unique
    assert stats == {"读取行数": 4, "空评论数": 1, "重复评论数": 1, "清洗后行数": 2}
    with pytest.raises(ValueError, match="重复 ID"):
        runs.prepare_feedback(upload_frame().assign(id="same"), identifier, "comment", "product", "id")
    with pytest.raises(ValueError):
        runs.run_path("../../e1_offline")


def test_analysis_review_and_manual_reuse_of_e1_e2(monkeypatch):
    import analysis.run_issue_clustering as e1
    import analysis.build_product_insights as e2
    cluster, insights = Mock(wraps=e1.run_from_records), Mock(wraps=e2.build_for_batch)
    monkeypatch.setattr(e1, "run_from_records", cluster)
    monkeypatch.setattr(e2, "build_for_batch", insights)
    identifier, frame = prepared()
    result, meta = analyze(identifier, frame)
    assert len(result) == 3 and result["分析来源"].eq("mock").all()
    assert meta["analysis_status"] == "completed" and meta["issue_analysis_status"] == "not_started"
    assert not cluster.called and not insights.called
    with pytest.raises(ValueError, match="确认"):
        runs.update_insights(identifier)
    assert not cluster.called
    meta = runs.update_insights(identifier, confirmed=True)
    cluster.assert_called_once()
    insights.assert_called_once()
    assert meta["issue_analysis_status"] == "completed"
    _, package, comparison, audit = runs.read_insights(identifier)
    assert comparison.issue_feedback_count.sum() == len([r for r in package["feedback_mapping"] if r["eligible"]])
    for issue in package["issues"]:
        members = issue_evidence(package, issue["issue_id"])
        assert len(members) == issue["evidence_count"]
        assert all(r["comment"] == frame.set_index("ID").loc[r["feedback_id"], "评论原文"] for r in members)
    directory = runs.run_path(identifier) / meta["insights_directory"]
    assert (directory / "e2/product_insights.md").exists()
    assert all(c["review_status"] == "needs_review" for c in audit["top_issue_cards"])
    with pytest.raises(ValueError, match="不允许覆盖"):
        runs.update_insights(identifier, confirmed=True)


def test_partial_analysis_preserved_and_insights_blocked(monkeypatch):
    identifier, frame = prepared()
    client = FeedbackModelClient(ModelConfig(mode="mock"))
    original = client.analyze
    count = 0
    def fail_second(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("模拟结构化失败")
        return original(*args)
    monkeypatch.setattr(client, "analyze", fail_second)
    result, meta = runs.analyze_batch(frame, identifier, "test.csv", client)
    assert meta["analysis_status"] == "failed" and meta["success_count"] == 2
    assert len(result) == 3 and "ValueError" in result.iloc[1]["错误信息"]
    assert (runs.run_path(identifier) / "structured_feedback.json").exists()
    with pytest.raises(ValueError, match="包含失败"):
        runs.update_insights(identifier, confirmed=True)


def test_insight_failure_retains_analysis_and_retry_uses_new_directory(monkeypatch):
    import analysis.build_product_insights as e2
    identifier, frame = prepared()
    analyze(identifier, frame)
    path = runs.run_path(identifier) / "structured_feedback.json"
    before = path.read_bytes()
    original = e2.build_for_batch
    monkeypatch.setattr(e2, "build_for_batch", Mock(side_effect=RuntimeError("失败")))
    with pytest.raises(ValueError, match="Structured analysis completed, but product insight update failed"):
        runs.update_insights(identifier, confirmed=True)
    assert runs.load_batch(identifier)[0]["issue_analysis_status"] == "failed"
    assert path.read_bytes() == before
    monkeypatch.setattr(e2, "build_for_batch", original)
    runs.update_insights(identifier, confirmed=True)
    assert len(list(path.parent.glob("insights-*"))) == 2


def test_all_excluded_empty_insights_and_api_disabled():
    identifier, frame = prepared()
    with pytest.raises(ValueError, match="不允许真实 API"):
        runs.analyze_batch(frame, identifier, "test.csv", FeedbackModelClient(ModelConfig(mode="api")))
    assert not runs.run_path(identifier).exists()
    analyze(identifier, frame)
    runs.update_insights(identifier, frame["ID"].tolist(), confirmed=True)
    _, package, comparison, audit = runs.read_insights(identifier)
    assert not package["issues"] and comparison.empty and not audit["top_issue_cards"]


def test_ui_upload_analyze_review_update_and_switch(monkeypatch):
    # AppTest 不支持原生上传交互；注入 file_uploader 返回值，其他按钮真实点击。
    file = BytesIO(upload_frame().to_csv(index=False).encode("utf-8-sig"))
    file.name = "new.csv"
    monkeypatch.setattr(app.st, "file_uploader", lambda *a, **kw: file)
    test = AppTest.from_file(str(ROOT / "app.py")).run(timeout=20)
    test.switch_page("app_pages/feedback_analysis.py").run(timeout=20)
    assert not test.exception and not runs.RUNS_DIR.exists()
    test.button(key="import_analyze").click().run(timeout=20)
    assert not test.exception
    identifier = test.session_state["import_draft"]["batch_id"]
    assert runs.load_batch(identifier)[0]["issue_analysis_status"] == "not_started"
    assert test.button(key="import_update").disabled
    test.checkbox(key=f"new_confirm_{identifier}").check().run()
    test.button(key="import_update").click().run(timeout=20)
    assert not test.exception and runs.load_batch(identifier)[0]["issue_analysis_status"] == "completed"
    test.switch_page("app_pages/product_insights.py").run(timeout=20)
    assert test.selectbox(key="insights_analysis_run").value == "Project Demo / Original E2"
    test.selectbox(key="insights_analysis_run").select(identifier).run(timeout=20)
    assert not test.exception
    assert any("本批次产品问题画像" == d.proto.alt for d in test.dataframe)
    test.selectbox(key="insights_analysis_run").select("Project Demo / Original E2").run(timeout=20)
    assert not test.exception and test.selectbox(key="top_issue_id")


def test_ui_missing_column_friendly_and_dll_fallback(monkeypatch):
    file = BytesIO(b"foo,bar\na,b\n")
    file.name = "missing.csv"
    monkeypatch.setattr(app.st, "file_uploader", lambda *a, **kw: file)
    monkeypatch.setattr(app.st, "dataframe", Mock(side_effect=ImportError("DLL")))
    test = AppTest.from_file(str(ROOT / "app.py")).run(timeout=20)
    test.switch_page("app_pages/feedback_analysis.py").run(timeout=20)
    assert not test.exception
    assert any("缺少评论列" in w.value for w in test.warning)
    assert any("<table" in m.value for m in test.markdown)
    assert not runs.RUNS_DIR.exists()
