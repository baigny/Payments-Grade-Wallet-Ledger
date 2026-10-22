# Statement questions and advisory explanations

The default `LLM_PROVIDER=mock` is a deterministic offline mock, not a trained
language model. It recognizes the supported demo phrasings with two ISO dates
or “last month”. `MockProvider(response=...)` returns fixed JSON for tests.
No network or API key is required. The ten reproducible questions and expected
fixture answers are in [the sample corpus](statement-questions.json).

For a hosted model, configure `LLM_PROVIDER=hosted`, `LLM_BASE_URL`,
`LLM_MODEL` and `LLM_API_KEY`, then recreate API/worker. The adapter sends
non-streaming OpenAI-compatible Chat Completions with JSON-object mode and
includes the schema in the trusted system message. Application validation is
mandatory: JSON mode alone does not enforce our domain schema. Provider failures,
refusals and truncated responses return a bounded 503 without an offline fallback.
The default mock never contacts a provider. See [verification](live-llm.md).

## Questions

`POST /users/{id}/statement/ask` requires ownership (or an explicit admin override)
and an Idempotency-Key. The provider sees only the question and UTC reference date,
never rows, notes, balances, wallet IDs or other users. It returns one of:

1. Total peer payments sent to a named counterparty.
2. Total credits received.
3. Largest posted movement, with all tied transfer IDs.
4. Number of distinct posted movements.

Structured intent contains integer type 1..4, optional counterparty (required only
for type 1), and inclusive ISO `from`/`to` UTC dates. Extra fields, SQL, user IDs,
invalid types and unsupported date ranges are rejected. Ranges are at most 3,660
days; results are at most 1,000 contributing movements. Narrow larger requests.

The application builds SQL only from fixed clauses and bound parameters. Every
row comes from ledger entries joined to accounts owned by the authorized target
user. The provider cannot change that predicate. Amounts come from those entries;
transfer metadata supplies dates, status, kind and counterparty. Duplicate names
among matching counterparties require disambiguation rather than an arbitrary choice.

Results contain integer value, unit, validated intent and the contributing IDs.
Received totals are gross credits, including top-ups/reversals. Largest/count
queries include all posted kinds and deduplicate transfers between the same user's
wallets. They are not net spending reports. Held/rejected transfers have no entries
and never contribute. Empty results produce zero with no IDs. Exact retries return
the original saved answer, even after later payments change the statement.

## Flag explanations

`POST /admin/risk-flags/explain` accepts a bounded batch size. Each explanation
gets the immutable rule snapshot plus the selected wallet's last ten prior
transfers as JSON. System-source top-ups use the destination wallet, preventing
shared system-account history from exposing unrelated users. Emails, phones and
account-owner profiles are not sent. Notes are untrusted values, never instructions.

The schema requires two sentences and a label from `likely_ok`, `review`,
`likely_fraud`. Invalid responses roll back explanation writes and return 503.
Only advisory text/label/timestamp are updated; no model response can release,
reject or post a payment. Existing rule facts remain immutable. A separate
NOWAIT job lock prevents overlapping explanation batches.

The worker starts this step after its nightly reconciliation transaction. It uses
the most recent 02:00 UTC cutoff (or configured schedule), explains eligible
unexplained flags in batches of 100 per poll, and catches up any older backlog.
Flags newer than that cutoff wait for the next night; admins can explain them
immediately with the manual endpoint. Provider failures leave flags eligible for
retry on the next poll and cannot roll back a saved reconciliation report.

The optional hosted endpoint is operator configuration, never client input.
Requests have a ten-second HTTP timeout, redirects disabled, and bounded output.
An unavailable provider fails visibly; it does not silently fabricate an answer.
Using a remote endpoint sends the described prompt data to that operator-selected
provider. Keep the offline default when that data must stay within the application.
