"""Quick notification smoke test.

Run inside the container (no inline-code quoting headaches):

    docker compose exec celery-worker python -m app.notifications.test_notify

Prints which channels are enabled and sends one "System Ready" alert to each.
"""

import asyncio

from app.notifications.telegram import get_bot


async def _main() -> None:
    bot = get_bot()
    print("telegram_enabled:", bot.telegram_enabled)
    print("discord_enabled :", bot.discord_enabled)
    if not bot.enabled:
        print(
            "No channel configured — set DISCORD_WEBHOOK_URL (or TELEGRAM_*) "
            "in .env, then `docker compose up -d`."
        )
        return
    await bot.notify_system_ready()
    await bot.close()
    print("Sent test alert. Check your channel.")


if __name__ == "__main__":
    asyncio.run(_main())
