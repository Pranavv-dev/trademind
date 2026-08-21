from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery = Celery(
    "trademind",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone=settings.timezone,
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Route tasks to specific queues if needed later
    task_default_queue="default",
)

celery.conf.beat_schedule = {
    # Unattended Kite auto-login: 8:00 AM IST on trading days. Logs in via TOTP,
    # stores the fresh token in Redis, wakes the WS ticker. Runs BEFORE pre-market
    # (8:30) and market-scan (9:15) so the whole day runs without manual auth.
    "auto-auth": {
        "task": "app.tasks.auto_auth.run_auto_auth",
        "schedule": crontab(minute=0, hour=8, day_of_week="1-5"),
    },
    # Market scan: every 15 minutes during market hours (9:15 AM - 3:25 PM IST, Mon-Fri)
    "market-scan": {
        "task": "app.tasks.scanner.run_scan_cycle",
        "schedule": crontab(minute="*/15", hour="9-15", day_of_week="1-5"),
    },
    # Pre-market analysis: 8:30 AM IST on trading days
    "pre-market": {
        "task": "app.tasks.scanner.run_pre_market",
        "schedule": crontab(minute=30, hour=8, day_of_week="1-5"),
    },
    # EOD report: 3:45 PM IST on trading days
    "eod-report": {
        "task": "app.tasks.eod.run_eod_report",
        "schedule": crontab(minute=45, hour=15, day_of_week="1-5"),
    },
    # Historical data sync: 4:30 PM IST daily
    "data-sync": {
        "task": "app.tasks.data_sync.sync_daily_candles",
        "schedule": crontab(minute=30, hour=16, day_of_week="1-5"),
    },
    # Position monitor: every 5 minutes during market hours — checks SL/TP
    "position-monitor": {
        "task": "app.tasks.position_monitor.check_positions",
        "schedule": crontab(minute="*/5", hour="9-15", day_of_week="1-5"),
    },
    # Intraday scanner: every 5 minutes — runs intraday_technical agents only
    "intraday-scan": {
        "task": "app.tasks.intraday_scanner.run_intraday_scan",
        "schedule": crontab(minute="*/5", hour="9-15", day_of_week="1-5"),
    },
    # Health check: every 10 minutes
    "health-check": {
        "task": "app.tasks.health.check_system_health",
        "schedule": 600,
    },
    # Nightly expectancy snapshot — 4:00 PM IST (right after EOD report, before data sync).
    # Computes rolling 30/60/90-day R-multiple expectancy per agent. Foundation
    # for every future tuning decision; without it, you can't tell which agents
    # actually make money.
    "expectancy-snapshot": {
        "task": "app.tasks.expectancy_job.compute_expectancy_snapshot",
        "schedule": crontab(minute=0, hour=16, day_of_week="1-5"),
    },
}

# Explicitly import all task modules so shared_task decorators register
import app.tasks.auto_auth  # noqa: F401, E402
import app.tasks.data_sync  # noqa: F401, E402
import app.tasks.eod  # noqa: F401, E402
import app.tasks.expectancy_job  # noqa: F401, E402
import app.tasks.health  # noqa: F401, E402
import app.tasks.intraday_scanner  # noqa: F401, E402
import app.tasks.position_monitor  # noqa: F401, E402
import app.tasks.scanner  # noqa: F401, E402
