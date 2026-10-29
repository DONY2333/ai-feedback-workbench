"""C2：60 个逻辑结果；每条最多两次显式请求，系统性错误立即停止。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.langfuse_c0 import HistoricalClient, SOURCES, load_snapshot, save_receipt
from evaluation.langfuse_dataset import DATASET_NAME, build_items, check_existing
from evaluation.langfuse_one import load_environment
from evaluation.historical_processing import score_output, version_metadata
from evaluation.replay_history import RUNS, replay_version
from evaluation.run_evaluation import load_gold_set, split_ids
from src.model_client import ModelConfig

OUTPUT = PROJECT_ROOT / "evaluation/results/langfuse_phase_c2"
RECEIPT = OUTPUT / "receipt.json"
EXPERIMENTS = {v: f"feedback-dev-{v}-final" for v in SOURCES}
SCORES = ["valid_correct", "feedback_type_correct", "product_module_correct", "severity_correct", "all_fields_correct"]
TIMEOUT_SECONDS = 45.0
MAX_REQUESTS = 120  # 60 个 item × 最多 2 次；SDK max_retries 始终为 0。


def error_info(exc):
    """只保存类型/状态码，绝不保存可能含凭据的异常正文。未知错误保守停止。"""
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    root = chain[-1]
    status = next((getattr(e, "status_code") for e in chain if isinstance(getattr(e, "status_code", None), int)), None)
    auth = status in (401, 403) or any(type(e).__name__ == "AuthenticationError" for e in chain)
    transient = not auth and (status == 429 or status is not None and 500 <= status <= 599
                             or any(isinstance(e, TimeoutError) or type(e).__name__ == "APITimeoutError" for e in chain))
    return {"error_type": type(root).__name__, "http_status": status,
            "error_category": "transient" if transient else "systemic"}


def run_settings(revision=None):
    """新轮次独立命名和存档，不复用或覆盖旧回执。"""
    if revision is None:
        return EXPERIMENTS.copy(), RECEIPT
    if not re.fullmatch(r"r[1-9][0-9]*", revision):
        raise ValueError("revision 必须形如 r2")
    return {v: f"{name}-{revision}" for v, name in EXPERIMENTS.items()}, OUTPUT / revision / "receipt.json"


class MeteredAPI:
    """不改变请求参数：仅在真实请求前持久化预算，捕获耗时和 API 自报 token。"""

    def __init__(self, api, journal, record, path):
        self.max_retries = api.max_retries
        self.api, self.journal, self.record, self.path = api, journal, record, path
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        if self.record.get("attempted"):
            raise RuntimeError("同一 item 不允许再次调度请求")
        self.record["attempted"] = True
        self.record.update(attempt_count=0, attempts=[], retry_reason=None, api_latency_seconds=0.0,
                           usage_details=None, usage_source="unavailable")
        for number in (1, 2):
            if self.journal["model_api_calls"] >= MAX_REQUESTS:
                raise RuntimeError("超出 120 次全局请求上限")
            attempt = {"attempt": number, "status": "started", "usage_details": None}
            self.record["attempts"].append(attempt)
            self.record["attempt_count"] = number
            self.journal["model_api_calls"] += 1
            save_receipt(self.path, self.journal)
            start = time.perf_counter()
            try:
                response = self.api.chat.completions.create(**kwargs)
                usage = getattr(response, "usage", None)
                counts = {name: getattr(usage, attr, None) for name, attr in (
                    ("input", "prompt_tokens"), ("output", "completion_tokens"), ("total", "total_tokens"),
                )}
                reliable = all(type(v) is int and v >= 0 for v in counts.values())
                reliable = reliable and counts["input"] + counts["output"] == counts["total"]
                attempt.update(status="completed", usage_details=counts if reliable else None)
                self.record["usage_details"] = attempt["usage_details"]
                self.record["usage_source"] = "model_api_response" if reliable else "unavailable_or_inconsistent"
                self.record["response_id"] = getattr(response, "id", None)
                return response
            except Exception as exc:
                info = error_info(exc)
                attempt.update(status="failed", **info)
                self.record.update(api_error_type=info["error_type"], http_status=info["http_status"])
                if info["error_category"] != "transient" or number == 2:
                    raise
                self.record["retry_reason"] = info.copy()
            finally:
                attempt["latency_seconds"] = time.perf_counter() - start
                self.record["api_latency_seconds"] += attempt["latency_seconds"]
                save_receipt(self.path, self.journal)


def prepare_dev(langfuse):
    gold, _ = load_gold_set(split="dev")
    dataset = langfuse.get_dataset(DATASET_NAME)
    if check_existing(dataset, build_items(gold)):
        raise ValueError("服务端 Dataset 缺少条目")
    by_id = {item.metadata["gold_id"]: item for item in dataset.items}
    if len(dataset.items) != 20 or set(by_id) != set(split_ids("dev")):
        raise ValueError("Dataset 必须严格为 L001-L020，无缺失/重复")
    return dataset, [by_id[k] for k in split_ids("dev")], gold.set_index("ID", drop=False)


def run_final(langfuse, config, api, path=RECEIPT, *, experiments=None):
    experiments = EXPERIMENTS if experiments is None else experiments
    if set(experiments) != set(SOURCES) or len(set(experiments.values())) != 3:
        raise ValueError("必须提供三个不同的版本 Experiment 名")
    if path.exists():
        raise ValueError("C2 回执已存在，禁止重复调用；只能 --verify-only")
    if config.model != "gpt-6.1-sol":
        raise ValueError("当前环境模型必须为 gpt-6.1-sol")
    snapshots = {v: load_snapshot(v) for v in SOURCES}
    if len({s.system for s in snapshots.values()}) != 3:
        raise ValueError("三个历史 Prompt 必须不同")
    for snapshot in snapshots.values():
        HistoricalClient(config, api, snapshot)  # 在任何调用前检查 api/fallback/retry 配置。
    dataset, items, gold = prepare_dev(langfuse)
    for name in experiments.values():
        existing = langfuse.api.experiments.list(
            from_start_time=datetime(1970, 1, 1, tzinfo=timezone.utc), name=name, limit=100,
        )
        if existing.data:
            raise ValueError(f"Experiment 已存在：{name}；拒绝复用")
    # 回放失败时必须在任何真实模型请求前停止。
    history = {v: replay_version(v, PROJECT_ROOT / "evaluation/results" / RUNS[v]) for v in SOURCES}
    journal = {"phase": "C2", "dataset": DATASET_NAME, "dataset_id": dataset.id,
               "started_at": datetime.now(timezone.utc).isoformat(), "model_api_calls": 0,
               "model": config.model, "max_retries": 0, "timeout_seconds": TIMEOUT_SECONDS,
               "max_attempts_per_item": 2, "max_model_requests": MAX_REQUESTS,
               "historical_replay": history, "runs": [], "status": "started"}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(journal, handle, ensure_ascii=False, indent=2)

    for version, snapshot in snapshots.items():
        metadata = {**version_metadata(version), "experiment": experiments[version],
                    "model": config.model, "dataset": DATASET_NAME, "split": "dev",
                    "evaluation_source": "gold_set_82", "phase": "C2",
                    "prompt_source": snapshot.source, "prompt_sha256": snapshot.sha256,
                    "max_retries": 0, "timeout_seconds": TIMEOUT_SECONDS}
        run = {**metadata, "records": [], "status": "started"}
        journal["runs"].append(run)
        seen, failed = set(), [False]

        def task(*, item, **kwargs):
            # 只有系统性错误闩锁；耗尽显式重试的临时失败保留结果后继续。
            if failed[0]:
                raise RuntimeError("前一条已失败，禁止继续模型调用")
            gold_id = item.metadata.get("gold_id")
            if gold_id not in gold.index or gold_id in seen:
                failed[0] = True
                raise ValueError("Dataset ID 非法或重复")
            seen.add(gold_id)
            record = {**metadata, "gold_id": gold_id, "dataset_item_id": item.id,
                      "input": item.input, "expected_output": item.expected_output,
                      "status": "started", "final_status": "started", "attempt_count": 0,
                      "raw_output": None, "normalized_output": None, "score_input": None,
                      "scores": {}}
            run["records"].append(record)
            save_receipt(path, journal)
            try:
                with langfuse.start_as_current_observation(
                    as_type="generation", name="comment-classification", model=config.model,
                    metadata={**metadata, "gold_id": gold_id}, input={**item.input, "messages": [
                        {"role": "system", "content": snapshot.system},
                        {"role": "user", "content": f"产品：{item.input['product'] or '未知'}\n评论原文：{item.input['comment']}"},
                    ], "json_schema": snapshot.schema},
                ) as generation:
                    record.update(trace_id=generation.trace_id, generation_id=generation.id)
                    client = HistoricalClient(config, MeteredAPI(api, journal, record, path), snapshot)
                    prediction, source = client.analyze(item.input["comment"], item.input["product"])
                    if source != "api":
                        raise RuntimeError("禁止非 API 预测")
                    record.update({key: client.processing[key] for key in ("raw_output", "normalized_output", "score_input")})
                    record["scores"] = score_output(gold.loc[gold_id], prediction, version)
                    record.update(final_status="completed", result_output=record["raw_output"])
                    save_receipt(path, journal)
                    generation.update(
                        output=record["raw_output"], usage_details=record["usage_details"],
                        metadata={**metadata, "gold_id": gold_id, "raw_output": record["raw_output"],
                                  "normalized_output": record["normalized_output"], "score_input": record["score_input"],
                                  "api_latency_seconds": record["api_latency_seconds"], "usage_source": record["usage_source"],
                                  "attempt_count": record["attempt_count"], "attempts": record["attempts"],
                                  "retry_reason": record["retry_reason"], "final_status": "completed"},
                    )
                    for name, value in record["scores"].items():
                        generation.score(name=name, value=value, data_type="BOOLEAN", metadata={**metadata, "gold_id": gold_id})
                record["status"] = "completed"
                save_receipt(path, journal)
                print(f"{version} {gold_id}: completed; attempts={record['attempt_count']}, requests={journal['model_api_calls']}", flush=True)
                return record["raw_output"]
            except Exception as exc:
                info = error_info(exc)
                failed[0] = info["error_category"] != "transient"
                record.update(status="failed", final_status="failed", **info)
                record["result_output"] = {"error": info, "gold_id": gold_id, "final_status": "failed"}
                if failed[0]:
                    journal["status"] = "failed"
                if "generation_id" in record:
                    generation.update(output=record["result_output"], level="ERROR",
                                      metadata={**metadata, "gold_id": gold_id, "raw_output": record["raw_output"],
                                                "normalized_output": record["normalized_output"], "score_input": record["score_input"],
                                                "final_status": "failed", "attempt_count": record["attempt_count"],
                                                "attempts": record.get("attempts", []), "retry_reason": record.get("retry_reason"),
                                                "failure": info, "api_latency_seconds": record.get("api_latency_seconds")})
                save_receipt(path, journal)
                if failed[0]:
                    raise RuntimeError(f"C2 {version}/{gold_id} stopped: {info['error_type']}") from None
                print(f"{version} {gold_id}: failed after 2 attempts; continuing", flush=True)
                return record["result_output"]

        result = langfuse.run_experiment(
            name=experiments[version], run_name=experiments[version], data=items,
            description="C2 formal Dev 20; historical prompt/schema and C1 versioned processing.",
            task=task, evaluators=[], max_concurrency=1, metadata=metadata,
        )
        run["experiment_id"] = result.experiment_id
        langfuse.flush()
        if (failed[0] or len(result.item_results) != 20 or seen != set(split_ids("dev"))
                or len(run["records"]) != 20):
            run["status"] = "failed"
            journal["status"] = "failed"
            save_receipt(path, journal)
            raise RuntimeError("Experiment 未完整成功；已停止，不重试/补跑")
        run["status"] = "completed_with_failures" if any(r["status"] == "failed" for r in run["records"]) else "completed"
        save_receipt(path, journal)
    if not 60 <= journal["model_api_calls"] <= MAX_REQUESTS:
        raise RuntimeError("请求计数必须在 60 到 120 之间")
    journal["status"] = "model_calls_completed"
    save_receipt(path, journal)
    return journal


def summarize(journal):
    summary = []
    historical_fields = dict(zip(SCORES[:4], ["是否为有效产品反馈", "反馈类型", "产品模块", "严重程度"]))
    for run in journal["runs"]:
        records = run["records"]
        metrics = {}
        for name in SCORES:
            values = [r["scores"][name] for r in records if name in r["scores"]]
            metrics[name] = {"correct": sum(values), "total": len(values),
                             "accuracy": sum(values) / len(values) if values else None}
        usage = [r["usage_details"] for r in records if r.get("usage_details") is not None]
        attempts = [a for r in records for a in r.get("attempts", [])]
        history = journal["historical_replay"][run["prompt_version"]]["summary"]
        historical = {name: next(row["accuracy"] for row in history if row["field"] == field)
                      for name, field in historical_fields.items()}
        summary.append({"experiment": run["experiment"], "experiment_id": run["experiment_id"],
                        "prompt_version": run["prompt_version"], "samples": len(records), "metrics": metrics,
                        "successful": sum(r["status"] == "completed" for r in records),
                        "failed": sum(r["status"] == "failed" for r in records),
                        "model_requests": len(attempts), "retry_count": sum(max(r.get("attempt_count", 1) - 1, 0) for r in records),
                        "retries": [{"gold_id": r["gold_id"], "reason": r["retry_reason"]} for r in records if r.get("retry_reason")],
                        "failures": [{"gold_id": r["gold_id"], "error_type": r["error_type"], "http_status": r.get("http_status")} for r in records if r["status"] == "failed"],
                        "accuracy_denominator": "successful predictions only; report failures separately",
                        "mean_api_latency_seconds": sum(r["api_latency_seconds"] for r in records) / len(records),
                        "mean_attempt_latency_seconds": sum(a["latency_seconds"] for a in attempts) / len(attempts) if attempts else None,
                        "token_coverage": f"{len(usage)}/{len(records)}",
                        "tokens": {k: sum(u[k] for u in usage) for k in ("input", "output", "total")} if usage else None,
                        "historical_accuracy": historical})
    return summary


def verify_final(langfuse, journal, path=RECEIPT):
    """只回读，允许重复核验。每条与本地三层输出、Scores、Prompt/Schema 核对。"""
    def decode(value):
        return json.loads(value) if isinstance(value, str) else value
    start = datetime.fromisoformat(journal["started_at"]) - timedelta(minutes=1)
    verified = []
    if not 60 <= journal["model_api_calls"] <= MAX_REQUESTS or len(journal["runs"]) != 3:
        raise ValueError("本地 Experiment 不完整，不得宣称正式完成")
    if journal["model_api_calls"] != sum(r.get("attempt_count", 1) for run in journal["runs"] for r in run["records"]):
        raise ValueError("请求计数与逐条 attempt_count 不一致")
    for run in journal["runs"]:
        # SDK 返回的 experiment_id 可能是内部 ID，而云端列表使用另一 UUID。
        # 按事先确认唯一的新实验名回读，后续条目查询使用服务端 ID。
        experiments = langfuse.api.experiments.list(from_start_time=start, name=run["experiment"], fields="core,metadata")
        if len(experiments.data) != 1:
            raise ValueError("Experiment 尚未完整入库；只能只读核验")
        remote = experiments.data[0]
        if remote.name != run["experiment"] or remote.item_count != 20 or remote.dataset_id != journal["dataset_id"]:
            raise ValueError("Experiment 名称、数量或 Dataset 不匹配")
        run["server_experiment_id"] = remote.id
        response = langfuse.api.experiments.list_items(from_start_time=start, experiment_id=remote.id,
                                                       fields="core,dataset,io", limit=100)
        links = response.data
        expected = {r["dataset_item_id"]: r for r in run["records"]}
        if (len(links) != 20 or len(expected) != 20 or {x.experiment_item_id for x in links} != set(expected)
                or {r["gold_id"] for r in run["records"]} != set(split_ids("dev"))):
            raise ValueError("服务端 Item 缺失/重复/范围不符")
        snapshot = load_snapshot(run["prompt_version"])
        for link in links:
            record = expected[link.experiment_item_id]
            if (link.trace_id != record["trace_id"] or decode(link.input) != record["input"]
                    or decode(link.output) != record.get("result_output", record["raw_output"]) or decode(link.expected_output) != record["expected_output"]):
                raise ValueError("Item IO/Gold/Trace 关联错误")
            observations = langfuse.api.observations.get_many(
                trace_id=record["trace_id"], type="GENERATION", from_start_time=start,
                fields="core,basic,io,metadata,model,usage,time", limit=100,
            )
            matched = [o for o in observations.data if o.id == record["generation_id"]]
            if len(matched) != 1:
                raise ValueError("Generation 尚不可见或重复")
            obs = matched[0]
            request = decode(obs.input)
            keys = ("prompt_version", "normalization_version", "scoring_version", "normalization_reproduction", "gold_id", "experiment", "dataset")
            if (obs.model != record["model"] or decode(obs.output) != record.get("result_output", record["raw_output"])
                    or any(obs.metadata.get(k) != record[k] for k in keys)
                    or any(obs.metadata.get(k) != record[k] for k in ("raw_output", "normalized_output", "score_input"))
                    or request["messages"][0]["content"] != snapshot.system or request["json_schema"] != snapshot.schema):
                raise ValueError("Generation 模型/三层输出/版本/Prompt 核验失败")
            if record.get("attempt_count") is not None:
                if any(obs.metadata.get(k) != record[k] for k in ("attempt_count", "retry_reason", "final_status")):
                    raise ValueError("Generation 重试记录不一致")
            scores = langfuse.api.scores_v3.get_many_v3(trace_id=record["trace_id"], observation_id=record["generation_id"], limit=100)
            if len(scores.data) != len(record["scores"]) or {s.name: s.value for s in scores.data} != record["scores"]:
                raise ValueError("Score 未完整可见或不一致")
            record["server_verified"] = True
            save_receipt(path, journal)
        verified.append({"experiment": remote.name, "items": 20, "all_ids_and_generations_verified": True,
                         "successful": sum(r["status"] == "completed" for r in run["records"]),
                         "failed": sum(r["status"] == "failed" for r in run["records"])})
        print(f"Verified {remote.name}: 20 items", flush=True)
    journal["server_verification"] = verified
    journal["summary"] = summarize(journal)
    journal["status"] = "server_verified"
    save_receipt(path, journal)
    return journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--langfuse-env", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--revision", help="独立轮次，例如 r2；仅改变实验名和回执路径")
    args = parser.parse_args()
    experiments, receipt_path = run_settings(args.revision)
    load_environment(args.langfuse_env)
    if not all(os.getenv(k) for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")):
        raise ValueError("缺少 Langfuse 配置")
    from langfuse import get_client
    langfuse = get_client()
    try:
        if args.verify_only:
            journal = json.loads(receipt_path.read_text(encoding="utf-8"))
        else:
            from openai import OpenAI
            config = ModelConfig.from_environment("api", allow_mock_fallback=False)
            with OpenAI(api_key=config.api_key, base_url=config.base_url,
                        max_retries=0, timeout=TIMEOUT_SECONDS) as api:
                journal = run_final(langfuse, config, api, receipt_path, experiments=experiments)
        langfuse.flush()
        journal = verify_final(langfuse, journal, receipt_path)
        print(json.dumps({"model_api_calls": journal["model_api_calls"], "summary": journal["summary"]}, ensure_ascii=False, indent=2))
    finally:
        langfuse.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"C2 已停止：{type(exc).__name__}；禁止模型重试，检查回执后仅只读核验。", file=sys.stderr)
        raise SystemExit(1) from None
