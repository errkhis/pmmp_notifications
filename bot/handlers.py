import logging
import os
import re
from typing import Any

from database import (
    can_create_bid_watch,
    DatabaseNotConfigured,
    count_users,
    grant_premium,
    list_pending_bid_watches,
    set_free,
    stop_bid_watch,
    update_bid_watch_title,
    upsert_telegram_user,
    watch_bid_result,
)
from scraper import build_consultation_url, consultation_meta_from_url, scrape_consultation

from .messages import (
    account_status_message,
    active_watch_limit_message,
    database_error_message,
    esc,
    help_message,
    notification_list_message,
    remove_watch_markup,
    watch_created_message,
    welcome_message,
)
from .telegram import configure_public_commands, send


log = logging.getLogger(__name__)

TELEGRAM_ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_ID", "").strip()
TELEGRAM_ADMIN_USERNAME = os.environ.get("TELEGRAM_ADMIN_USERNAME", "").strip().lstrip("@")


def process_update(update: dict[str, Any]) -> None:
    callback = update.get("callback_query")
    if callback:
        process_callback(callback)
        return

    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    if handle_admin_command(chat_id, text, message):
        return
    if text.startswith("/start"):
        handle_start(chat_id, message)
        return
    if text.startswith("/help"):
        send(chat_id, help_message(TELEGRAM_ADMIN_USERNAME, is_admin(message)))
        return
    if text.startswith("/me") or text.startswith("/subscription"):
        handle_account(chat_id, message)
        return
    if text.startswith("/notifications") or text.startswith("/watchlist"):
        handle_notifications(chat_id, message)
        return

    url = extract_url(message)
    if not url:
        if not text.startswith("/"):
            send(chat_id, "⚠️ Send a valid <b>marchespublics.gov.ma</b> consultation link.")
        return
    handle_watch_request(chat_id, message, url)


def handle_start(chat_id: int, message: dict[str, Any]) -> None:
    user = None
    try:
        user = upsert_telegram_user(message.get("from") or {"id": chat_id})
    except DatabaseNotConfigured:
        log.warning("Database not configured during /start")
    except Exception:
        log.exception("Failed to upsert user during /start")
    try:
        configure_public_commands()
    except Exception:
        log.exception("Failed to configure Telegram commands")
    send(chat_id, welcome_message(user, TELEGRAM_ADMIN_USERNAME))


def handle_account(chat_id: int, message: dict[str, Any]) -> None:
    sender = message.get("from") or {}
    if not sender.get("id"):
        send(chat_id, "❌ Unable to identify your Telegram user id.")
        return
    try:
        user = upsert_telegram_user(sender)
        send(chat_id, account_status_message(user))
    except DatabaseNotConfigured:
        send(chat_id, database_error_message())
    except Exception as exc:
        log.exception("Account command error")
        send(chat_id, f"❌ <b>Error:</b> {esc(str(exc)[:400])}")


def handle_notifications(chat_id: int, message: dict[str, Any]) -> None:
    sender = message.get("from") or {}
    if not sender.get("id"):
        send(chat_id, "❌ Unable to identify your Telegram user id.")
        return
    try:
        user = upsert_telegram_user(sender)
        watches = list_pending_bid_watches(user.telegram_id)
        send(chat_id, notification_list_message(watches), reply_markup=remove_watch_markup(watches) if watches else None)
    except DatabaseNotConfigured:
        send(chat_id, database_error_message())
    except Exception as exc:
        log.exception("Notifications command error")
        send(chat_id, f"❌ <b>Error:</b> {esc(str(exc)[:400])}")


