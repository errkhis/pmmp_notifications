import unittest
from datetime import datetime, timedelta, timezone

from database import FREE_ACTIVE_WATCH_LIMIT, User


class DatabaseLogicTests(unittest.TestCase):
    def test_premium_property(self):
        user = User(
            telegram_id=1,
            username="demo",
            first_name="Demo",
            plan="premium",
            premium_expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        self.assertTrue(user.is_premium)

    def test_free_property(self):
        user = User(
            telegram_id=1,
            username="demo",
            first_name="Demo",
            plan="free",
            premium_expires_at=None,
        )
        self.assertFalse(user.is_premium)

    def test_free_active_watch_limit_constant(self):
        self.assertEqual(FREE_ACTIVE_WATCH_LIMIT, 1)


if __name__ == "__main__":
    unittest.main()
