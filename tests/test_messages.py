import unittest
from datetime import datetime, timedelta, timezone

from bot.messages import active_watch_limit_message, account_status_message, notification_list_message, results_published_message
from database import BidWatch, User


class MessageTests(unittest.TestCase):
    def test_account_message_premium(self):
        user = User(
            telegram_id=1,
            username="demo",
            first_name="Demo",
            plan="premium",
            premium_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        self.assertIn("Premium", account_status_message(user))

    def test_notification_list_message(self):
        watch = BidWatch(
            id=1,
            telegram_id=1,
            consultation_reference="REF1",
            org_acronyme="x",
            consultation_url="https://example.com",
            consultation_title="Example consultation",
            status="watching",
            created_at=None,
            updated_at=None,
            last_checked_at=None,
        )
        text = notification_list_message([watch])
        self.assertIn("Example consultation", text)

    def test_results_published_message(self):
        watch = BidWatch(
            id=1,
            telegram_id=1,
            consultation_reference="REF1",
            org_acronyme="x",
            consultation_url="https://example.com",
            consultation_title="Example consultation",
            status="watching",
            created_at=None,
            updated_at=None,
            last_checked_at=None,
        )
        text = results_published_message(watch)
        self.assertIn("Results published", text)
        self.assertIn("https://example.com", text)

    def test_active_watch_limit_message(self):
        text = active_watch_limit_message("notif_admin")
        self.assertIn("1", text)
        self.assertIn("@notif_admin", text)


if __name__ == "__main__":
    unittest.main()
