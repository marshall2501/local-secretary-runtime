from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import anyio
import httpx

from pkb_proto.async_transport import (
    AsyncHTTPStatusError,
    AsyncRequestTimeout,
    AsyncRetryExhausted,
    request_json,
    request_json_with_retry,
)
from pkb_proto.magi_async import (
    _call_ollama_guided_async,
    call_guided_panel_async,
    start_dialogue_async,
)


class _SlowTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await anyio.sleep(0.2)
        return httpx.Response(200, json={"ok": True}, request=request)


class AsyncTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_overall_deadline_cancels_slow_http_request(self):
        async with httpx.AsyncClient(
            transport=_SlowTransport(), timeout=None
        ) as client:
            with self.assertRaises(AsyncRequestTimeout):
                await request_json(
                    "GET",
                    "https://example.invalid/slow",
                    timeout=0.02,
                    client=client,
                )

    async def test_http_error_retains_provider_status_and_message_only(self):
        class _ErrorTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "status": "INVALID_ARGUMENT",
                            "message": "Unsupported field: minLength",
                        }
                    },
                    request=request,
                )

        async with httpx.AsyncClient(
            transport=_ErrorTransport(), timeout=None
        ) as client:
            with self.assertRaises(AsyncHTTPStatusError) as caught:
                await request_json(
                    "POST",
                    "https://example.invalid/error",
                    json_body={"secret": "not echoed"},
                    timeout=1,
                    client=client,
                )
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.provider_status, "INVALID_ARGUMENT")
        self.assertEqual(
            caught.exception.provider_message,
            "Unsupported field: minLength",
        )


    async def test_retryable_503_retries_within_same_logical_request(self):
        class _SequenceTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.calls = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                self.calls += 1
                if self.calls < 3:
                    return httpx.Response(
                        503,
                        json={"error": {"status": "UNAVAILABLE", "message": "busy"}},
                        request=request,
                    )
                return httpx.Response(200, json={"ok": True}, request=request)

        transport = _SequenceTransport()
        async with httpx.AsyncClient(transport=transport, timeout=None) as client:
            with patch(
                "pkb_proto.async_transport.random.uniform",
                return_value=0.0,
            ), patch(
                "pkb_proto.async_transport.anyio.sleep",
                new=AsyncMock(return_value=None),
            ):
                result, diagnostic = await request_json_with_retry(
                    "POST",
                    "https://example.invalid/retry",
                    timeout=10,
                    retry_enabled=True,
                    retry_http_codes=[503],
                    client=client,
                )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(transport.calls, 3)
        self.assertEqual(diagnostic["attempt_count"], 3)
        self.assertEqual(diagnostic["retry_count"], 2)
        self.assertEqual(diagnostic["retry_http_codes_seen"], [503, 503])
        self.assertEqual(diagnostic["retry_budget_seconds"], 5.0)
        self.assertEqual(diagnostic["final_status"], "ok")

    async def test_retry_can_be_disabled_per_member(self):
        class _ErrorTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.calls = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                self.calls += 1
                return httpx.Response(
                    503,
                    json={"error": {"status": "UNAVAILABLE", "message": "busy"}},
                    request=request,
                )

        transport = _ErrorTransport()
        async with httpx.AsyncClient(transport=transport, timeout=None) as client:
            with self.assertRaises(AsyncRetryExhausted) as caught:
                await request_json_with_retry(
                    "POST",
                    "https://example.invalid/no-retry",
                    timeout=10,
                    retry_enabled=False,
                    retry_http_codes=[503],
                    client=client,
                )
        self.assertEqual(transport.calls, 1)
        self.assertEqual(caught.exception.diagnostic["attempt_count"], 1)
        self.assertEqual(caught.exception.diagnostic["retry_count"], 0)
        self.assertEqual(caught.exception.diagnostic["final_status"], "http_503")

    async def test_nonconfigured_http_code_is_not_retried(self):
        class _ErrorTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.calls = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                self.calls += 1
                return httpx.Response(
                    400,
                    json={"error": {"status": "INVALID_ARGUMENT", "message": "bad request"}},
                    request=request,
                )

        transport = _ErrorTransport()
        async with httpx.AsyncClient(transport=transport, timeout=None) as client:
            with self.assertRaises(AsyncRetryExhausted) as caught:
                await request_json_with_retry(
                    "POST",
                    "https://example.invalid/non-retryable",
                    timeout=10,
                    retry_enabled=True,
                    retry_http_codes=[429, 500, 502, 503, 504],
                    client=client,
                )
        self.assertEqual(transport.calls, 1)
        self.assertEqual(caught.exception.diagnostic["retry_http_codes_seen"], [400])
        self.assertEqual(caught.exception.diagnostic["final_status"], "http_400")

    async def test_parent_cancel_propagates_during_retry_wait(self):
        class _ErrorTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    503,
                    json={"error": {"status": "UNAVAILABLE", "message": "busy"}},
                    request=request,
                )

        async with httpx.AsyncClient(
            transport=_ErrorTransport(), timeout=None
        ) as client:
            task = asyncio.create_task(
                request_json_with_retry(
                    "GET",
                    "https://example.invalid/retry-cancel",
                    timeout=10,
                    retry_enabled=True,
                    retry_http_codes=[503],
                    client=client,
                )
            )
            await asyncio.sleep(0.02)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_parent_cancel_propagates_without_waiting_for_deadline(self):
        async with httpx.AsyncClient(
            transport=_SlowTransport(), timeout=None
        ) as client:
            task = asyncio.create_task(
                request_json(
                    "GET",
                    "https://example.invalid/cancel",
                    timeout=10,
                    client=client,
                )
            )
            await asyncio.sleep(0.02)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_ollama_profile_context_is_sent_as_num_ctx(self):
        response = {
            "message": {
                "content": (
                    '{"category":"INFORMATION",'
                    '"understood_request":"GPUを知りたい",'
                    '"reason":"情報照会",'
                    '"confidence":"high",'
                    '"multiple_requests":false}'
                )
            },
            "done_reason": "stop",
        }
        retry_diagnostic = {
            "retry_enabled": True,
            "attempt_count": 1,
            "retry_count": 0,
            "retry_http_codes_seen": [],
            "retry_wait_seconds": 0.0,
            "retry_budget_seconds": 0.5,
            "final_status": "ok",
        }
        mock = AsyncMock(return_value=(response, retry_diagnostic))
        with patch("pkb_proto.magi_async.request_json_with_retry", mock):
            result = await _call_ollama_guided_async(
                {"stage": "classify"},
                model="qwen3.5:9b",
                timeout=1,
                context_window_tokens=32768,
                ollama_num_predict=8192,
            )
        self.assertEqual(result["status"], "ok")
        payload = mock.await_args.kwargs["json_body"]
        self.assertEqual(payload["options"]["num_ctx"], 32768)
        self.assertEqual(payload["options"]["num_predict"], 8192)

    async def test_magi_ollama_num_predict_env_override_is_used(self):
        response = {
            "message": {
                "content": (
                    '{"category":"INFORMATION",'
                    '"understood_request":"GPUを知りたい",'
                    '"reason":"情報照会",'
                    '"confidence":"high",'
                    '"multiple_requests":false}'
                )
            },
            "done_reason": "stop",
        }
        retry_diagnostic = {
            "retry_enabled": True,
            "attempt_count": 1,
            "retry_count": 0,
            "retry_http_codes_seen": [],
            "retry_wait_seconds": 0.0,
            "retry_budget_seconds": 0.5,
            "final_status": "ok",
        }
        mock = AsyncMock(return_value=(response, retry_diagnostic))
        with patch.dict(
            "os.environ",
            {"LSA_MAGI_OLLAMA_NUM_PREDICT": "2048"},
            clear=False,
        ), patch("pkb_proto.magi_async.request_json_with_retry", mock):
            result = await _call_ollama_guided_async(
                {"stage": "classify"},
                model="qwen3.5:9b",
                timeout=1,
                context_window_tokens=65536,
            )
        self.assertEqual(result["status"], "ok")
        payload = mock.await_args.kwargs["json_body"]
        self.assertEqual(payload["options"]["num_predict"], 2048)
        self.assertEqual(result["diagnostic"]["num_predict"], 2048)

    async def test_panel_members_execute_concurrently(self):
        active = 0
        max_active = 0

        async def fake_member(spec, envelope, *, timeout):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await anyio.sleep(0.03)
                return {
                    "name": spec["name"],
                    "profile_id": spec.get("profile_id"),
                    "provider": spec["provider"],
                    "model": spec["model"],
                    "weight": spec["weight"],
                    "timeout_seconds": spec["timeout_seconds"],
                    "status": "ok",
                    "response": {
                        "category": "INFORMATION",
                        "understood_request": "公開情報を知りたい",
                        "reason": "情報照会",
                        "confidence": "high",
                        "multiple_requests": False,
                    },
                    "errors": [],
                    "diagnostic": {},
                }
            finally:
                active -= 1

        specs = [
            {
                "name": "MELCHIOR",
                "profile_id": "p1",
                "provider": "ollama",
                "model": "local-a",
                "weight": 1.0,
                "timeout_seconds": 1,
                "enabled": True,
            },
            {
                "name": "CASPER",
                "profile_id": "p2",
                "provider": "openai",
                "model": "cloud-b",
                "weight": 1.0,
                "timeout_seconds": 1,
                "enabled": True,
            },
            {
                "name": "BALTHASAR",
                "profile_id": "p3",
                "provider": "gemini",
                "model": "cloud-c",
                "weight": 1.0,
                "timeout_seconds": 1,
                "enabled": False,
            },
        ]
        envelope = {
            "stage": "classify",
            "task_id": "task-async",
            "turn": 1,
        }
        with patch(
            "pkb_proto.magi_async._call_panel_member_async",
            side_effect=fake_member,
        ):
            result = await call_guided_panel_async(
                envelope,
                member_specs=specs,
                timeout=1,
            )

        self.assertEqual(max_active, 2)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["diagnostic"]["mode"],
            "weighted_panel_async",
        )

    async def test_one_member_timeout_does_not_discard_other_valid_member(self):
        async def fake_member(spec, envelope, *, timeout):
            if spec["name"] == "CASPER":
                return {
                    "name": "CASPER",
                    "profile_id": spec.get("profile_id"),
                    "provider": "openai",
                    "model": spec["model"],
                    "weight": 1.0,
                    "timeout_seconds": 1,
                    "status": "unavailable",
                    "response": None,
                    "errors": ["timeout"],
                    "diagnostic": {"provider": "openai", "error": "timeout"},
                }
            return {
                "name": "MELCHIOR",
                "profile_id": spec.get("profile_id"),
                "provider": "ollama",
                "model": spec["model"],
                "weight": 1.0,
                "timeout_seconds": 1,
                "status": "ok",
                "response": {
                    "category": "INFORMATION",
                    "understood_request": "公開情報を知りたい",
                    "reason": "情報照会",
                    "confidence": "high",
                    "multiple_requests": False,
                },
                "errors": [],
                "diagnostic": {},
            }

        specs = [
            {
                "name": "MELCHIOR",
                "profile_id": "p1",
                "provider": "ollama",
                "model": "local-a",
                "weight": 1.0,
                "timeout_seconds": 1,
                "enabled": True,
            },
            {
                "name": "CASPER",
                "profile_id": "p2",
                "provider": "openai",
                "model": "cloud-b",
                "weight": 1.0,
                "timeout_seconds": 1,
                "enabled": True,
            },
            {
                "name": "BALTHASAR",
                "profile_id": "p3",
                "provider": "gemini",
                "model": "cloud-c",
                "weight": 1.0,
                "timeout_seconds": 1,
                "enabled": False,
            },
        ]
        with patch(
            "pkb_proto.magi_async._call_panel_member_async",
            side_effect=fake_member,
        ):
            result = await call_guided_panel_async(
                {"stage": "classify", "task_id": "task-async", "turn": 1},
                member_specs=specs,
                timeout=1,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["consensus"]["valid_members"], ["MELCHIOR"])
        by_name = {item["name"]: item for item in result["member_results"]}
        self.assertEqual(by_name["CASPER"]["errors"], ["timeout"])



class AsyncMagiDialogueTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_request_after_current_turn_prevents_next_turn(self):
        stop = {"requested": False}
        calls = []

        async def caller(envelope, *, model, timeout, member_specs):
            calls.append(envelope["turn"])
            stop["requested"] = True
            return {
                "status": "ok",
                "response": {
                    "category": "INFORMATION",
                    "understood_request": "富士山の高さを知りたい",
                    "reason": "情報照会",
                    "confidence": "high",
                    "multiple_requests": False,
                },
                "errors": [],
                "diagnostic": {"mode": "test"},
                "member_results": [],
                "consensus": None,
            }

        specs = [
            {
                "name": "MELCHIOR",
                "profile_id": "p1",
                "provider": "ollama",
                "model": "local-a",
                "weight": 1.0,
                "timeout_seconds": 1,
                "enabled": True,
            },
            {
                "name": "CASPER",
                "profile_id": "p2",
                "provider": "openai",
                "model": "cloud-b",
                "weight": 1.0,
                "timeout_seconds": 1,
                "enabled": False,
            },
            {
                "name": "BALTHASAR",
                "profile_id": "p3",
                "provider": "gemini",
                "model": "cloud-c",
                "weight": 1.0,
                "timeout_seconds": 1,
                "enabled": False,
            },
        ]
        session = await start_dialogue_async(
            "富士山の高さを教えて",
            member_specs=specs,
            timeout=1,
            caller=caller,
            stop_requested=lambda: stop["requested"],
        )
        self.assertEqual(calls, [1])
        self.assertEqual(len(session["turns"]), 1)
        self.assertEqual(session["status"], "stopped")
        self.assertEqual(session["next_step"], "user_requested_stop")


if __name__ == "__main__":
    unittest.main()
