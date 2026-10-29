"""Dataset 离线测试：不连接 Langfuse 或模型。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langfuse.api import NotFoundError

from evaluation.langfuse_dataset import DATASET_NAME, build_items, upload_dev
from evaluation.run_evaluation import load_gold_set


@pytest.fixture
def dev():
    data, _ = load_gold_set(split="dev")
    return data


def remote(items):
    return SimpleNamespace(id="dataset-test", items=[
        SimpleNamespace(**item, status="ACTIVE") for item in items
    ])


def test_mapping_and_selection(dev):
    items = build_items(dev.sample(frac=1, random_state=4))
    assert len(items) == 20
    assert [x["metadata"]["gold_id"] for x in items] == [f"L{i:03d}" for i in range(1, 21)]
    for item, row in zip(items, dev.to_dict("records")):
        assert item["input"] == {"product": row["产品"], "comment": row["评论原文"]}
        assert set(item["expected_output"]) == {"是否为有效产品反馈", "反馈类型", "产品模块", "核心痛点", "严重程度"}
        assert all(value == row[f"gold_{field}"] for field, value in item["expected_output"].items())
        assert item["metadata"] == {
            "gold_id": row["ID"], "split": "dev", "evaluation_source": "gold_set_82",
            "excel_row": row["原始Excel行号"],
        }
    assert len({x["id"] for x in items}) == 20
    assert items == build_items(dev)


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "unexpected", "blank_gold", "blank_product", "missing_column"])
def test_invalid_data_fails_before_network(dev, invalid):
    if invalid == "missing":
        dev = dev.iloc[:-1]
    elif invalid == "duplicate":
        dev.loc[1, "ID"] = "L001"
    elif invalid == "unexpected":
        dev.loc[1, "ID"] = "W001"
    elif invalid == "blank_gold":
        dev.loc[1, "gold_核心痛点"] = None
    elif invalid == "blank_product":
        dev.loc[1, "产品"] = " "
    else:
        dev = dev.drop(columns="gold_严重程度")
    client = Mock()
    with pytest.raises((ValueError, RuntimeError)):
        upload_dev(client, dev)
    assert client.mock_calls == []


def test_existing_dataset_is_noop(dev):
    client = Mock()
    client.get_dataset.return_value = remote(build_items(dev))
    result = upload_dev(client, dev)
    assert result["written"] == 0
    assert result["verified_count"] == 20
    client.create_dataset.assert_not_called()
    client.create_dataset_item.assert_not_called()
    client.api.dataset_items.get.assert_not_called()


def test_create_and_verify(dev):
    client = Mock()
    client.get_dataset.side_effect = [NotFoundError(body="not found"), remote(build_items(dev))]
    client.api.dataset_items.get.side_effect = NotFoundError(body="not found")
    result = upload_dev(client, dev)
    assert result["written"] == 20
    assert result["model_api_calls"] == 0
    assert client.create_dataset.call_args.kwargs["name"] == DATASET_NAME
    assert client.create_dataset_item.call_count == 20


def test_partial_dataset_only_fills_missing(dev):
    items = build_items(dev)
    client = Mock()
    client.get_dataset.side_effect = [remote(items[:19]), remote(items)]
    client.api.dataset_items.get.side_effect = NotFoundError(body="not found")
    assert upload_dev(client, dev)["written"] == 1
    client.create_dataset.assert_not_called()
    assert client.create_dataset_item.call_args.kwargs["metadata"]["gold_id"] == "L020"


@pytest.mark.parametrize("conflict", ["duplicate", "unexpected", "input", "gold", "metadata", "archived"])
def test_remote_conflicts_fail_before_writes(dev, conflict):
    dataset = remote(build_items(dev))
    item = dataset.items[0]
    if conflict == "duplicate":
        dataset.items.append(item)
    elif conflict == "unexpected":
        item.metadata["gold_id"] = "L021"
    elif conflict == "input":
        item.input = {}
    elif conflict == "gold":
        item.expected_output = {}
    elif conflict == "metadata":
        item.metadata["split"] = "holdout"
    else:
        item.status = "ARCHIVED"
    client = Mock()
    client.get_dataset.return_value = dataset
    with pytest.raises(ValueError):
        upload_dev(client, dev)
    client.create_dataset.assert_not_called()
    client.create_dataset_item.assert_not_called()


def test_network_error_does_not_create_dataset(dev):
    client = Mock()
    client.get_dataset.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError, match="offline"):
        upload_dev(client, dev)
    client.create_dataset.assert_not_called()


def test_global_item_id_collision_does_not_overwrite(dev):
    client = Mock()
    client.get_dataset.side_effect = NotFoundError(body="not found")
    with pytest.raises(ValueError, match="upsert"):
        upload_dev(client, dev)
    client.create_dataset.assert_not_called()
    client.create_dataset_item.assert_not_called()


def test_no_model_or_evaluation_calls(dev, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Dataset 路径不允许调用模型或计算评测")
    monkeypatch.setattr("src.model_client.FeedbackModelClient.analyze", forbidden)
    monkeypatch.setattr("evaluation.run_evaluation.run_evaluation", forbidden)
    monkeypatch.setattr("evaluation.langfuse_one.analyze_detail", forbidden)
    client = Mock()
    client.get_dataset.return_value = remote(build_items(dev))
    assert upload_dev(client, dev)["verified_count"] == 20
