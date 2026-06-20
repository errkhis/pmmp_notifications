import os

import requests as http


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""


def tg(method: str, payload: dict) -> None:
    if not TG:
        return
    try:
        http.post(f"{TG}/{method}", json=payload, timeout=15)
    except Exception:
        return


def send(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg("sendMessage", payload)


def configure_public_commands() -> None:
    commands = [
        {"command": "start", "description": "Show welcome message"},
        {"command": "help", "description": "Show help"},
        {"command": "me", "description": "Show your account"},
        {"command": "notifications", "description": "Show active watches"},
    ]
    tg("setMyCommands", {"commands": commands})
