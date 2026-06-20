import unittest
from unittest.mock import patch

from api.check_notifications import _has_complete_prices, _is_waiting_for_price
from bot import handlers
from scraper import Bidder, ConsultationData


class NotificationLogicTests(unittest.TestCase):
    def test_waiting_for_price_detects_pending_dash(self):
        bidder = Bidder(rank=1, name="A", admin_status="Admissible", financial_status="Admissible", price=None, price_before_raw="-", price_after_raw="")
        self.assertTrue(_is_waiting_for_price(bidder, use_after_prices=False))

    def test_waiting_for_price_ignores_empty_cells(self):
        bidder = Bidder(rank=1, name="A", admin_status="", financial_status="", price=None, price_before_raw="", price_after_raw="")
        self.assertFalse(_is_waiting_for_price(bidder, use_after_prices=False))

    def test_has_complete_prices_blocks_pending_bidder(self):
        data = ConsultationData(
            reference="1",
            object="x",
            estimated_price=None,
            estimated_price_currency="MAD",
            procedure="p",
            category="c",
            bidders=[Bidder(rank=1, name="A", admin_status="Admissible", financial_status="Admissible", price=None, price_before_raw="-", price_after_raw="")],
        )
        self.assertFalse(_has_complete_prices(data))

    def test_has_complete_prices_accepts_eliminated_bidder(self):
        data = ConsultationData(
            reference="1",
            object="x",
            estimated_price=None,
            estimated_price_currency="MAD",
            procedure="p",
            category="c",
            bidders=[Bidder(rank=1, name="A", admin_status="Écartée", financial_status="", price=None, price_before_raw="-", price_after_raw="")],
        )
        self.assertTrue(_has_complete_prices(data))

    @patch("bot.handlers.send")
    @patch("bot.handlers.watch_bid_result")
    @patch("bot.handlers.scrape_consultation")
    @patch("bot.handlers.can_create_bid_watch", return_value=True)
    @patch("bot.handlers.upsert_telegram_user")
    def test_handle_watch_request_creates_watch_without_immediate_notification(
        self,
        upsert_mock,
        _can_create_mock,
        scrape_mock,
        watch_mock,
        send_mock,
    ):
        user = type("User", (), {"telegram_id": 123})()
        upsert_mock.return_value = user
        consultation = ConsultationData(
            reference="1",
            object="Example consultation",
            estimated_price=None,
            estimated_price_currency="MAD",
            procedure="p",
            category="c",
            bidders=[
                Bidder(
                    rank=1,
                    name="A",
                    admin_status="Admissible",
                    financial_status="Admissible",
                    price=100.0,
                    price_before_raw="100,00",
                    price_after_raw="",
                )
            ],
        )
        scrape_mock.return_value = consultation
        watch = type(
            "Watch",
            (),
            {
                "id": 9,
                "telegram_id": 123,
                "consultation_reference": "REF1",
                "consultation_title": "Example consultation",
                "consultation_url": "https://example.com",
            },
        )()
        watch_mock.return_value = watch

        handlers.handle_watch_request(
            123,
            {"from": {"id": 123}},
            "https://www.marchespublics.gov.ma/index.php?page=entreprise.SuiviConsultation&refConsultation=REF1&orgAcronyme=test",
        )

        sent_text = send_mock.call_args[0][1]
        self.assertIn("Notification activated", sent_text)


if __name__ == "__main__":
    unittest.main()
