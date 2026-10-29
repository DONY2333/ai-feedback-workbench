import pandas as pd

from src.data_loader import load_excel_comments


def test_load_excel_comments_removes_empty_and_duplicates(tmp_path):
    source = pd.DataFrame(
        {
            "产品": ["飞书", "飞书", "企业微信", "飞书"],
            "评论原文": ["收不到消息", " 收不到消息 ", None, "很好用"],
        }
    )
    with pd.ExcelWriter(tmp_path / "comments.xlsx") as writer:
        source.to_excel(writer, sheet_name="评论数据", index=False)
        pd.DataFrame({"飞书 vs 企业微信：公开评论第一批样本": ["采集口径说明"]}).to_excel(
            writer, sheet_name="说明", index=False
        )

    result, stats = load_excel_comments(tmp_path)

    assert len(result) == 2
    assert stats["空评论数"] == 1
    assert stats["重复评论数"] == 1
    assert stats["跳过工作表数"] == 1
