import unittest

from api.check_notifications import _has_complete_prices, _is_waiting_for_price
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


if __name__ == "__main__":
    unittest.main()
