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

## Quotas and cooldowns

API replicas and the worker share PostgreSQL quota reservations, keyed by the
configured endpoint and model, across rolling one-minute and 24-hour windows.
`LLM_REQUESTS_PER_MINUTE`, `LLM_REQUESTS_PER_DAY`, `LLM_TOKENS_PER_MINUTE` and
`LLM_TOKENS_PER_DAY` default to 30, 1000, 8000 and 200000. Check your account's
actual limits and lower these settings when needed. The mock bypasses this gate.

Before sending a request, the adapter reserves input UTF-8 byte length plus
output allowance and framing. This deliberately overestimates tokens and can
reject a large prompt that the provider might accept. Successful responses
replace the reservation with reported usage. Missing/failed usage retains the
full reservation. Failed calls consume reservations, and completed idempotency
replays do not call the provider again. Expired reservations are pruned on the
next admitted call for that endpoint/model.

Reservations commit independently of the caller's transaction: rolling back a
question or explanation must not erase already consumed external quota. Budget
locks are short and released before HTTP calls; admission has a 250ms lock wait
and a two-second SQL statement timeout. The existing caller transaction still
spans the model call; moving inference fully outside transactions is future work.

Budget exhaustion returns the standard `503 LLM_BUSY` error with `Retry-After`.
An upstream 429 creates a shared cooldown using its Retry-After header (seconds
or HTTP date, bounded to one day, default 60 seconds). It is not forwarded as 429
and is not retried automatically. Retry with the same idempotency key after the
cooldown; this infrastructure failure is not saved as a completed response.
Nightly failures leave reconciliation committed and the explanation pending.

This reduces calls that would exceed our configured allowance; it cannot
guarantee the provider never throttles. Groq limits are organization-wide, so
other applications, different endpoint aliases/models, account-specific limits
or capacity restrictions can still cause 429. All replicas must share the same
database/schema and quota settings for coordinated admission. No unbounded queue,
sleep inside a payment transaction, or paid provider fallback is used.

## Errors and recovery

Errors keep the API shape `{code, message, request_id}`. Provider failures use
503 because client authentication can still be valid when the external service
fails. Raw provider bodies, endpoint credentials and API keys are never returned.

| Code | Meaning | Recovery |
| --- | --- | --- |
| `LLM_BUSY` | Local allowance exhausted or provider cooldown active | Wait for `Retry-After`, then retry the same idempotency key |
| `LLM_NOT_CONFIGURED` | Hosted credentials/model/endpoint missing | Operator configures the ignored environment and recreates API/worker |
| `LLM_INVALID_CONFIGURATION` | Invalid mode, URL or quota settings | Operator fixes configuration; restarting alone does not fix it |
| `LLM_AUTH_FAILED` | Provider rejected server credentials/access | Operator checks the key and provider permissions; client should not replace its wallet bearer token |
| `LLM_MODEL_UNAVAILABLE` | Configured provider endpoint/model returned 404 | Operator checks endpoint/model availability |
| `LLM_TIMEOUT` | Provider did not finish within the timeout | Retry the same key; inspect repeated failures using request IDs |
| `LLM_INVALID_RESPONSE` | Incomplete, refused, malformed or oversized provider reply | Retry conservatively; operator checks provider compatibility if persistent |
| `LLM_UNAVAILABLE` | Connection failure or other unsuccessful provider response | Retry later with the same key; check provider availability |

Existing domain errors remain distinct: invalid/unsupported statement intent is
422 `UNSUPPORTED_QUESTION`; an invalid advisory explanation is 503
`INVALID_EXPLANATION`. Permission and input failures occur before provider calls.
A 503 rolls back the question/explanation batch and its idempotency claim. It does
not roll back quota already consumed by an external request. Held money remains
held; a nightly explanation failure does not undo saved reconciliation results.

Tests cover concurrent reservations, each request/token window, window expiry,
actual usage, malformed Retry-After, shared cooldown after one upstream 429,
retry after local exhaustion and exact replay without another provider call.
Transport tests separately cover credential errors, timeout, malformed/truncated
content and configuration errors. These tests use mocks or loopback HTTP, with
real PostgreSQL for admission and API behavior; they do not consume Groq quota.
