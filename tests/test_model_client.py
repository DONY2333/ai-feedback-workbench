import pytest
from dotenv import load_dotenv

from src.model_client import FeedbackModelClient, ModelConfig, parse_json_object


def test_parse_json_from_markdown_and_explanation():
    raw = '分析如下：```json\n{"反馈类型": "Bug"}\n```谢谢'
    assert parse_json_object(raw)["反馈类型"] == "Bug"


def test_parse_invalid_json_raises():
    with pytest.raises(ValueError):
        parse_json_object("没有 JSON")


def test_mock_analysis_has_required_shape():
    client = FeedbackModelClient(ModelConfig(mode="mock"))
    result, source = client.analyze("小米手机一直收不到消息通知", "飞书")
    assert source == "mock"
    assert result["反馈类型"] == "Bug"
    assert result["产品模块"] == "通知"
    assert result["严重程度"] == "高"


def test_system_environment_has_priority_over_dotenv(tmp_path, monkeypatch):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("OPENAI_MODEL=model-from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_MODEL", "model-from-system")

    load_dotenv(dotenv_path=dotenv_path, override=False)
    config = ModelConfig.from_environment("mock")

    assert config.model == "model-from-system"

