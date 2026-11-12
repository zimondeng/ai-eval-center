from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from app.adapters.component_harness import ComponentHarnessAdapter
from app.adapters.http_json import HttpJsonAdapter
from app.adapters.http_sse import HttpSseAdapter
from app.adapters.models import (
    ComponentRecordResult,
    SutAdapterConfig,
    SutExecutionError,
    SutExecutionInput,
    SutExecutionResult,
)


async def execute_sut_adapter(
    adapter: Any,
    case_input: Any,
    *,
    run_overrides: Mapping[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SutExecutionResult:
    config = SutAdapterConfig.from_adapter(adapter)
    exec_input = SutExecutionInput.from_case(case_input, run_overrides=run_overrides)
    adapter_type = config.adapter_type.upper()

    try:
        if adapter_type == "HTTP_JSON":
            return await HttpJsonAdapter(config).execute(exec_input, transport=transport)
        if adapter_type == "HTTP_SSE":
            return await HttpSseAdapter(config).execute(exec_input, transport=transport)
        if adapter_type == "COMPONENT_HARNESS":
            return await ComponentHarnessAdapter(config).replay(exec_input, transport=transport)
    except ValueError as exc:
        raise SutExecutionError(f"适配器配置或映射解析错误: {exc}") from exc

    raise SutExecutionError(f"不支持的被测适配器驱动类型: {config.adapter_type}")


async def record_component_snapshot(
    adapter: Any,
    record_input: Any,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ComponentRecordResult:
    config = SutAdapterConfig.from_adapter(adapter)
    try:
        return await ComponentHarnessAdapter(config).record(record_input, transport=transport)
    except ValueError as exc:
        raise SutExecutionError(f"适配器配置或映射解析错误: {exc}") from exc