def handle_admin_command(chat_id: int, text: str, message: dict[str, Any]) -> bool:
    admin_commands = ("/premium", "/free", "/users")
    if not text.startswith(admin_commands):
        return False
    if not is_admin(message):
        send(chat_id, "⛔ This command is reserved for the administrator.")
        return True
    if text.startswith("/users"):
        try:
            send(chat_id, f"👥 Registered users: <b>{count_users()}</b>")
        except DatabaseNotConfigured:
            send(chat_id, database_error_message())
        except Exception as exc:
            log.exception("Users command error")
            send(chat_id, f"❌ <b>Error:</b> {esc(str(exc)[:400])}")
        return True

    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        send(chat_id, "Format: <code>/premium TELEGRAM_ID [years]</code> or <code>/free TELEGRAM_ID</code>")
        return True
    telegram_id = int(parts[1])
    admin_id = int((message.get("from") or {}).get("id"))
    try:
        if text.startswith("/premium"):
            years = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
            user = grant_premium(telegram_id, years, admin_telegram_id=admin_id)
            send(
                chat_id,
                "✅ Premium activated\n"
                f"User ID: <code>{user.telegram_id}</code>\n"
                f"Valid until: <b>{user.premium_expires_at.strftime('%Y-%m-%d')}</b>",
            )
        else:
            user = set_free(telegram_id, admin_telegram_id=admin_id)
            send(chat_id, f"✅ Free plan restored\nUser ID: <code>{user.telegram_id}</code>")
    except DatabaseNotConfigured:
        send(chat_id, database_error_message())
    except Exception as exc:
        log.exception("Admin command error")
        send(chat_id, f"❌ <b>Error:</b> {esc(str(exc)[:400])}")
    return True


def handle_watch_request(chat_id: int, message: dict[str, Any], url: str) -> None:
    sender = message.get("from") or {"id": chat_id}
    reference, org = consultation_meta_from_url(url)
    if not reference:
        send(chat_id, "❌ Invalid consultation URL.")
        return
    try:
        user = upsert_telegram_user(sender)
        if not can_create_bid_watch(user, reference, org or ""):
            send(chat_id, active_watch_limit_message(TELEGRAM_ADMIN_USERNAME))
            return
        title = None
        try:
            title = (scrape_consultation(url).object or "").strip() or None
        except Exception:
            log.exception("Failed to fetch consultation title for watch %s", reference)
        watch_bid_result(user.telegram_id, build_consultation_url(reference, org or ""), reference, org or "", title)
        send(chat_id, watch_created_message(reference, title))
    except DatabaseNotConfigured:
        send(chat_id, database_error_message())
    except Exception as exc:
        log.exception("Watch creation error")
        send(chat_id, f"❌ <b>Error:</b> {esc(str(exc)[:400])}")


def process_callback(callback: dict[str, Any]) -> None:
    message = callback.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    sender = callback.get("from") or {}
    data = callback.get("data") or ""
    if not chat_id:
        return
    if data.startswith("unwatch:"):
        try:
            watch_id = int(data.split(":", 1)[1])
        except ValueError:
            send(chat_id, "❌ Unknown notification.")
            return
        try:
            user = upsert_telegram_user(sender or {"id": chat_id})
            watch = stop_bid_watch(user.telegram_id, watch_id)
            if not watch:
                send(chat_id, "❌ Notification not found or already removed.")
                return
            send(chat_id, f"✅ Notification removed for <b>{esc(watch.consultation_reference)}</b>.")
        except DatabaseNotConfigured:
            send(chat_id, database_error_message())
        except Exception as exc:
            log.exception("Unwatch callback error")
            send(chat_id, f"❌ <b>Error:</b> {esc(str(exc)[:400])}")


def extract_url(message: dict[str, Any]) -> str | None:
    text = message.get("text") or message.get("caption") or ""
    for entity in message.get("entities") or []:
        if entity.get("type") == "text_link":
            url = entity.get("url", "")
            if "marchespublics.gov.ma" in url:
                return url
    for match in re.findall(r"https?://[^\s]+", text):
        if "marchespublics.gov.ma" in match:
            return match.rstrip(".,)")
    return None


def is_admin(message: dict[str, Any]) -> bool:
    if not TELEGRAM_ADMIN_ID:
        return False
    sender = message.get("from") or {}
    return str(sender.get("id", "")) == TELEGRAM_ADMIN_ID
