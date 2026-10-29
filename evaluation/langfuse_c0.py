"""C0：Dataset L001 × 三份历史快照；仅三次模型请求，不运行整个 Dataset。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.langfuse_dataset import DATASET_NAME, build_items, check_existing
from evaluation.langfuse_one import TIMEOUT_SECONDS, load_environment
from evaluation.historical_processing import process_output, score_output, version_metadata
from evaluation.run_evaluation import load_gold_set
from src.model_client import FeedbackModelClient, ModelConfig, parse_json_object

SOURCES = {
    "v1": "prompt_v1_baseline.md", "v2": "prompt_v2.md", "v3": "prompt_v3.md",
}
RECEIPT = PROJECT_ROOT / "evaluation/results/langfuse_phase_c0/receipt.json"
USER_TEMPLATE = 'f"产品：{product or \'未知\'}\\n评论原文：{comment}"'


@dataclass(frozen=True)
class Snapshot:
    version: str
    source: str
    system: str
    user_template: str
    schema: dict
    sha256: str


def load_snapshot(version: str) -> Snapshot:
    path = PROJECT_ROOT / "evaluation/prompts" / SOURCES[version]
    text = path.read_text(encoding="utf-8")
    def block(language):
        matches = re.findall(rf"```{language}\n(.*?)\n```", text, flags=re.DOTALL)
        if len(matches) != 1:
            raise ValueError(f"{version} 快照代码块缺失或不唯一")
        return matches[0]
    template = block("python")
    if template != USER_TEMPLATE:
        raise ValueError("历史 User Prompt 模板发生变化；拒绝静默使用当前模板")
    schema = json.loads(block("json"))
    return Snapshot(version, str(path.relative_to(PROJECT_ROOT)), block("text"),
                    template, schema, hashlib.sha256(path.read_bytes()).hexdigest())


def validate_raw(raw: dict, snapshot: Snapshot) -> None:
    """这三份历史 Schema 只有固定字段、基础类型和枚举；解析失败即停止。"""
    schema = snapshot.schema["schema"]
    if set(raw) != set(schema["required"]):
        raise ValueError("模型 JSON 缺少字段或包含多余字段")
    for key, spec in schema["properties"].items():
        required_type = bool if spec["type"] == "boolean" else str
        if not isinstance(raw[key], required_type):
            raise ValueError(f"模型字段类型错误：{key}")
        if "enum" in spec and raw[key] not in spec["enum"]:
            raise ValueError(f"模型字段不符合历史 Schema：{key}")


class HistoricalClient(FeedbackModelClient):
    """历史 Prompt 和版本后处理；禁止进入父类的现代 normalize_result。"""

    def __init__(self, config, api, snapshot):
        if config.mode != "api" or config.allow_mock_fallback or not config.api_key:
            raise ValueError("C0 必须使用真实 API 且禁止 fallback")
        if api.max_retries != 0:
            raise ValueError("C0 禁止模型重试")
        super().__init__(config)
        self._client = api
        self.snapshot = snapshot
        self.calls = 0
        self.raw_output = None
        self.processing = None

    def analyze(self, comment, product=""):
        try:
            self._call_api(comment, product)
            self.processing = process_output(self.raw_output, comment, self.snapshot.version)
            return self.processing["score_input"], "api"
        except Exception as exc:
            raise RuntimeError(f"历史版本 API 分析失败：{type(exc).__name__}") from exc

    def _call_api(self, comment, product):
        if self.calls:
            raise RuntimeError("每个版本仅允许一次请求")
        self.calls += 1
        response = self._client.chat.completions.create(
            model=self.config.model, temperature=0,
            response_format={"type": "json_schema", "json_schema": self.snapshot.schema},
            messages=[
                {"role": "system", "content": self.snapshot.system},
                {"role": "user", "content": f"产品：{product or '未知'}\n评论原文：{comment}"},
            ],
        )
        raw_text = response.choices[0].message.content or ""
        self.raw_output = parse_json_object(raw_text)
        validate_raw(self.raw_output, self.snapshot)
        return raw_text


def select_l001(langfuse):
    dev, _ = load_gold_set(split="dev")
    expected = build_items(dev)
    dataset = langfuse.get_dataset(DATASET_NAME)
    if check_existing(dataset, expected):
        raise ValueError("Dataset 缺少正式 Dev 项目")
    selected = [item for item in dataset.items if item.metadata.get("gold_id") == "L001"]
    if len(selected) != 1:
        raise ValueError("Dataset 必须且仅能包含一个 L001")
    # 输入来自服务端 Dataset，Gold 已与正式 Excel 全字段核验一致。
    return selected[0], dev.set_index("ID").loc["L001"].rename("L001")


def save_receipt(path: Path, receipt: dict) -> None:
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")


def run_c0(langfuse, config, api, path: Path = RECEIPT) -> dict:
    if path.exists():
        raise ValueError("C0 回执已存在：禁止重复模型调用；请使用 --verify-only")
    if config.model != "gpt-6.1-sol":
        raise ValueError("当前 OPENAI_MODEL 必须是 gpt-6.1-sol")
    snapshots = [load_snapshot(version) for version in SOURCES]
    if len({s.system for s in snapshots}) != 3:
        raise ValueError("三份 System Prompt 必须各不相同")
    # 所有客户端在请求前完成配置校验。
    clients = [HistoricalClient(config, api, snapshot) for snapshot in snapshots]
    item, gold = select_l001(langfuse)
    gold["ID"] = "L001"
    for version in SOURCES:
        existing = langfuse.api.experiments.list(
            from_start_time=datetime(1970, 1, 1, tzinfo=timezone.utc),
            name=f"feedback-dev-{version}", limit=100,
        )
        if existing.data:
            raise ValueError(f"feedback-dev-{version} 已存在；拒绝混入已有 Experiment")

    receipt = {"dataset": DATASET_NAME, "gold_id": "L001", "model_api_calls": 0,
               "started_at": datetime.now(timezone.utc).isoformat(), "runs": []}
    path.parent.mkdir(parents=True, exist_ok=True)
    # 排他创建防止不小心并发/重复执行；从此处失败也不允许自动重跑。
    with path.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2)
    for client in clients:  # 固定三个版本，绝不迭代 Dataset.items。
        snapshot = client.snapshot
        metadata = {
            **version_metadata(snapshot.version), "model": config.model, "gold_id": "L001",
            "dataset": DATASET_NAME, "run_name": f"feedback-dev-{snapshot.version}",
            "experiment_name": f"feedback-dev-{snapshot.version}", "split": "dev",
            "evaluation_source": "gold_set_82", "phase": "C0",
            "prompt_source": snapshot.source, "prompt_sha256": snapshot.sha256,
            "max_retries": 0, "timeout_seconds": TIMEOUT_SECONDS,
            "scoring_pipeline": "versioned normalization + historical exact match",
        }
        record = {**metadata, "status": "started"}
        receipt["runs"].append(record)
        save_receipt(path, receipt)
        def task(*, item, **kwargs):
            if item.metadata.get("gold_id") != "L001":
                raise ValueError("C0 仅允许 Dataset L001")
            with langfuse.start_as_current_observation(
                as_type="generation", name="comment-classification", model=config.model,
                metadata=metadata, input={**item.input, "messages": [
                    {"role": "system", "content": snapshot.system},
                    {"role": "user", "content": f"产品：{item.input['product'] or '未知'}\n评论原文：{item.input['comment']}"},
                ], "json_schema": snapshot.schema},
            ) as generation:
                record.update(trace_id=generation.trace_id, generation_id=generation.id)
                # 先持久化调用意图；崩溃后也不能以“没保存预测”为由重新请求。
                receipt["model_api_calls"] += 1
                save_receipt(path, receipt)
                try:
                    prediction, source = client.analyze(item.input["comment"], item.input["product"])
                except Exception as exc:
                    # SDK 会记录 task 异常；仅传递类型，避免日志泄露请求信息。
                    raise RuntimeError(type(exc).__name__) from None
                if source != "api":
                    raise RuntimeError("禁止 Mock 结果")
                scores = score_output(gold, prediction, snapshot.version)
                changes = {key: {"raw": value, "normalized": prediction[key]}
                           for key, value in client.raw_output.items() if value != prediction[key]}
                generation.update(output=client.raw_output,
                                  metadata={**metadata, "normalized_output": client.processing["normalized_output"],
                                            "score_input": client.processing["score_input"],
                                            "normalization_changes": changes})
                for name, value in scores.items():
                    generation.score(name=name, value=value, data_type="BOOLEAN", metadata=metadata)
                record.update(raw_output=client.raw_output,
                              normalized_output=client.processing["normalized_output"], score_input=prediction,
                              scores=scores, normalization_changes=changes)
                save_receipt(path, receipt)
            return client.raw_output

        try:
            # SDK 官方 Experiment 执行器负责实验上下文传播；data 明确只有 L001。
            # 4.17.0 内部会尝试旧 Run 链接，HTTP 410 时仍会用新 Experiment
            # observation 属性记录 dataset_id/item_id，不能只看旧 dataset_run_id。
            result = langfuse.run_experiment(
                name=metadata["run_name"], run_name=metadata["run_name"], data=[item],
                description="C0: L001 only; historical prompt/schema; Phase A Gold scoring.",
                task=task, evaluators=[], max_concurrency=1, metadata=metadata,
            )
            if len(result.item_results) != 1 or "scores" not in record:
                raise RuntimeError("单条 Experiment 失败；不重试模型")
            langfuse.flush()
            record.update(status="linked", dataset_item_id=item.id,
                          dataset_id=item.dataset_id, experiment_id=result.experiment_id)
        except Exception as exc:
            record.update(status="failed", error_type=type(exc).__name__)
            save_receipt(path, receipt)
            raise
        save_receipt(path, receipt)
    if receipt["model_api_calls"] != 3 or sum(c.calls for c in clients) != 3:
        raise RuntimeError("调用次数不等于 3")
    return receipt


def verify_runs(langfuse, receipt: dict) -> list[dict]:
    """仅回读；入库延迟时可以重复执行，绝不再次调用模型。"""
    def decode(value):
        # 新版云端 io 始终为 JSON 字符串，不能请求已移除的 parseIoAsJson。
        return json.loads(value) if isinstance(value, str) else value

    verified = []
    start = datetime.fromisoformat(receipt["started_at"]) - timedelta(minutes=1)
    for record in receipt["runs"]:
        # 只读旧 C0 回执仍按其原记录核验，不偷偷重算/覆盖旧服务端 Score。
        raw_output = record.get("raw_output", record.get("raw_prediction"))
        experiments = langfuse.api.experiments.list(
            from_start_time=start, id=record["experiment_id"], fields="core,metadata",
        )
        if len(experiments.data) != 1:
            raise ValueError("Experiment 尚不可见；只能只读重试核验")
        experiment = experiments.data[0]
        if (experiment.name != record["run_name"] or experiment.item_count != 1
                or experiment.dataset_id != record["dataset_id"]):
            raise ValueError("Experiment 名称、数量或 Dataset 关联错误")
        items = langfuse.api.experiments.list_items(
            from_start_time=start, experiment_id=record["experiment_id"],
            fields="core,dataset,io", limit=100,
        )
        if len(items.data) != 1:
            raise ValueError("Experiment 必须只有一个 Item")
        link = items.data[0]
        if (link.experiment_item_id != record["dataset_item_id"]
                or link.trace_id != record["trace_id"] or decode(link.output) != raw_output):
            raise ValueError("Experiment Item/Trace/输出关联不匹配")
        observations = langfuse.api.observations.get_many(
            trace_id=record["trace_id"], type="GENERATION", from_start_time=start,
            to_start_time=datetime.now(timezone.utc) + timedelta(minutes=1),
            fields="core,basic,io,metadata,model",
        )
        matches = [o for o in observations.data if o.id == record["generation_id"]]
        if len(matches) != 1:
            raise ValueError("Generation 尚不可见；只能只读重试核验")
        obs = matches[0]
        snapshot = load_snapshot(record["prompt_version"])
        request = decode(obs.input)
        if (decode(obs.output) != raw_output or obs.model != record["model"]
                or obs.metadata.get("prompt_sha256") != record["prompt_sha256"]
                or obs.metadata.get("prompt_version") != record["prompt_version"]
                or request["messages"][0]["content"] != snapshot.system
                or request["json_schema"] != snapshot.schema):
            raise ValueError("Generation 输出、模型或快照元数据核验不一致")
        scores = langfuse.api.scores_v3.get_many_v3(
            trace_id=record["trace_id"], observation_id=record["generation_id"], limit=100,
        )
        actual = {s.name: s.value for s in scores.data}
        if actual != record["scores"] or len(scores.data) != len(actual):
            raise ValueError("Score 尚未完整可见或与本地不一致")
        verified.append({"run_name": experiment.name, "experiment_id": experiment.id, "items": 1,
                         "generation_verified": True, "scores": actual})
    if len(verified) != 3:
        raise ValueError("必须核验三个 Run")
    return verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--langfuse-env", type=Path)
    parser.add_argument("--verify-only", action="store_true", help="只回读既有回执，不构造模型客户端")
    args = parser.parse_args()
    load_environment(args.langfuse_env)
    if not all(os.getenv(k) for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")):
        raise ValueError("缺少 Langfuse 配置")
    from langfuse import get_client
    langfuse = get_client()
    try:
        if args.verify_only:
            receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        else:
            from openai import OpenAI
            config = ModelConfig.from_environment("api", allow_mock_fallback=False)
            if not config.api_key:
                raise ValueError("未配置模型 API Key")
            with OpenAI(api_key=config.api_key, base_url=config.base_url,
                        max_retries=0, timeout=TIMEOUT_SECONDS) as api:
                receipt = run_c0(langfuse, config, api)
        langfuse.flush()
        receipt["server_verification"] = verify_runs(langfuse, receipt)
        save_receipt(RECEIPT, receipt)
        print(json.dumps({"model_api_calls": receipt["model_api_calls"],
                          "verified_runs": receipt["server_verification"]}, ensure_ascii=False, indent=2))
    finally:
        langfuse.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"C0 停止：{type(exc).__name__}。不自动重试模型；已有回执仅使用 --verify-only 核验。", file=sys.stderr)
        raise SystemExit(1) from None
