"""VayuNetra Telegram bot — citizen advisory.

Commands:
  /start    Consent flow → language → city → location precision → vuln tier.
  /aqi      One-shot advisory for the user's saved preferences.
  /lang     Change preferred language.
  /city     Change city (delhi/bengaluru).
  /vuln     Change vulnerability tier (general/elderly_children/asthmatic).
  /stop     Opt out and forget chat preferences.

A daily 07:00 IST job broadcasts advisories to every opted-in user, staggered
with ``asyncio.Semaphore(20)`` to stay clear of Telegram's per-bot rate limit.

The bot reaches the FastAPI ``/advisory`` endpoint over HTTP so the production
graph (DB → forecast → templates → RAG) stays the single source of truth.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from bots.telegram import store
from vayunetra.advisory import templates

log = logging.getLogger("vayunetra.bot")

API_BASE = os.environ.get("VAYUNETRA_API_BASE", "http://127.0.0.1:8000")
DEFAULT_CITY = "delhi"
SUPPORTED_CITIES: tuple[str, ...] = ("delhi", "bengaluru")
VULN_LABELS = {
    "general": "General population",
    "elderly_children": "Children / elderly",
    "asthmatic": "Asthma / COPD",
}

# IST 07:00 broadcast — explicit timezone so DST drift in deployment doesn't
# move the send window.
BROADCAST_TIME = dtime(hour=7, minute=0, tzinfo=ZoneInfo("Asia/Kolkata"))
BROADCAST_CONCURRENCY = 20


async def fetch_advisory(prefs: store.UserPrefs) -> dict[str, Any] | None:
    """Call /advisory with whatever location precision the user consented to."""
    params: dict[str, Any] = {
        "city": prefs.city,
        "lang": prefs.lang,
        "vuln_tier": prefs.vuln_tier,
    }
    if prefs.precision == "exact" and prefs.lat is not None and prefs.lon is not None:
        params["lat"] = prefs.lat
        params["lon"] = prefs.lon
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{API_BASE}/advisory", params=params)
        r.raise_for_status()
    except Exception as e:
        log.warning("advisory_fetch_failed", extra={"err": str(e)})
        return None
    return r.json()


def format_advisory(data: dict[str, Any]) -> str:
    lines = [f"*{data['headline']}*", data["advice"]]
    if data.get("citation_source"):
        lines.append("")
        lines.append(f"_Source: {data['citation_source']}_")
    return "\n".join(lines)


# --- Handlers -------------------------------------------------------------


def _lang_keyboard() -> list[list[dict[str, str]]]:
    """3 columns × 4 rows of language buttons."""
    btns = [
        {"text": f"{templates.LANGUAGE_NAMES[lc]}", "callback_data": f"lang:{lc}"}
        for lc in templates.LANGUAGES
    ]
    return [btns[i : i + 3] for i in range(0, len(btns), 3)]


def _city_keyboard() -> list[list[dict[str, str]]]:
    return [
        [
            {"text": c.title(), "callback_data": f"city:{c}"}
            for c in SUPPORTED_CITIES
        ]
    ]


def _precision_keyboard() -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "Ward (coarse)", "callback_data": "prec:ward"},
            {"text": "Pincode", "callback_data": "prec:pincode"},
            {"text": "Exact", "callback_data": "prec:exact"},
        ]
    ]


def _vuln_keyboard() -> list[list[dict[str, str]]]:
    return [
        [{"text": label, "callback_data": f"vuln:{tier}"}]
        for tier, label in VULN_LABELS.items()
    ]


async def cmd_start(update: Any, context: Any) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    chat_id = update.effective_chat.id
    store.upsert(chat_id)
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
            for row in _lang_keyboard()
        ]
    )
    await update.message.reply_text(
        "Namaste! I'll send you air-quality advisories.\n\n"
        "Pick your preferred language to begin. You can change it later with /lang.",
        reply_markup=markup,
    )


async def cmd_aqi(update: Any, context: Any) -> None:
    chat_id = update.effective_chat.id
    prefs = store.get(chat_id)
    if prefs is None or not prefs.opted_in:
        await update.message.reply_text(
            "Send /start first so I know which city + language to use."
        )
        return
    data = await fetch_advisory(prefs)
    if data is None:
        await update.message.reply_text("Advisory service unavailable, please retry shortly.")
        return
    await update.message.reply_markdown(format_advisory(data))


async def cmd_lang(update: Any, context: Any) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
            for row in _lang_keyboard()
        ]
    )
    await update.message.reply_text("Pick your language:", reply_markup=markup)


async def cmd_city(update: Any, context: Any) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
            for row in _city_keyboard()
        ]
    )
    await update.message.reply_text("Pick your city:", reply_markup=markup)


async def cmd_vuln(update: Any, context: Any) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
            for row in _vuln_keyboard()
        ]
    )
    await update.message.reply_text("Tell me which group applies to you:", reply_markup=markup)


async def cmd_stop(update: Any, context: Any) -> None:
    chat_id = update.effective_chat.id
    store.upsert(chat_id, opted_in=0)
    await update.message.reply_text("You're opted out. Daily messages will stop.")


async def on_callback(update: Any, context: Any) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id
    data = q.data or ""
    if ":" not in data:
        return
    kind, value = data.split(":", 1)

    if kind == "lang":
        store.upsert(chat_id, lang=value)
        markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
                for row in _city_keyboard()
            ]
        )
        await q.edit_message_text(
            f"Language set to {templates.LANGUAGE_NAMES.get(value, value)}.\n"
            f"Now pick your city:",
            reply_markup=markup,
        )
    elif kind == "city":
        store.upsert(chat_id, city=value)
        markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
                for row in _precision_keyboard()
            ]
        )
        await q.edit_message_text(
            f"City set to {value.title()}.\n"
            f"How precise can I be about your location?\n"
            f"• Ward — coarse, anonymous\n"
            f"• Pincode — neighbourhood-scale\n"
            f"• Exact — best forecast (you'll share GPS once)",
            reply_markup=markup,
        )
    elif kind == "prec":
        store.upsert(chat_id, precision=value)
        markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
                for row in _vuln_keyboard()
            ]
        )
        await q.edit_message_text(
            "Location precision saved.\nFinally, who's this for?",
            reply_markup=markup,
        )
    elif kind == "vuln":
        store.upsert(chat_id, vuln_tier=value, opted_in=1)
        await q.edit_message_text(
            "You're all set ✓\n\nUse /aqi for an immediate reading. "
            "I'll send a daily advisory at 07:00 IST. /stop to unsubscribe."
        )


# --- Broadcast job --------------------------------------------------------


async def broadcast(context: Any) -> None:
    """Send daily advisory to every opted-in user with bounded concurrency."""
    bot = context.bot
    users = store.opted_in()
    if not users:
        log.info("broadcast_no_users")
        return
    sem = asyncio.Semaphore(BROADCAST_CONCURRENCY)

    async def _send(u: store.UserPrefs) -> None:
        async with sem:
            data = await fetch_advisory(u)
            if data is None:
                return
            try:
                await bot.send_message(
                    chat_id=u.chat_id,
                    text=format_advisory(data),
                    parse_mode="Markdown",
                )
            except Exception as e:
                log.warning("broadcast_send_failed", extra={"chat": u.chat_id, "err": str(e)})

    await asyncio.gather(*[_send(u) for u in users])


def build_app(token: str):  # pragma: no cover - thin runtime wiring
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
    )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("aqi", cmd_aqi))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CommandHandler("city", cmd_city))
    app.add_handler(CommandHandler("vuln", cmd_vuln))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CallbackQueryHandler(on_callback))
    if app.job_queue is not None:
        app.job_queue.run_daily(broadcast, time=BROADCAST_TIME, name="daily_advisory")
    return app


def main() -> int:  # pragma: no cover - entrypoint
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        log.error("TELEGRAM_TOKEN is not set; refusing to start")
        return 2
    app = build_app(token)
    log.info("vayunetra-bot starting; api=%s", API_BASE)
    app.run_polling(close_loop=False)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
