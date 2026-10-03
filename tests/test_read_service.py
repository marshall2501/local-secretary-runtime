"""Offline tests for the transport-independent Secretary read service."""
import unittest
from uuid import UUID

from application.read_service import ReadService


class FakeReadRepository:
    def __init__(self):
        self.calls = []

    def list_entities(self, domain, limit):
        self.calls.append(("entities", domain, limit))
        return [{"name": "PC"}]

    def list_current_claims(self, entity_id, verified_only, limit):
        self.calls.append(("claims", entity_id, verified_only, limit))
        return [{"predicate": "gpu"}]

    def list_tasks(self, task_status, limit):
        self.calls.append(("tasks", task_status, limit))
        return [{"status": task_status or "pending"}]

    def search_memory(self, q, domain, kind, include_history, limit, offset):
        self.calls.append(("memory", q, domain, kind, include_history, limit, offset))
        return 1, [{"kind": kind or "claim"}]

    def search_experience(self, domain, entity_name, limit, offset):
        self.calls.append(("experience", domain, entity_name, limit, offset))
        return 1, [{"domain": domain}]


class ReadServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeReadRepository()
        self.service = ReadService(self.repository)

    def test_common_reads_do_not_depend_on_http(self):
        entity_id = UUID("00000000-0000-0000-0000-000000000001")
        self.assertEqual(self.service.list_entities(" pc ", 10)[0]["name"], "PC")
        self.service.list_current_claims(entity_id, True, 20)
        self.service.list_tasks("running", 5)
        self.assertEqual(
            self.repository.calls,
            [
                ("entities", "pc", 10),
                ("claims", entity_id, True, 20),
                ("tasks", "running", 5),
            ],
        )

    def test_memory_search_normalizes_transport_strings(self):
        result = self.service.search_memory(
            q="  RAM  ", domain=" pc ", kind="claim",
            include_history=True, limit=25, offset=50,
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["offset"], 50)
        self.assertEqual(
            self.repository.calls[-1],
            ("memory", "RAM", "pc", "claim", True, 25, 50),
        )

    def test_experience_requires_domain_for_entity(self):
        with self.assertRaisesRegex(ValueError, "entity_name requires domain"):
            self.service.search_experience(entity_name="Main PC")
        self.assertEqual(self.repository.calls, [])

    def test_experience_preserves_scope_contract(self):
        linked = self.service.search_experience(" pc ", " Main PC ", 10, 0)
        self.assertEqual(linked["scope"], "linked entity only")
        domain_wide = self.service.search_experience("pc", None, 10, 0)
        self.assertEqual(domain_wide["scope"], "domain-wide including unlinked tasks")


if __name__ == "__main__":
    unittest.main()
