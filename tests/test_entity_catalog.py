import unittest

from pkb.application.entity_catalog import EntityCatalogService


class _Repository:
    def __init__(self):
        self.calls = []

    def create_or_get(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "1", **kwargs, "created": True}


class EntityCatalogServiceTests(unittest.TestCase):
    def test_normalizes_and_delegates(self):
        repo = _Repository()
        result = EntityCatalogService(repo).create(
            name="  サブPC  ",
            domain=" pc ",
            entity_type=" computer ",
        )
        self.assertEqual(result["name"], "サブPC")
        self.assertEqual(
            repo.calls,
            [{
                "name": "サブPC",
                "domain": "pc",
                "entity_type": "computer",
                "actor": "local_user",
            }],
        )

    def test_rejects_missing_fields(self):
        service = EntityCatalogService(_Repository())
        for kwargs in (
            {"name": "", "domain": "pc", "entity_type": "computer"},
            {"name": "サブPC", "domain": "", "entity_type": "computer"},
            {"name": "サブPC", "domain": "pc", "entity_type": ""},
        ):
            with self.assertRaises(ValueError):
                service.create(**kwargs)


if __name__ == "__main__":
    unittest.main()
