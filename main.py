from fastapi import FastAPI, Request

from bot.handlers import process_update
from api.check_notifications import run_notification_check


app = FastAPI(title="PMMP Notifications Bot", version="1.0.0")


@app.get("/")
async def healthcheck():
    return {"ok": True, "service": "pmmp-notifications-bot"}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    process_update(update)
    return {"ok": True}


@app.get("/cron/check-notifications")
async def cron_check_notifications():
    return run_notification_check()
