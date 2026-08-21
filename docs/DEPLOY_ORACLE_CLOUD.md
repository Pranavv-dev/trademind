# Deploy TradeMind on Oracle Cloud (Always Free)

A one-time setup to run TradeMind 24/7 on a free Oracle Cloud ARM VM, so it trades
9:15 AM–3:30 PM IST without your Mac being awake. After this, you can shut your Mac
off whenever — the VM keeps running.

**Total cost: ₹0** (Oracle "Always Free" ARM shapes are never billed).

---

## Part A — Create the free VM (Oracle web console)

1. Sign up at **https://cloud.oracle.com** → "Start for free".
   - Pick **Home Region = India (Mumbai)** or **India (Hyderabad)** — this is permanent, choose an India region.
   - A card is required for identity verification. **Always Free shapes are never charged.** (A tiny temporary authorization hold may appear and reverse.)

2. Once in the console: **Menu → Compute → Instances → Create Instance**.
   - **Name:** `trademind`
   - **Image:** Canonical **Ubuntu 22.04** (make sure it's the **aarch64 / ARM** build)
   - **Shape:** click *Change Shape* → **Ampere (ARM)** → `VM.Standard.A1.Flex` →
     set **2 OCPUs** and **12 GB RAM** (well within the Always Free allowance of 4 OCPU / 24 GB).
   - **SSH keys:** "Generate a key pair for me" → **download both keys**, OR paste your own
     public key. (To make your own on the Mac: `ssh-keygen -t ed25519` then paste
     `~/.ssh/id_ed25519.pub`.)
   - Leave networking default (it creates a VCN with SSH open). Click **Create**.

3. When it's running, copy the **Public IP address** (e.g. `140.238.x.x`).

> If "Out of capacity" appears for ARM, just retry Create a few times or try the other
> India region — free ARM capacity fluctuates.

---

## Part B — One-time server setup (SSH in from your Mac)

```bash
# from your Mac. Oracle's default Ubuntu user is "ubuntu".
ssh ubuntu@<PUBLIC_IP>            # add -i ~/.ssh/your_key if you used a custom key
```

Then on the server:

```bash
# 1. Update + install Docker (official script installs docker + compose plugin, ARM-aware)
sudo apt update && sudo apt -y upgrade
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
sudo systemctl enable --now docker      # start Docker on every boot

# 2. IST timezone (for readable logs; Celery uses IST internally regardless)
sudo timedatectl set-timezone Asia/Kolkata

# 3. A little swap helps the first build on a small VM
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 4. apply the docker group (or just log out/in)
newgrp docker
```

---

## Part C — Get the code onto the VM

Run this **on your Mac** (not the server). It copies the project including your `.env`,
skipping the bulky regenerable folders:

```bash
rsync -avz \
  --exclude '.git' --exclude 'node_modules' --exclude '.next' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.venv' \
  ~/path/to/trademind/ \
  ubuntu@<PUBLIC_IP>:~/trademind/
```

> This carries your `.env` (with the Kite + Discord secrets) straight to the VM over the
> encrypted SSH connection. Don't commit `.env` to git; rsync is the safe transfer.

If you'd rather use git: push the repo to a private GitHub repo, `git clone` it on the
VM, then create `.env` on the VM by hand (`nano ~/trademind/.env`) since it's gitignored.

---

## Part D — Build, migrate, launch (on the server)

```bash
cd ~/trademind

# Build images (ARM build — first time takes ~5-10 min on the free VM)
docker compose build

# Start data stores, run migrations, then start everything
docker compose up -d db redis
docker compose run --rm backend alembic upgrade head
docker compose up -d

# One-time backtest universe seed (optional, non-blocking)
docker compose exec backend python -m app.db.seed_membership
```

---

## Part E — Verify

```bash
# Everything "Up"?
docker compose ps

# Discord works?
docker compose exec celery-worker python -m app.notifications.test_notify
#   -> expect discord_enabled: True  + a "System Ready" message in Discord

# Auto-login works on the VM?
docker compose exec celery-worker celery -A app.tasks.celery_app call app.tasks.auto_auth.run_auto_auth
docker compose logs celery-worker --tail 30 | grep -E "auto_login|auto_auth"
docker compose exec redis redis-cli GET kite:access_token     # should print a token
```

When Discord pings "System Ready" and the auth log shows `auto_login_success`, you're done.

---

## Part F — Viewing the dashboard (securely, no open ports)

Don't expose ports 3000/5000 to the internet. Instead tunnel them over SSH when you want
to look. Run this **on your Mac**:

```bash
ssh -L 3000:localhost:3000 -L 5000:localhost:5000 ubuntu@<PUBLIC_IP>
```

Leave that session open, then in your Mac browser go to **http://localhost:3000**.
The dashboard and API are served from the VM through the encrypted tunnel. Close the SSH
session when done — nothing is publicly reachable.

---

## What happens now (every weekday, IST) — fully unattended

| Time | What |
|---|---|
| 8:00 AM | auto-login via TOTP → token in Redis → Discord "System Ready" |
| 8:30 AM | pre-market auth check |
| 9:15 AM–3:30 PM | scans + trades, Discord pings each |
| 3:45 PM | EOD report to Discord |
| 4:00 / 4:30 PM | expectancy snapshot + next-day data sync |

The VM runs 24/7. `restart: unless-stopped` + `systemctl enable docker` mean it all comes
back automatically after any VM reboot. **Your Mac can be off.**

---

## Notes & gotchas

- **Kite redirect URL:** no change needed. The auto-login captures the `request_token`
  from the redirect chain in-process; the redirect URL doesn't have to be reachable from
  the VM. Keep your Kite app's registered redirect URL as-is.
- **ARM build hiccup:** if `docker compose build` fails on a Python wheel (rare on Ampere),
  note the package and ping me — there's usually a one-line fix.
- **Updating code later:** re-run the Part C rsync from your Mac, then on the VM
  `docker compose build <changed services> && docker compose up -d`.
  For schema changes also run `docker compose run --rm backend alembic upgrade head`.
- **Security:** the VM holds your `.env` secrets. Oracle defaults to SSH-key-only login
  (no password) — keep your private key safe and that's enough for paper trading.
- **Data safety:** same as local — `docker compose down` keeps data (named volumes
  `pgdata`/`redisdata`). Never `down -v`.
```
