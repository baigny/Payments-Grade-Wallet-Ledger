# Review dataset

`python -m scripts.seed` loads 1,000 users, 2,000 wallets and exactly 100,000
posted transfers (200,000 entries). Run as the migration owner against a fresh,
migrated development database, before creating the administrator. Existing users
or transfers cause a refusal; nothing is deleted or replaced. Standard `PG*` or
`DATABASE_URL` settings select the database. Custom sizes use `--users`, `--wallets`
and `--transfers`.

After starting a fresh installation with `docker compose up`, the owner-configured
Compose service can run the loader directly:

```powershell
docker compose run --rm migrate python -m scripts.seed
```

The first 2,000 transfers fund each wallet with 100,000 paise. The remaining
98,000 are deterministic 100-paise peer payments around a ring of wallets.
Funding is dated January 2026 and payments February 2026, giving statement
questions predictable date ranges. All users have synthetic `.test` addresses.

This is an offline database fixture, not a payment API load test: it skips HTTP,
authentication, idempotency and fraud evaluation. Database constraints, immutable
history, posting validation and status audit triggers remain enabled throughout.
One transaction loads and validates all data, including every raw reconciliation
check and event comparison. Failure rolls back the load. Identity sequences can
advance on rollback, as usual in PostgreSQL. No bearer tokens are generated.
Bootstrap an admin afterward to inspect wallets through the API.

Verify the exact full-size dataset without touching application records:

```powershell
python -m scripts.verify_seed
```

This creates, migrates and removes a uniquely named scratch schema using the
migration-owner connection. Small automated tests check totals, audit history,
balance invariants, invalid arguments and refusal to load twice.
