from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from uuid import uuid4

import httpx
import pytest

from wallet.core import llm, llm_budget
from wallet.core.errors import DomainError


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2", 2),
        ("2.5", 3),
        ("-1", 1),
        (None, 60),
        ("invalid", 60),
        ("nan", 60),
        ("inf", 60),
        ("999999999", 86400),
    ],
)
def test_retry_after_is_bounded(value, expected):
    assert llm_budget.retry_seconds(value) == expected


def test_retry_after_supports_http_date():
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    assert 28 <= llm_budget.retry_seconds(future) <= 30


@pytest.fixture
def quota(api, monkeypatch):
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "3")
    monkeypatch.setenv("LLM_REQUESTS_PER_DAY", "10")
    monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "10000")
    monkeypatch.setenv("LLM_TOKENS_PER_DAY", "100000")
    return api


@pytest.mark.database
def test_concurrent_reservations_share_one_budget(quota):
    def claim(_):
        try:
            llm_budget.reserve("https://provider.test/v1", "shared", 100)
            return True
        except DomainError as exc:
            assert exc.code == "LLM_BUSY" and exc.status == 503
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, range(12)))
    assert sum(results) == 3
    with quota.owner() as conn:
        assert conn.execute("SELECT count(*) FROM llm_requests").fetchone()[0] == 3


@pytest.mark.database
@pytest.mark.parametrize(
    "setting,tokens",
    [
        ("LLM_REQUESTS_PER_MINUTE", 1),
        ("LLM_REQUESTS_PER_DAY", 1),
        ("LLM_TOKENS_PER_MINUTE", 100),
        ("LLM_TOKENS_PER_DAY", 100),
    ],
)
def test_each_quota_rejects_before_provider_call(quota, monkeypatch, setting, tokens):
    monkeypatch.setenv(setting, str(tokens))
    llm_budget.reserve("https://provider.test/v1", "limited", tokens)
    with pytest.raises(DomainError) as caught:
        llm_budget.reserve("https://provider.test/v1", "limited", 1)
    assert caught.value.status == 503
    assert caught.value.retry_after >= 1
    with quota.owner() as conn:
        assert conn.execute("SELECT count(*) FROM llm_requests").fetchone()[0] == 1


@pytest.mark.database
def test_expired_windows_and_actual_token_usage(quota, monkeypatch):
    monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "100")
    first = llm_budget.reserve("https://provider.test/v1", "limited", 100)
    llm_budget.complete(first, 20)
    second = llm_budget.reserve("https://provider.test/v1", "limited", 80)
    with pytest.raises(DomainError):
        llm_budget.reserve("https://provider.test/v1", "limited", 1)
    with quota.owner() as conn:
        conn.execute(
            "UPDATE llm_requests SET created_at = clock_timestamp() - interval '61 seconds'"
        )
    third = llm_budget.reserve("https://provider.test/v1", "limited", 100)
    with quota.owner() as conn:
        assert conn.execute("SELECT count(*) FROM llm_requests").fetchone()[0] == 3
        conn.execute("UPDATE llm_requests SET created_at = clock_timestamp() - interval '25 hours'")
    fourth = llm_budget.reserve("https://provider.test/v1", "limited", 100)
    assert len({first[0], second[0], third[0], fourth[0]}) == 4
    with quota.owner() as conn:
        assert conn.execute("SELECT count(*) FROM llm_requests").fetchone()[0] == 1


@pytest.mark.database
def test_upstream_429_sets_shared_cooldown_without_retrying(quota, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "hosted")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.test/v1")
    monkeypatch.setenv("LLM_MODEL", "limited")
    monkeypatch.setenv("LLM_API_KEY", "test-only-secret")
    original = httpx.Client
    calls = []

    def throttled(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "37"}, text="test-only-secret")

    monkeypatch.setattr(
        llm.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(throttled), **kw)
    )
    for _ in range(2):
        with pytest.raises(DomainError) as caught:
            # Fresh provider instances still share the persisted cooldown.
            llm.provider().generate("statement_question", {}, {})
        assert caught.value.status == 503
        assert 1 <= caught.value.retry_after <= 37
        assert "test-only-secret" not in caught.value.message
    assert len(calls) == 1


@pytest.mark.database
def test_http_quota_failure_is_retryable_and_not_saved_on_idempotency_key(quota, monkeypatch):
    user = quota.user()
    monkeypatch.setenv("LLM_PROVIDER", "hosted")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.test/v1")
    monkeypatch.setenv("LLM_MODEL", "limited")
    monkeypatch.setenv("LLM_API_KEY", "test-only-secret")
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "1")
    ticket = llm_budget.reserve("https://provider.test/v1", "limited", 1)
    key = str(uuid4())
    response = quota.request(
        "POST",
        f"/users/{user['id']}/statement/ask",
        token=user["token"],
        key=key,
        json={"question": "Count transfers"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "LLM_BUSY"
    assert response.headers["Retry-After"] == "60"
    with quota.owner() as conn:
        assert (
            conn.execute("SELECT count(*) FROM idempotency_keys WHERE key = %s", (key,)).fetchone()[
                0
            ]
            == 0
        )
        assert conn.execute("SELECT count(*) FROM ledger_entries").fetchone()[0] == 0
        assert conn.execute("SELECT id FROM llm_requests").fetchone()[0] == ticket[0]
        conn.execute(
            "UPDATE llm_requests SET created_at = clock_timestamp() - interval '61 seconds'"
        )
    original = httpx.Client
    calls = []

    def answer(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"type":4,"from":"2026-10-01",'
                            '"to":"2026-10-31","counterparty":null}'
                        },
                    }
                ],
                "usage": {"total_tokens": 100},
            },
        )

    monkeypatch.setattr(
        llm.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(answer), **kw)
    )
    responses = [
        quota.request(
            "POST",
            f"/users/{user['id']}/statement/ask",
            token=user["token"],
            key=key,
            json={"question": "Count transfers"},
        )
        for _ in range(2)
    ]
    assert all(response.status_code == 201 for response in responses)
    assert responses[0].content == responses[1].content
    assert responses[1].headers["Idempotent-Replay"] == "true"
    assert len(calls) == 1
