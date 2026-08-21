# Contributing to TradeMind

Thanks for taking a look. TradeMind is a working trading system with a
[documented lack of edge](docs/SIGNAL_EDGE_FINDINGS.md), which shapes what's useful
here: contributions that make the *evaluation* more honest are worth more than
contributions that make the *strategy* sound better.

## Where help is most valuable

1. **Attack the backtest.** Find lookahead bias, survivorship bias, unrealistic fills,
   or cost-model errors in `backend/app/backtest/`. A demonstrated flaw in the harness
   is the single most valuable contribution to this repo.
2. **Seed point-in-time index membership.** `index_membership` is unseeded, so backtests
   use today's NIFTY-50 over historical windows. This inflates the mean-reversion
   results and is the largest known methodological gap.
3. **Add authentication to the API.** See
   [SECURITY.md](SECURITY.md#the-api-has-no-authentication). Currently there is none.
4. **Out-of-sample strategy validation.** Does the mean-reversion inversion hold on
   other universes, other periods, other markets?
5. **Isolate the test database from the dev database** — `conftest.py` currently
   drops the tables of whatever database the dev stack uses (see the warning below).
6. Ordinary things: bug fixes, test coverage, docs, developer experience.

## Development setup

Requires Docker and Docker Compose.

```bash
git clone https://github.com/<your-fork>/trademind.git   # your fork of Pranavv-dev/trademind
cd trademind
cp .env.example .env          # placeholders are fine for most development

docker compose up -d db redis
docker compose run --rm backend alembic upgrade head
make dev                       # hot-reload stack
```

The backend runs without any broker or LLM credentials — agents needing live data
simply no-op. You do **not** need a Zerodha account to work on most of the codebase.

## Before you open a PR

```bash
make test     # pytest
make lint     # ruff check + ruff format --check
make format   # auto-fix
```

> **Baseline: 260 tests pass and lint is clean.** If something is red before you've
> changed anything, that's a bug worth reporting — with one exception: tests that
> resolve config as `arg or settings.<field>` can pick up your `.env`. Those are
> pinned with an autouse fixture (see `tests/test_notifications/test_telegram.py`);
> follow that pattern for any new test that touches configured credentials.

> **`make test` will destroy your local database.** `backend/tests/conftest.py` runs
> `create_all`/`drop_all` against the *same* Postgres database the dev stack uses. Run
> it against a throwaway instance if you have paper-trade history you care about:
> ```bash
> docker compose -p trademind-test up -d db redis
> docker compose -p trademind-test run --rm --no-deps backend pytest -q
> docker compose -p trademind-test down -v
> ```
> Pointing the test fixtures at a dedicated test database is itself a welcome fix.

> **Lint is enforced on `app/` only** (see the `Makefile`), at `line-length = 100`
> with ruff rules `E,F,I,N,W`. `tests/` is deliberately not linted. Run `make format`
> before pushing.

- **Tests are required** for behaviour changes. Add them next to the module they cover
  under `backend/tests/test_<area>/`.
- **Keep `TRADING_MODE=paper`.** Never add code that defaults to live order placement,
  and never widen a risk check's default in the same PR as a feature.
- **Don't weaken the risk manager.** `backend/app/risk/` is a hard gate. Changes there
  need an explicit rationale in the PR description.
- **Migrations:** schema changes need an Alembic revision —
  `make migration msg="describe change"` — committed alongside the model change.
- Follow the surrounding style. Backend is `ruff`-formatted; frontend is TypeScript
  with Tailwind. Match the comment density you find.

## Strategy and parameter PRs

A PR that changes signal logic, thresholds, or exits must include **cost-aware backtest
evidence**:

```bash
docker compose exec backend python -m app.backtest.proactive_backtest
```

Report win rate, expectancy in R, profit factor, and max drawdown — before and after —
over the same window, net of costs. State the sample size. A parameter change that
improves results on one 12-month window is a coincidence until it survives multiple
regimes; `docs/SIGNAL_EDGE_FINDINGS.md` exists because that lesson was expensive.

Please don't submit strategies without evidence, or backtests without costs.

## Secrets

Never commit `.env`, credentials, access tokens, real account IDs, or personal trade
history. Check `git diff --cached` before committing. If you add a new configuration
variable, add it to `.env.example` with a **placeholder** value and a comment saying
what it does.

If you believe you've committed a secret, treat it as compromised: rotate it first,
then tell us. Read [SECURITY.md](SECURITY.md) for rotation guidance — for Kite, the
TOTP seed must be re-provisioned, not just the password.

## Reporting bugs

Open an issue with what you expected, what happened, the relevant container logs
(`make logs-backend`), and your `TRADING_MODE`. **Redact tokens, API keys, and account
identifiers from logs before pasting them.**

For security vulnerabilities, do not open a public issue — follow
[SECURITY.md](SECURITY.md#reporting-a-vulnerability).

## License

Contributions are accepted under the [MIT License](LICENSE), the same terms as the
project.
