"""最终包装只读完整性检查；不创建模型或 Langfuse 客户端。"""

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md"] + [ROOT / "docs" / name for name in (
    "project_case.md", "resume_bullets.md", "interview_3min_pitch.md",
    "interview_deep_dive.md", "screenshot_checklist.md", "project_status.md",
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


def test_packaging_interview_and_resume_scope():
    deep = (ROOT / "docs/interview_deep_dive.md").read_text(encoding="utf-8")
    assert re.findall(r"^## (\d+)\.", deep, re.M) == [str(i) for i in range(1, 39)]
    shots = (ROOT / "docs/screenshot_checklist.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^## \d+\.", shots, re.M)) == 10
    pitch = (ROOT / "docs/interview_3min_pitch.md").read_text(encoding="utf-8")
    short = pitch.split("## 60-second version", 1)[1].strip()
    assert 180 <= len(re.findall(r"[\u4e00-\u9fff]", short)) <= 260
    resume = (ROOT / "docs/resume_bullets.md").read_text(encoding="utf-8")
    formal, compressed = resume.split("## A. 推荐正式版", 1)[1].split("## B. 一页简历压缩版")
    assert len(re.findall(r"^- ", formal, re.M)) == 4
    bullets = re.findall(r"^- (.+)", compressed.split("## 表述边界")[0], re.M)
    assert len(bullets) == 4 and all(len(line) <= 80 for line in bullets)
