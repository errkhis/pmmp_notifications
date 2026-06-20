import json
import logging
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import requests as http

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.messages import results_published_message
from database import DatabaseNotConfigured, claim_due_bid_watches, mark_bid_watch_error, mark_bid_watch_notified
from scraper import _parse_price_fr, scrape_consultation


logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""


def _json_response(handler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.end_headers()
    handler.wfile.write(body)


def _cron_secret() -> str:
    return os.environ.get("CRON_SECRET", "").strip()


def _is_authorized(path: str) -> bool:
    secret = _cron_secret()
    if not secret:
        return False
    query = parse_qs(urlparse(path).query)
    provided = (query.get("secret") or [""])[0]
    return provided == secret


def _send_notification(watch) -> None:
    payload = {
        "chat_id": watch.telegram_id,
        "text": results_published_message(watch),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    response = http.post(f"{TG}/sendMessage", json=payload, timeout=10)
    response.raise_for_status()


def _has_complete_prices(data) -> bool:
    lots = data.lots or [data]
    if not lots:
        return False
    for lot in lots:
        if not lot.bidders:
            return False
        use_after_prices = any(
            _parse_price_fr(getattr(bidder, "price_after_raw", "")) is not None
            for bidder in lot.bidders
        )
        if any(_is_waiting_for_price(bidder, use_after_prices) for bidder in lot.bidders):
            return False
    return True


def _is_waiting_for_price(bidder, use_after_prices: bool) -> bool:
    if bidder.price is not None:
        return False
    before = getattr(bidder, "price_before_raw", "")
    after = getattr(bidder, "price_after_raw", "")
    before_clean = before.strip()
    after_clean = after.strip()
    if not before_clean and not after_clean:
        return False
    if _is_eliminated_bidder(bidder):
        return False
    selected = after_clean if use_after_prices else before_clean
    return selected == "-"


def _is_eliminated_bidder(bidder) -> bool:
    text = f"{getattr(bidder, 'admin_status', '')} {getattr(bidder, 'financial_status', '')}"
    norm = _norm_status(text)
    return "ecarte" in norm or "rejet" in norm


def _norm_status(text: str) -> str:
    return (
        text.strip()
        .lower()
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("â", "a")
        .replace("ç", "c")
    )


def run_notification_check() -> dict:
    now = datetime.utcnow()
    limit = int(os.environ.get("NOTIFICATION_CHECK_BATCH_SIZE", "10"))
    watches = claim_due_bid_watches(limit)
    notified = 0
    errors = 0
    for watch in watches:
        try:
            data = scrape_consultation(watch.consultation_url)
            if not _has_complete_prices(data):
                continue
            _send_notification(watch)
            mark_bid_watch_notified(watch.id)
            notified += 1
        except Exception as exc:
            errors += 1
            mark_bid_watch_error(watch.id, str(exc))
            log.exception("Notification check failed for watch %s", watch.id)
    return {
        "ok": True,
        "checked": len(watches),
        "notified": notified,
        "errors": errors,
        "checked_at": now.isoformat() + "Z",
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not _is_authorized(self.path):
            _json_response(self, 401, {"ok": False, "error": "unauthorized"})
            return
        try:
            _json_response(self, 200, run_notification_check())
        except DatabaseNotConfigured:
            _json_response(self, 500, {"ok": False, "error": "database_not_configured"})
        except Exception as exc:
            log.exception("Notification endpoint error")
            _json_response(self, 500, {"ok": False, "error": str(exc)[:400]})

    def log_message(self, fmt, *args):
        return
