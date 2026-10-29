"""公开文档的链接、数据和功能边界检查；不连接外部服务。"""

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md"] + [ROOT / "docs" / name for name in (
    "project_case.md", "project_status.md", "publication_notes.md",
    "top1_notification_mini_prd.md", "top1_notification_figma_spec.md",
)]


def test_packaging_local_links_exist():
    for path in DOCS:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if not target.startswith(("https://", "http://", "#")):
                assert (path.parent / target.split("#")[0]).resolve().is_file(), (path, target)


def test_packaging_metrics_and_top1_evidence_match_saved_sources():
    with (ROOT / "evaluation/results/v3_holdout/analysis/metrics_overall.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    for name in ("README.md", "docs/project_case.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for row in rows:
            assert f"{float(row['accuracy']):.2%}" in text
            if row["macro_f1"]:
                assert f"{float(row['macro_f1']):.2%}" in text
        assert "37/62" in text
    audit = json.loads((ROOT / "analysis/results/e2_product_insights/review_audit.json").read_text(encoding="utf-8"))
    top = audit["top_issue_cards"][0]
    assert top["issue_id"] == "ISS-3955d4f6ed9d"
    assert set(top["evidence_ids"]) == {"L002", "L003", "L010", "L016", "L038"}
    for name in ("README.md", "docs/project_case.md", "docs/top1_notification_mini_prd.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert all(feedback_id in text for feedback_id in top["evidence_ids"])


def test_public_docs_describe_actual_demo_scope():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for required in ("Mock", "规则", "离线编辑判断", "新批次不会套用", "没有实际 Figma", "未上线"):
        assert required in readme
    for source in ("analysis/results/user_runs/<batch_id>/", "analysis/README.md", "docs/publication_notes.md"):
        assert source in readme
    for path in DOCS:
        text = path.read_text(encoding="utf-8")
        assert not any(phrase in text for phrase in ("本轮到此收尾", "为快速收尾", "不要添加用户画像", "部分结果被 Git 忽略"))
