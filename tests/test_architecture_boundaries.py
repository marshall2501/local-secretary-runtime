"""Static dependency guards for the refactored runtime boundaries."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_legacy_pkb_proto_directory_is_absent(self):
        self.assertFalse((ROOT / "pkb_proto").exists())

    def test_production_code_does_not_depend_on_legacy_pkb_proto(self):
        violations = []
        for package in (
            "api", "application", "bootstrap", "capabilities", "infrastructure",
            "integrations", "interfaces", "mcp_adapter", "pkb", "ritsuko",
            "secretary",
        ):
            root = ROOT / package
            if not root.exists():
                continue
            for path in root.rglob("*.py"):
                for name in _imports(path):
                    if (
                        name == "pkb_proto"
                        or name.startswith("pkb_proto.")
                        or name in {"secretary.read_service", "secretary.read_repository"}
                    ):
                        violations.append(f"{path.relative_to(ROOT)} -> {name}")
        self.assertEqual([], violations)

    def test_tests_do_not_import_legacy_packages(self):
        violations = []
        for path in (ROOT / "tests").rglob("*.py"):
            for name in _imports(path):
                if (
                    name == "pkb_proto"
                    or name.startswith("pkb_proto.")
                    or name in {"secretary.read_service", "secretary.read_repository"}
                ):
                    violations.append(f"{path.relative_to(ROOT)} -> {name}")
        self.assertEqual([], violations)

    def test_pkb_and_capabilities_do_not_depend_on_ritsuko(self):
        violations = []
        for package in ("pkb", "capabilities"):
            root = ROOT / package
            if not root.exists():
                continue
            for path in root.rglob("*.py"):
                for name in _imports(path):
                    if name == "ritsuko" or name.startswith("ritsuko."):
                        violations.append(f"{path.relative_to(ROOT)} -> {name}")
        self.assertEqual([], violations)

    def test_mcp_adapter_has_no_database_dependency(self):
        violations = []
        for path in (ROOT / "mcp_adapter").rglob("*.py"):
            for name in _imports(path):
                if name == "psycopg" or name.startswith("psycopg."):
                    violations.append(f"{path.relative_to(ROOT)} -> {name}")
        self.assertEqual([], violations)

    def test_interfaces_have_no_concrete_postgres_dependency(self):
        violations = []
        for path in (ROOT / "interfaces").rglob("*.py"):
            for name in _imports(path):
                if (
                    name == "psycopg"
                    or name.startswith("psycopg.")
                    or name == "infrastructure.postgres"
                    or name.startswith("infrastructure.postgres.")
                ):
                    violations.append(f"{path.relative_to(ROOT)} -> {name}")
        self.assertEqual([], violations)

    def test_application_and_domain_packages_do_not_import_infrastructure(self):
        violations = []
        for package in ("application", "ritsuko", "pkb", "capabilities"):
            root = ROOT / package
            if not root.exists():
                continue
            for path in root.rglob("*.py"):
                for name in _imports(path):
                    if name == "infrastructure" or name.startswith("infrastructure."):
                        violations.append(f"{path.relative_to(ROOT)} -> {name}")
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
