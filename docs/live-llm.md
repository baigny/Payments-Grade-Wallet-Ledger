# Local model verification

Offline CI uses a deterministic mock and mocked Ollama transport. A separate,
opt-in integration check runs the authenticated ASGI HTTP application with real
PostgreSQL and an operator-configured local model. It verifies all four question
types against known ledger values and transfer IDs, excludes another user's
money, includes an injection note, and validates a flag explanation without
changing a held transfer or its wallet balance.

Configure the usual migration-owner database variables, then:

```powershell
$env:WALLET_LIVE_LLM = '1'
$env:OLLAMA_BASE_URL = 'http://localhost:11434'
$env:OLLAMA_MODEL = 'llama3.2:3b'
$env:WALLET_LLM_EVIDENCE = 'docs/evidence/ollama-smoke.json'
python -m pytest tests/test_live_llm.py -q
```

Use an already installed model and warm it before running; the application keeps
its ten-second provider timeout. The test creates and removes a private database
schema. It never modifies the running API's provider configuration and does not
download models. Evidence is written only after every assertion passes.

Current verification limit: the installed local model could not load because
Ollama failed to allocate its CPU model buffer. No successful real-model result
is claimed. The opt-in test remains excluded from ordinary offline CI. A machine
with enough available memory is needed to complete it; provider failures continue
to return a bounded 503 rather than a fabricated answer.
