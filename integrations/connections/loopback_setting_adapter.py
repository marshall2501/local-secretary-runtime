"""Only a fixed loopback fixture operation; NOT a generic HTTP proxy."""
from __future__ import annotations

import httpx

from infrastructure.async_runtime.transport import request_json


class LoopbackSettingAdapter:
    def __init__(self, port: int):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("invalid_fixture_port")
        self._url = f"http://127.0.0.1:{port}/value"

    async def write_and_readback(self, *, value: str, timeout: float) -> dict:
        # Do not trust HTTP_PROXY; do not follow off-loopback redirects.
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                     timeout=None) as client:
            write = await request_json(
                "PUT", self._url, json_body={"value": value},
                timeout=timeout, client=client,
            )
            if not isinstance(write, dict) or write.get("accepted") is not True:
                return {"write_ack": False, "observed": None}
            read = await request_json(
                "GET", self._url, timeout=timeout, client=client,
            )
            return {
                "write_ack": True,
                "observed": read.get("value") if isinstance(read, dict) else None,
            }
