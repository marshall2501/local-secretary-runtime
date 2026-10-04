"""Strict orchestration for the disposable production-runtime rehearsal.

The generated runtime login secret lives only inside a temporary directory.
Provision must succeed before verification starts. Any subprocess failure raises,
and the secret is removed on success and every exception path.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
PROVISION = ROOT / "scripts" / "db" / "provision_daily_runtime.py"
VERIFY = ROOT / "scripts" / "db" / "verify_production_runtime.py"


def _validate_port(value: int, name: str) -> int:
    if not 1024 <= int(value) <= 65535:
        raise RuntimeError(f"invalid {name} PostgreSQL port")
    return int(value)


def _run_steps(
    *,
    target_port: int,
    live_port: int,
    admin_secret_file: Path,
    runtime_secret_file: Path,
    settings_source_port: int | None,
    runner,
    target_database: str = "secretary",
) -> None:
    provision_cmd = [
        sys.executable,
        str(PROVISION),
        "--port",
        str(target_port),
        "--database",
        target_database,
        "--admin-secret-file",
        str(admin_secret_file),
        "--runtime-secret-file",
        str(runtime_secret_file),
        "--target-database",
        target_database,
    ]
    verify_cmd = [
        sys.executable,
        str(VERIFY),
        "--target-port",
        str(target_port),
        "--live-port",
        str(live_port),
        "--runtime-secret-file",
        str(runtime_secret_file),
    ]
    if settings_source_port is not None:
        verify_cmd.extend([
            "--settings-source-port",
            str(settings_source_port),
            "--admin-secret-file",
            str(admin_secret_file),
        ])

    try:
        runner(provision_cmd, check=True, cwd=str(ROOT))
        if not runtime_secret_file.is_file():
            raise RuntimeError("provisioning did not create the runtime secret")
        runner(verify_cmd, check=True, cwd=str(ROOT))
    finally:
        if runtime_secret_file.exists():
            runtime_secret_file.unlink()
        if runtime_secret_file.exists():
            raise RuntimeError("temporary runtime secret cleanup failed")


def run_rehearsal(
    *,
    target_port: int,
    live_port: int,
    admin_secret_file: Path,
    settings_source_port: int | None = None,
    runner=None,
    target_database: str = "secretary",
) -> None:
    target_port = _validate_port(target_port, "target")
    live_port = _validate_port(live_port, "live")
    from config.runtime_database import PRODUCTION_DB, approved_production_database_name
    if not approved_production_database_name(target_database):
        raise RuntimeError("unapproved production rehearsal database")
    if target_port == live_port and target_database == PRODUCTION_DB:
        raise RuntimeError("refusing to run production write probes on the live PostgreSQL database")
    if settings_source_port is not None:
        settings_source_port = _validate_port(settings_source_port, "settings source")
    if not admin_secret_file.is_file():
        raise RuntimeError("admin secret file is missing")

    execute = subprocess.run if runner is None else runner
    with tempfile.TemporaryDirectory(prefix="lsa-production-rehearsal-") as temp_dir:
        runtime_secret = Path(temp_dir) / "daily-runtime.secret"
        _run_steps(
            target_port=target_port,
            live_port=live_port,
            admin_secret_file=admin_secret_file,
            runtime_secret_file=runtime_secret,
            settings_source_port=settings_source_port,
            runner=execute,
            target_database=target_database,
        )
        if runtime_secret.exists():
            raise RuntimeError("temporary runtime secret remained after verification")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--live-port", type=int, required=True)
    parser.add_argument("--admin-secret-file", type=Path, required=True)
    parser.add_argument("--settings-source-port", type=int)
    parser.add_argument("--target-database", default="secretary")
    args = parser.parse_args()

    run_rehearsal(
        target_port=args.target_port,
        live_port=args.live_port,
        admin_secret_file=args.admin_secret_file,
        settings_source_port=args.settings_source_port,
        target_database=args.target_database,
    )
    print("PASS: provision completed before verification and the temporary runtime secret was removed.")


if __name__ == "__main__":
    main()
