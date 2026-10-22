import json
from datetime import UTC, datetime

import httpx
import pytest

from wallet import worker
from wallet.core import llm
from wallet.core.errors import DomainError
from wallet.statements.schemas import QuestionSpec


def test_ollama_adapter_uses_schema_and_treats_question_as_data(monkeypatch):
    original = httpx.Client
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://provider.test")
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")

    def handle(request):
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["format"] == QuestionSpec.model_json_schema()
        assert body["messages"][0]["role"] == "system"
        assert json.loads(body["messages"][1]["content"])["question"] == "Count transfers"
        return httpx.Response(200, json={"message": {"content": '{"type":4}'}})

    monkeypatch.setattr(
        llm.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    assert (
        llm.OllamaProvider().generate(
            "statement_question", {"question": "Count transfers"}, QuestionSpec.model_json_schema()
        )
        == '{"type":4}'
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, content=b"not JSON"),
        httpx.Response(200, content=b"x" * 65537),
    ],
)
def test_provider_errors_are_bounded_and_safe(monkeypatch, response):
    original = httpx.Client
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://provider.test")
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setattr(
        llm.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(lambda request: response), **kw),
    )
    with pytest.raises(DomainError) as caught:
        llm.OllamaProvider().generate("statement_question", {}, {})
    assert caught.value.status == 503
    assert "provider.test" not in caught.value.message


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


def test_explanation_failure_does_not_undo_nightly_reconciliation(monkeypatch):
    report = {"id": 1, "status": "passed"}
    monkeypatch.setattr(worker.reconciliation, "execute", lambda *args: report)
    calls = []

    def fail(limit, cutoff):
        calls.append((limit, cutoff))
        raise DomainError("LLM_UNAVAILABLE", "Unavailable", 503)

    monkeypatch.setattr(worker.explanations, "execute", fail)
    assert worker.tick(datetime(2026, 8, 2, 3, tzinfo=UTC), 2, 0) == report
    assert calls == [(100, datetime(2026, 8, 2, 2, tzinfo=UTC))]
