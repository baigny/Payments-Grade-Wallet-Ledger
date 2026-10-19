# Fraud decisions and review

Database rules run inside the same transaction as posting, after both account
rows have been locked in ascending ID order. Balances and ownership are checked
before creating either a held or a posted transfer. No LLM is called here.

| Rule | Decision | Index |
|---|---|---|
| amount | Candidate amount > threshold; all movement types | Enabled rules are a small configuration table; no history lookup |
| velocity | Prior posted/held outgoing attempts in the window + candidate > threshold | Partial `transfers_risk_history(source_account_id, created_at)` |
| daily_total | Prior posted/held outgoing amount in the configured rolling window + candidate > threshold | Same partial index, including amount |

Velocity and rolling totals are scoped to the source wallet. System-source
movements still receive the amount check but have no wallet velocity/total check.
Every threshold and action lives in `risk_rules`. Equality does not trigger a
rule. Rejected transfers do not count; held attempts do count. Funds are checked
first, so insufficient-funds attempts are not recorded as risk attempts.

The default rules are amount >100,000 paise (hold), more than five outgoing
attempts in 60 seconds (hold), and rolling outgoing total >500,000 paise in
86,400 seconds (flag). If any matched rule says hold, no money moves. Flag-only
matches post normally. Each matched rule stores its configuration, observed
value and evaluation time in an immutable snapshot.

## Review

Held transfers reserve no money. Admin release locks both accounts again,
locks and rechecks the existing transfer, checks current funds/overflow, and
posts that same ID. The original rule decision is explicitly overridden by
the review, without rerunning rules. An admin rejection changes only status.
Concurrent release/reject attempts have one winner. A rejected or posted
transfer cannot be reviewed again. Review requires a reason and records actor,
time, previous/new status and reason in immutable status history.

Each POST is idempotent, including rule updates and review. Replaying creation
still returns the original held snapshot after release; GET `/transfers/{id}`
returns current state to either wallet owner or an admin. Rule management,
the held queue and risk flags are admin-only. Rule changes save configuration
events with the actor. Rule changes never rewrite existing risk snapshots.

## Verification

Existing money tests and the isolated network stampede keep all rules enabled
with non-triggering thresholds. This preserves the exact 50-of-100 money-path
acceptance test while checking that rule queries do not break locking/atomicity.
Production default velocity rules intentionally hold the sixth matching attempt;
it would be incorrect to expect fifty posted payments under that policy.

Fraud-specific tests exercise exact boundaries, rolling totals, parallel velocity
checks, no-money holds, release/rejection races, current-funds checks, replay,
ownership and immutable rule snapshots. The Postman collection temporarily lowers
the amount threshold, creates/reviews two holds, then restores its prior setting.

[Measured per-rule overhead](fraud-perf.md) uses an isolated database and actual
committed money transactions against 10,000 prior outgoing payments. It compares
no fraud SQL, amount only, velocity only, rolling total only, and all rules.
