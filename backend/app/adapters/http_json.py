from __future__ import annotations

import httpx

from app.adapters.mapper import execute_json_sut_request, render_sut_request
from app.adapters.models import (
    SutAdapterConfig,
    SutExecutionInput,
    SutExecutionResult,
)


class HttpJsonAdapter:
    def __init__(self, config: SutAdapterConfig) -> None:
        self.config = config

    async def execute(
        self,
        exec_input: SutExecutionInput,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> SutExecutionResult:
        rendered = render_sut_request(self.config, exec_input)
        return await execute_json_sut_request(
            self.config,
            rendered,
            target_label="被测系统 HTTP_JSON 接口",
            transport=transport,
        )
