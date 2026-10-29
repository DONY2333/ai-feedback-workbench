"""手动运行的单条 API 连通性检查；pytest 收集时不会发送请求。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.model_client import FeedbackModelClient, ModelConfig


def main() -> int:
    config = ModelConfig.from_environment("api", allow_mock_fallback=False)

    def redact(value: object) -> str:
        text = str(value)
        return text.replace(config.api_key, "[REDACTED]") if config.api_key else text

    try:
        if not config.api_key:
            raise ValueError("未配置 OPENAI_API_KEY")

        from openai import OpenAI

        client = FeedbackModelClient(config)
        # 关闭 SDK 自动重试，确保此次检查最多发送一个请求。
        with OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=0,
            timeout=60.0,
        ) as api_client:
            client._client = api_client
            result, source = client.analyze(
                "手机经常收不到新消息提醒，要打开App后才看到。",
                "飞书",
            )

        if source != "api":
            raise RuntimeError(f"分析来源不是 api：{source}")

        print("请求成功")
        print(f"模型名称：{redact(config.model)}")
        print(f"分析来源：{source}")
        print(redact(json.dumps(result, ensure_ascii=False, indent=2)))
        return 0
    except Exception as exc:
        # 现有客户端会包装异常，沿 cause 链寻找原始 HTTP 错误。
        original = exc
        status_code = None
        current: BaseException | None = exc
        while current is not None:
            original = current
            if status_code is None:
                status_code = getattr(current, "status_code", None)
            current = current.__cause__

        print("请求失败")
        print(f"HTTP 状态码：{status_code if status_code is not None else '无法获取'}")
        print(f"异常类型：{type(original).__name__}")
        print(f"错误信息：{redact(original)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
