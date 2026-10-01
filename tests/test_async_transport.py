from __future__ import annotations

import unittest
from unittest.mock import patch

import anyio
import httpx

from pkb_proto.async_transport import AsyncRequestTimeout, request_json
from pkb_proto.magi_async import call_guided_panel_async


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


if __name__ == "__main__":
    unittest.main()
