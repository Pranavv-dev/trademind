# Security

TradeMind can hold credentials to a **live brokerage account**. That makes its threat
model unusual for a hobby project: a compromise here is not a leaked API quota, it is
potential unauthorized trading in a real account. Please read this before deploying.

---

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Use GitHub's private vulnerability reporting on this repository:
**Security → Advisories → Report a vulnerability**. That creates a private thread with
the maintainers.

Please include what you can: affected version/commit, the component, reproduction steps
or a proof of concept, and the impact you believe it has. Expect an acknowledgement
within a few days. This is a small, unfunded project — there is no bug bounty, but
credit in the advisory is offered to anyone who wants it.

**Out of scope:** losing money because a strategy in this repository has no edge. That
is not a vulnerability, it is [documented behaviour](docs/SIGNAL_EDGE_FINDINGS.md).

---

## Secrets: what the system holds and where

Every secret is supplied through environment variables, read by `pydantic-settings`
from a **gitignored `.env`**. No credential is ever hardcoded in source, and none is
written to logs.

| Secret | Purpose | Blast radius if leaked |
|---|---|---|
| `KITE_API_KEY` / `KITE_API_SECRET` | Kite Connect app identity | Impersonate the app; exchange request tokens |
| `KITE_ACCESS_TOKEN` | Live session token | **Read + trade the account** until daily expiry |
| `KITE_PASSWORD` | Zerodha login password | Direct account access |
| `KITE_TOTP_SECRET` | TOTP seed — generates 2FA codes indefinitely | **Password + this = full account takeover, 2FA defeated** |
| `GEMINI_API_KEY` | LLM calls | Billed quota abuse |
| `TELEGRAM_BOT_TOKEN` | Alerts | Send/read bot messages |
| `DISCORD_WEBHOOK_URL` | Alerts | Post to your channel |
| `DB_PASSWORD` | Postgres | Trade history and state |

### Rules

1. **Never commit `.env`.** It is in `.gitignore`; keep it there. Use `.env.example`
   as the template — it contains placeholders only.
2. **Do not bake secrets into Docker images.** Compose injects them at runtime via
   `env_file: .env`. A `.dockerignore` excludes `.env` from the build context so a
   `COPY . .` cannot capture it. Anything baked into an image is recoverable with
   `docker history` by anyone who can pull it.
3. **Transfer `.env` out of band.** `scp`/`rsync` over SSH, or type it on the server.
   Never through a git remote, chat, or CI log.
4. **Rotate on any suspicion.** Kite: regenerate the API secret in the Kite developer
   console, change the account password, and **re-provision the TOTP authenticator** —
   rotating the password alone leaves the TOTP seed valid.
5. **`.env` should be `chmod 600`**, owned by the user running the stack.

---

## Unattended auto-login (highest-risk feature)

Kite Connect access tokens expire daily and there is **no refresh token**, so a system
that trades unattended has to log in again every morning. TradeMind's optional
auto-auth task does this by driving the Zerodha login with your password and a TOTP
code generated from `KITE_TOTP_SECRET`.

**This means your broker password and a permanent 2FA seed sit in plaintext on the
host.** Anyone who reads that file owns the brokerage account, and two-factor
authentication does not stop them — the seed *is* the second factor.

It is **disabled by default** (`KITE_AUTO_AUTH_ENABLED=false`). Before you enable it:

- Only enable it on a host **you alone** control. Never on shared, managed, or
  multi-tenant infrastructure, and never in CI.
- Keep `TRADING_MODE=paper`. Then a compromise leaks credentials but this system is not
  the thing placing orders.
- Prefer SSH-key-only login, no password SSH, no root login, and a host firewall.
- Consider whether daily manual login is genuinely worse for you than this trade-off.
  For most users it is not.

If you don't need unattended operation, leave it off and use
`GET /api/auth/kite/login`.

---

## The API has no authentication

**This is the most important operational caveat in the project.**

The FastAPI backend exposes **no authentication or authorization whatsoever**. It is
designed on the assumption that only `localhost` can reach it. Anyone who can reach
port 5000 can, without a credential:

- start and stop trading agents — `POST /api/agents/{id}/start`
- create, modify, and delete agents — `POST`/`PUT`/`DELETE /api/agents/{id}`
- change risk limits — `PUT /api/risk/config`
- read all trades, positions, and P&L
- trigger the Kite OAuth flow and reconnect the ticker

In `live` mode that is remote control of a brokerage account by an unauthenticated
caller.

Because of this, **every port in `docker-compose.yml` is bound to `127.0.0.1`** rather
than to all interfaces:

```yaml
ports:
  - "127.0.0.1:5000:5000"
```

That default matters more than it looks. **On a cloud host, Docker's port publishing
writes iptables rules that bypass host firewalls like `ufw`** — a `ufw deny` does *not*
protect a container port published to `0.0.0.0`. Binding to loopback is what actually
keeps it unreachable.

### Deploy safely

- **Keep the loopback bindings.** Widening them to `0.0.0.0` puts unauthenticated
  brokerage control on the public internet. If you widen them anyway, enforce access at
  the cloud provider's network layer (security list / security group), not just the
  host firewall.
- Reach the dashboard over an **SSH tunnel**:
  ```bash
  ssh -L 3000:localhost:3000 -L 5000:localhost:5000 user@<host>
  ```
  Then open http://localhost:3000 locally. Nothing is publicly reachable.
- If you genuinely must expose it, put an authenticating reverse proxy in front (mTLS,
  OIDC, or at minimum HTTP basic auth over TLS). Do not rely on obscurity.

[`docs/DEPLOY_ORACLE_CLOUD.md`](docs/DEPLOY_ORACLE_CLOUD.md) walks through a hardened
single-host deployment using the SSH-tunnel approach.

Adding real authentication to the API is a welcome and high-value contribution.

---

## Other hardening notes

- **CORS** is restricted to `http://localhost:3000` in `app/main.py`. If you change the
  frontend origin, set it explicitly — do not use `allow_origins=["*"]` with
  `allow_credentials=True`.
- **Access tokens in Redis.** The daily Kite token is cached in Redis so Celery workers
  can use it. Never expose Redis (`6379`/`6380`) beyond the Docker network — a reachable
  Redis hands out a live trading token, and it has no password set.
- **Database defaults.** `trademind_dev` is a development password. Set a real
  `DB_PASSWORD` for anything long-lived.
- **Postgres and Redis ports** are published for local debugging (`5433`, `6380`), also
  loopback-only. Remove the mappings entirely if you don't need `psql`/`redis-cli` from
  the host.
- **LLM inputs.** The reasoning and sentiment agents send market data and news text to
  Google Gemini. Assume anything reaching those agents leaves your infrastructure.
- **Dependencies.** Run `pip-audit` / `npm audit` before deploying; this repo pins
  minimum versions, not exact ones.

---

## Supported versions

This project is pre-1.0 and unversioned in practice. Only the latest `main` receives
security fixes.
