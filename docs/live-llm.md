# Hosted model verification

Normal CI runs every provider test without an external network, key or model.
`tests/integration/test_provider_flow.py` starts a loopback HTTP server that speaks
Chat Completions, exercises the real hosted adapter and authenticated ASGI app,
and checks four question answers plus an explanation against PostgreSQL. The
fixture generates deterministic JSON; it is not a learned model. No opt-in model
test is skipped in this suite.

For real hosted verification, add the key to ignored `.env`:

```dotenv
LLM_PROVIDER=hosted
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=openai/gpt-oss-20b
LLM_API_KEY=your-key
EXPLANATION_BATCH_SIZE=1
```

With PostgreSQL running and the development dependencies installed, run:

```powershell
.venv/Scripts/python.exe scripts/check_live_llm.py
```

This command requires credentials and fails if the provider or assertions fail;
it does not silently skip. It creates and removes scratch schemas and uses only
synthetic data. It checks all four answer values and contributing transfer IDs,
excludes another user's funds, includes a prompt-injection note, validates a
two-sentence explanation and confirms the held transfer still has no entries.
Only a successful real hosted run writes `docs/evidence/hosted-llm.json`.
Existing application data is not used. Do not commit `.env` or provider keys.
The [recorded hosted check](evidence/hosted-llm.json) passed on 22 October 2026
using Groq's `openai/gpt-oss-20b`; this is a functional check, not a load test.

Groq's [free limits](https://console.groq.com/docs/rate-limits) currently list
30 requests/minute and 1,000/day for `openai/gpt-oss-20b`, with additional token
limits. Actual account limits apply. Five model calls are made by this check;
there is no paid fallback or automatic retry. Compatible endpoints must accept
JSON-object mode and `max_completion_tokens`. The app validates JSON before any
scoped SQL query runs; the model never supplies SQL or chooses user IDs.

Provider timeout is ten seconds, redirects are disabled, the response is bounded
at 64 KiB and message content at 8 KiB. Errors do not expose the key or provider
response body. HTTPS is required except for loopback test endpoints.

The nightly worker defaults to one explanation per tick, configurable from 1 to
100 with `EXPLANATION_BATCH_SIZE`. Small batches bound rollback on quota failures.
Calls still happen inside the explanation transaction; moving them to leased
jobs is future work. Reconciliation commits separately and survives model failure.
Manual explanation requests retain their explicit limit and may exhaust quotas.

The former skipped check required an installed local model and failed memory
allocation on this machine. That project integration has been removed; no machine
installation or downloaded model is deleted.
