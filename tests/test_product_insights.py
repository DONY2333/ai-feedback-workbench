"""最小 E2 离线回归：统计、证据链、输出及源文件保护。"""

from hashlib import sha256
import json
from pathlib import Path

import pandas as pd
import pytest

from analysis.build_product_insights import build, load_e1, product_comparison, top_cards

SOURCE = Path(__file__).resolve().parents[1] / "analysis/results/e1_offline"


def test_comparison_totals_and_exclusions():
    package, _ = load_e1(SOURCE)
    result = product_comparison(package["feedback_mapping"])
    assert result.issue_feedback_count.sum() == 63
    assert result.groupby("product").issue_feedback_count.sum().to_dict() == {"飞书/Lark": 31, "企业微信/WeCom": 32}
    assert result.groupby("product").percentage.sum().to_dict() == pytest.approx({"飞书/Lark": 100, "企业微信/WeCom": 100})
    assert (result.bug_count + result.usage_difficulty_count + result.feature_request_count).sum() == 63
    assert result.bug_count.sum() == 45
    assert result.usage_difficulty_count.sum() == 12
    assert result.feature_request_count.sum() == 6
    extras = [dict(r, feedback_id=f"extra-{n}", valid=True, feedback_type="正向评价") for n, r in enumerate(package["feedback_mapping"][:2])]
    assert product_comparison(package["feedback_mapping"] + extras).equals(result)


def test_top_five_membership_evidence_and_boundaries():
    package, _ = load_e1(SOURCE)
    issues = {i["issue_id"]: i for i in package["issues"]}
    lookup = {r["feedback_id"]: r for r in package["feedback_mapping"]}
    cards = top_cards(package)
    assert len(cards) == len({c["issue_id"] for c in cards}) == 5
    assert sum(c["evidence_count"] >= 2 for c in cards) == 4
    assert {i["issue_id"] for i in package["issues"] if i["evidence_count"] >= 2}.issubset({c["issue_id"] for c in cards})
    for card in cards:
        assert card["evidence_ids"] == issues[card["issue_id"]]["evidence_ids"]
        assert card["evidence_count"] == len(card["evidence_ids"]) == len(set(card["evidence_ids"]))
        assert all(i in lookup for i in card["evidence_ids"])
        assert {(lookup[i]["product"], lookup[i]["product_module"]) for i in card["evidence_ids"]} == {(card["product"], card["product_module"])}
        assert 1 <= len(card["representative_comments"]) <= 3
        for rep in card["representative_comments"]:
            assert rep["feedback_id"] in card["evidence_ids"]
            assert rep["comment"] == lookup[rep["feedback_id"]]["comment"]
        assert card["priority"] in {"高", "中", "低"}
        assert 1 <= len(card["proposed_solution"]) <= 3
        assert 2 <= len(card["validation_metrics"]) <= 4


def test_outputs_and_source_unchanged(tmp_path):
    _, before = load_e1(SOURCE)
    target = tmp_path / "report"
    build(SOURCE, target)
    top = pd.read_csv(target / "top_issues.csv")
    comparison = pd.read_csv(target / "product_comparison.csv")
    evidence = pd.read_csv(target / "evidence.csv")
    assert len(top) == 5
    assert comparison.issue_feedback_count.sum() == 63
    assert len(evidence) == top.evidence_count.sum() == 14
    report = (target / "product_insights.md").read_text(encoding="utf-8")
    for header in ["Executive Summary", "Dataset Scope", "飞书/Lark Problem Profile", "企业微信/WeCom Problem Profile", "Competitor Comparison", "Top 5 Issues", "Product Recommendations", "Limitations"]:
        assert header in report
    audit = json.loads((target / "review_audit.json").read_text(encoding="utf-8"))
    assert audit["model_api_calls"] == 0
    assert sum(i["e2_review_status"] == "evidence_checked_by_assistant" for i in audit["issue_review"]) == 5
    assert all(sha256((SOURCE / name).read_bytes()).hexdigest() == digest for name, digest in before.items())
    with pytest.raises(FileExistsError):
        build(SOURCE, target)


def test_invalid_e1_fails_before_output(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ["issue_clusters.json", "issue_clusters.csv", "feedback_mapping.csv", "summary.csv", "review.json"]:
        (source / name).write_bytes((SOURCE / name).read_bytes())
    data = json.loads((source / "issue_clusters.json").read_text(encoding="utf-8"))
    data["issues"][0]["evidence_count"] += 1
    (source / "issue_clusters.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        build(source, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()
