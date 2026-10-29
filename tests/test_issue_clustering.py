"""E1 离线测试，禁止真实模型/网络依赖。"""

import json
from pathlib import Path

import pandas as pd
import pytest

from src.issue_clustering import (
    ISSUE_FIELDS, MAPPING_FIELDS, apply_review, build_clusters, export_package, load_feedback,
    package_results, review_template,
)


def feedback(id="A", **updates):
    row = dict(feedback_id=id, product="飞书", comment="后台收不到消息通知", valid=True,
               feedback_type="Bug", product_module="通知", core_user_pain="后台收不到消息通知",
               severity="高", user_scenario="")
    row.update(updates)
    return row


def test_semantic_anchor_and_evidence():
    rows = [feedback(), feedback("B", comment="不开 App 就不推送", core_user_pain="消息不推送"),
            feedback("C", core_user_pain="通知声音太小", feedback_type="使用困难"),
            feedback("D", core_user_pain="收不到验证码", product_module="账号/登录/验证")]
    issues = build_clusters(rows)
    assert sorted(i["evidence_count"] for i in issues) == [1, 1, 2]
    assert set(issues[0]) == set(ISSUE_FIELDS)
    package = package_results(rows, issues)
    assert sum(i["evidence_count"] for i in issues) == 4
    assert len({i for issue in issues for i in issue["evidence_ids"]}) == 4
    original = {r["feedback_id"]: r["comment"] for r in rows}
    for issue in issues:
        assert issue["evidence_count"] == len(issue["evidence_ids"])
        assert issue["review_status"] == "needs_review"
        for rep in issue["representative_comments"]:
            assert rep["comment"] == original[rep["feedback_id"]]
    assert len(package["feedback_mapping"]) == 4


@pytest.mark.parametrize("updates", [dict(valid=False), dict(feedback_type="情绪/无效反馈"), dict(feedback_type="正向评价")])
def test_exclusion(updates):
    rows = [feedback(**updates)]
    package = package_results(rows, build_clusters(rows))
    assert package["issues"] == []
    assert package["feedback_mapping"][0]["issue_id"] == ""
    assert package["feedback_mapping"][0]["exclusion_reason"]


@pytest.mark.parametrize("updates", [dict(product="企业微信"), dict(product_module="其他"), dict(feedback_type="功能建议")])
def test_group_boundaries(updates):
    assert len(build_clusters([feedback(), feedback("B", **updates)])) == 2


def test_empty_export_and_no_overwrite(tmp_path):
    package = package_results([], build_clusters([]))
    target = tmp_path / "run"
    export_package(package, target)
    assert list(pd.read_csv(target / "issue_clusters.csv")) == ISSUE_FIELDS
    assert pd.read_csv(target / "feedback_mapping.csv").empty
    assert list(pd.read_csv(target / "feedback_mapping.csv")) == MAPPING_FIELDS
    assert json.loads((target / "issue_clusters.json").read_text(encoding="utf-8"))["issues"] == []
    with pytest.raises(FileExistsError):
        export_package(package, target)


def test_review_rename_split_move_merge():
    rows = [feedback(), feedback("B"), feedback("C", core_user_pain="通知声音太小")]
    review = review_template(build_clusters(rows))
    merged = next(i for i in review["issues"] if len(i["evidence_ids"]) == 2)
    merged["evidence_ids"].remove("B")
    review["issues"].append(dict(issue_id="new", issue_title="后台通知未送达", evidence_ids=["B"], review_status="reviewed"))
    assert len(apply_review(rows, review)) == 3
    review["issues"][-1]["evidence_ids"].append("A")
    review["issues"] = [i for i in review["issues"] if i is not merged]
    issues = apply_review(rows, review)
    assert len(issues) == 2
    assert next(i for i in issues if i["issue_id"] == "new")["evidence_count"] == 2


@pytest.mark.parametrize("kind", ["duplicate", "unknown", "missing", "cross_product", "cross_module"])
def test_invalid_review(kind):
    rows = [feedback(), feedback("B", product="企业微信", product_module="其他")]
    review = review_template(build_clusters(rows))
    if kind == "duplicate":
        review["issues"][0]["evidence_ids"] *= 2
    elif kind == "unknown":
        review["issues"][0]["evidence_ids"] = ["X"]
    elif kind == "missing":
        review["issues"].pop()
    else:
        if kind == "cross_module":
            rows[1]["product"] = rows[0]["product"]
        review["issues"][0]["evidence_ids"] = ["A", "B"]
        review["issues"] = review["issues"][:1]
    with pytest.raises(ValueError):
        apply_review(rows, review)


def test_input_duplicate_and_missing_ids():
    for rows in [[feedback(), feedback()], [feedback("")]]:
        with pytest.raises(ValueError):
            build_clusters(rows)


def test_exact_pain_and_determinism():
    rows = [feedback(core_user_pain="长文本具体痛点完全相同"), feedback("B", core_user_pain="长文本具体痛点完全相同")]
    assert build_clusters(rows) == build_clusters(list(reversed(rows)))
    assert len(build_clusters(rows)) == 1


def test_formula_safety_json_preserves_original(tmp_path):
    rows = [feedback(comment="=HYPERLINK(\"bad\")")]
    export_package(package_results(rows, build_clusters(rows)), tmp_path / "out")
    assert pd.read_csv(tmp_path / "out/feedback_mapping.csv").iloc[0]["comment"].startswith("'=")
    saved = json.loads((tmp_path / "out/issue_clusters.json").read_text(encoding="utf-8"))
    assert saved["feedback_mapping"][0]["comment"] == rows[0]["comment"]


def test_real_v3_source_no_gold_leakage():
    root = Path(__file__).resolve().parents[1]
    paths = [root / f"evaluation/results/{run}/evaluation_detail.xlsx" for run in ["v3", "v3_holdout"]]
    rows = load_feedback(paths)
    package = package_results(rows, build_clusters(rows))
    assert len(rows) == 82
    assert package["summary"]["eligible_count"] == 63
    assert package["summary"]["coarse_group_count"] == 24
    assert all(not any(k.startswith("gold_") for k in r) for r in rows)
    assert all(r["scenario_source"] == "not_saved" and r["user_scenario"] == "" for r in rows)


def test_loader_uses_prediction_only(monkeypatch):
    frame = pd.DataFrame([{
        "ID": "A", "产品": "飞书", "评论原文": "通知收不到", "分析来源": "api", "prompt_version": "v3",
        "pred_是否为有效产品反馈": True, "pred_反馈类型": "Bug", "pred_产品模块": "通知",
        "pred_核心痛点": "通知收不到", "pred_严重程度": "高", "gold_产品模块": "其他",
    }])
    monkeypatch.setattr(pd, "read_excel", lambda _: frame)
    assert load_feedback(["unused.xlsx"])[0]["product_module"] == "通知"
    frame.loc[0, "分析来源"] = "mock"
    with pytest.raises(ValueError):
        load_feedback(["unused.xlsx"])
