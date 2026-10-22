import json
from datetime import UTC, datetime

import httpx
import pytest

from wallet import worker
from wallet.core import llm
from wallet.core.errors import DomainError
from wallet.statements.schemas import QuestionSpec


def test_hosted_adapter_uses_schema_and_treats_question_as_data(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "hosted")
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    original = httpx.Client
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    def handle(request):
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["response_format"] == {"type": "json_object"}
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert request.url.path == "/chat/completions"
        assert json.dumps(QuestionSpec.model_json_schema()) in body["messages"][0]["content"]
        assert body["messages"][0]["role"] == "system"
        assert json.loads(body["messages"][1]["content"])["question"] == "Count transfers"
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"type":4}'}}]}
        )

    monkeypatch.setattr(
        llm.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    assert (
        llm.provider().generate(
            "statement_question", {"question": "Count transfers"}, QuestionSpec.model_json_schema()
        )
        == '{"type":4}'
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(429, text="test-secret"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"finish_reason": "length"}]}),
        httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": []}]}),
        httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"refusal": "No"}}]},
        ),
        httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": None}}]}
        ),
        httpx.Response(200, content=b"not JSON"),
        httpx.Response(200, content=b"x" * 65537),
    ],
)
def test_provider_errors_are_bounded_and_safe(monkeypatch, response):
    monkeypatch.setenv("LLM_PROVIDER", "hosted")
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    original = httpx.Client
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setattr(
        llm.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(lambda request: response), **kw),
    )
    with pytest.raises(DomainError) as caught:
        llm.provider().generate("statement_question", {}, {})
    assert caught.value.status == 503
    assert "provider.test" not in caught.value.message
    assert "test-secret" not in caught.value.message


def test_offline_last_month_handles_leap_year():
    result = json.loads(
        llm.MockProvider().generate(
            "statement_question",
            {
                "question": "How much did I send to Rahul last month?",
                "reference_date": "2024-03-01",
            },
            {},
        )
    )
    assert result == {"type": 1, "counterparty": "rahul", "from": "2024-02-01", "to": "2024-02-29"}


@pytest.mark.parametrize(
    "url",
    ["http://remote.test/v1", "https://user:secret@api.test/v1", "https://api.test/v1?key=secret"],
)
def test_hosted_provider_rejects_unsafe_configuration(monkeypatch, url):
    monkeypatch.setenv("LLM_PROVIDER", "hosted")
    monkeypatch.setenv("LLM_BASE_URL", url)
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    with pytest.raises(DomainError) as caught:
        llm.provider()
    assert caught.value.status == 503
    assert "secret" not in caught.value.message


def test_hosted_provider_requires_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "hosted")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(DomainError) as caught:
        llm.provider()
    assert caught.value.status == 503


def test_hosted_timeout_does_not_expose_credentials(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "hosted")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.test/v1")
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    original = httpx.Client

    def timeout(request):
        raise httpx.ReadTimeout("secret", request=request)

    monkeypatch.setattr(
        llm.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(timeout), **kw)
    )
    with pytest.raises(DomainError) as caught:
        llm.provider().generate("statement_question", {}, {})
    assert caught.value.status == 503
    assert "secret" not in caught.value.message


def test_explanation_failure_does_not_undo_nightly_reconciliation(monkeypatch):
    report = {"id": 1, "status": "passed"}
    monkeypatch.setattr(worker.reconciliation, "execute", lambda *args: report)
    calls = []

    def fail(limit, cutoff):
        calls.append((limit, cutoff))
        raise DomainError("LLM_UNAVAILABLE", "Unavailable", 503)

    monkeypatch.setattr(worker.explanations, "execute", fail)
    assert worker.tick(datetime(2026, 8, 2, 3, tzinfo=UTC), 2, 0) == report
    assert calls == [(1, datetime(2026, 8, 2, 2, tzinfo=UTC))]
