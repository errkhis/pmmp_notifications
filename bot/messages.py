from database import BidWatch, FREE_ACTIVE_WATCH_LIMIT, User


def esc(value) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_date(value):
    return value.strftime("%Y-%m-%d") if value else "—"


def fmt_datetime(value):
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


def admin_contact(admin_username: str) -> str:
    return f"@{admin_username.lstrip('@')}" if admin_username else "the administrator"


def welcome_message(user: User | None, admin_username: str) -> str:
    lines = [
        "🔔 <b>PMMP Notifications Bot</b>",
        "",
        "Send one <b>marchespublics.gov.ma</b> consultation link.",
        "The bot will watch that consultation and notify you when results are fully published.",
        "",
        f"Free plan: <b>{FREE_ACTIVE_WATCH_LIMIT}</b> active watch.",
        f"Premium contact: <b>{esc(admin_contact(admin_username))}</b>.",
    ]
    if user:
        lines.extend(["", account_status_message(user)])
    return "\n".join(lines)


def help_message(admin_username: str, is_admin: bool) -> str:
    lines = [
        "📖 <b>Commands</b>",
        "/start - Show welcome message",
        "/help - Show commands",
        "/me - Show your plan",
        "/subscription - Alias of /me",
        "/notifications - Show your active watches",
        "/watchlist - Alias of /notifications",
        "",
        "Send a consultation link directly to create or refresh a notification watch.",
        f"Free plan: <b>{FREE_ACTIVE_WATCH_LIMIT}</b> active watch at a time.",
        "",
        f"Premium contact: <b>{esc(admin_contact(admin_username))}</b>",
    ]
    if is_admin:
        lines.extend(["", "<b>Admin</b>", "/premium TELEGRAM_ID [years]", "/free TELEGRAM_ID", "/users"])
    return "\n".join(lines)


def account_status_message(user: User) -> str:
    if user.is_premium:
        return (
            "👤 <b>Your account</b>\n"
            "Plan: <b>Premium</b>\n"
            f"Valid until: <b>{fmt_date(user.premium_expires_at)}</b>\n"
            "Notifications: <b>enabled</b>"
        )
    return (
        "👤 <b>Your account</b>\n"
        "Plan: <b>Free</b>\n"
        f"Active watch limit: <b>{FREE_ACTIVE_WATCH_LIMIT}</b>"
    )


def database_error_message() -> str:
    return (
        "❌ <b>Database is not configured.</b>\n"
        "Add `DATABASE_URL` or `POSTGRES_URL` to your deployment environment."
    )


def active_watch_limit_message(admin_username: str) -> str:
    return (
        "🔒 <b>Free plan limit reached</b>\n\n"
        f"The free plan allows only <b>{FREE_ACTIVE_WATCH_LIMIT}</b> active watch.\n"
        "Remove an existing watch or contact "
        f"<b>{esc(admin_contact(admin_username))}</b> to activate Premium."
    )


def watch_created_message(reference: str, title: str | None) -> str:
    return (
        "🔔 Notification activated.\n\n"
        "I will notify you when results are fully published for "
        f"<b>{esc(title or reference)}</b>."
    )


def notification_list_message(watches: list[BidWatch]) -> str:
    if not watches:
        return "🔕 You do not have any active notifications."
    lines = ["🔔 <b>Active notifications</b>", ""]
    for watch in watches:
        title = esc(watch.consultation_title or watch.consultation_reference)
        lines.append(f"• <b>{title}</b>")
        lines.append(f"  Link: <a href=\"{esc(watch.consultation_url)}\">Open consultation</a>")
        lines.append(f"  Last check: <b>{fmt_datetime(watch.last_checked_at)}</b>")
    return "\n".join(lines)


def remove_watch_markup(watches: list[BidWatch]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "❌ Remove", "callback_data": f"unwatch:{watch.id}"}]
            for watch in watches
        ]
    }


def results_published_message(watch: BidWatch) -> str:
    title = esc(watch.consultation_title or watch.consultation_reference)
    return (
        "🔔 <b>Results published</b>\n\n"
        f"Results are now fully published for <b>{title}</b>.\n"
        f"URL: <code>{esc(watch.consultation_url)}</code>"
    )
