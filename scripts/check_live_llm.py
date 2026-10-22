"""Explicit hosted-model acceptance check using disposable PostgreSQL schemas."""

import os
from pathlib import Path

import pytest


def main():
    root = Path(__file__).resolve().parents[1]
    env_file = root / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    if not os.getenv("LLM_API_KEY", "").strip():
        raise SystemExit("Set LLM_API_KEY in ignored .env before hosted verification")
    os.environ["WALLET_VERIFY_HOSTED"] = "1"
    os.environ["LLM_PROVIDER"] = "hosted"
    os.environ.setdefault("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    os.environ.setdefault("LLM_MODEL", "openai/gpt-oss-20b")
    os.environ.setdefault("PGHOST", "127.0.0.1")
    os.environ.setdefault("PGPORT", os.getenv("POSTGRES_PORT", "55432"))
    os.environ.setdefault("PGDATABASE", os.getenv("POSTGRES_DB", "wallet"))
    os.environ.setdefault("PGUSER", os.getenv("POSTGRES_USER", "wallet_owner"))
    os.environ.setdefault("PGPASSWORD", os.getenv("POSTGRES_PASSWORD", ""))
    os.environ.setdefault("WALLET_LLM_EVIDENCE", str(root / "docs/evidence/hosted-llm.json"))
    # Never expose provider credentials in pytest failure locals or HTTP debug logs.
    return pytest.main(
        [
            str(root / "tests/integration/test_provider_flow.py"),
            "-q",
            "--tb=short",
            "-o",
            "addopts=",
            "--override-ini",
            "log_level=WARNING",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
