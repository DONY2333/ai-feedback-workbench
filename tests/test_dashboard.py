import pandas as pd

from src.dashboard import comments_for_issue, frequent_issues, product_comparison


def sample_data():
    return pd.DataFrame(
        [
            {"产品": "飞书", "反馈类型": "Bug", "核心痛点": "收不到通知", "严重程度": "高", "评论原文": "a"},
            {"产品": "飞书", "反馈类型": "Bug", "核心痛点": "收不到通知", "严重程度": "严重", "评论原文": "b"},
            {"产品": "企业微信", "反馈类型": "功能建议", "核心痛点": "缺少功能", "严重程度": "低", "评论原文": "c"},
        ]
    )


def test_frequent_issues_and_drill_down():
    data = sample_data()
    issues = frequent_issues(data)
    assert issues.iloc[0]["核心痛点"] == "收不到通知"
    assert issues.iloc[0]["评论数"] == 2
    assert len(comments_for_issue(data, "收不到通知")) == 2


def test_product_comparison_has_total():
    comparison = product_comparison(sample_data())
    assert comparison.loc["飞书", "总计"] == 2

