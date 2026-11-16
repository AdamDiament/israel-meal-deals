"""Run: python3 -m unittest discover tests"""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import planner  # noqa: E402
import shufersal  # noqa: E402

PRICES = """<Root><Items>
<Item><ItemCode>111</ItemCode><ItemName>פסטה ספגטי 400</ItemName><ManufactureName>X</ManufactureName>
<Quantity>400</Quantity><UnitQty>גרם</UnitQty><ItemPrice>7.90</ItemPrice><UnitOfMeasurePrice>1.98</UnitOfMeasurePrice><bIsWeighted>0</bIsWeighted></Item>
<Item><ItemCode>222</ItemCode><ItemName>עגבניה</ItemName><ManufactureName></ManufactureName>
<Quantity>1</Quantity><UnitQty>קילוגרם</UnitQty><ItemPrice>8.90</ItemPrice><UnitOfMeasurePrice>8.90</UnitOfMeasurePrice><bIsWeighted>1</bIsWeighted></Item>
<Item><ItemCode>333</ItemCode><ItemName>שמפו לשיער</ItemName><ManufactureName>Y</ManufactureName>
<Quantity>1</Quantity><UnitQty>יחידות</UnitQty><ItemPrice>20.00</ItemPrice><UnitOfMeasurePrice>20</UnitOfMeasurePrice><bIsWeighted>0</bIsWeighted></Item>
<Item><ItemCode>444</ItemCode><ItemName>פסטרמה במשקל</ItemName><ManufactureName></ManufactureName>
<Quantity>1</Quantity><UnitQty>קילוגרם</UnitQty><ItemPrice>95.00</ItemPrice><UnitOfMeasurePrice>95</UnitOfMeasurePrice><bIsWeighted>1</bIsWeighted></Item>
</Items></Root>"""


def promo(pid, desc, code, min_qty, total, end="2026-11-28T02:59:00.000", club="0 - כלל הלקוחות",
          coupon="", weighted="0"):
    return f"""<Promotion><PromotionID>{pid}</PromotionID><PromotionDescription>{desc}</PromotionDescription>
<PromotionStartDateTime>2026-11-01T00:00:00.000</PromotionStartDateTime><PromotionEndDateTime>{end}</PromotionEndDateTime>
<ClubID>{club}</ClubID><AdditionalIsCoupon>{coupon}</AdditionalIsCoupon><Groups><Group><MinPurchaseAmount></MinPurchaseAmount>
<PromotionItems><PromotionItem><ItemCode>{code}</ItemCode><MinQty>{min_qty}</MinQty><DiscountedPrice>{total}</DiscountedPrice>
<bIsWeighted>{weighted}</bIsWeighted></PromotionItem></PromotionItems></Group></Groups></Promotion>"""


PROMOS = "<Root><Promotions>" + "".join([
    promo(1, "4ב10 פסטות", "111", "4.00", "10.00"),
    promo(2, "expired", "111", "1.00", "1.00", end="2026-01-01T00:00:00.000"),
    promo(3, "coupon", "111", "1.00", "0.50", coupon="1"),
    promo(4, "club only", "111", "1.00", "1.00", club="2 - מועדון"),
    promo(5, "2ב30 per pack", "222", "2.00", "30.00", weighted="1"),
    promo(6, "250ב22 פסטרמה", "444", "0.25", "22.00", weighted="1"),
    promo(7, "2ב24 שמפו", "333", "2.00", "24.00"),
]) + "</Promotions></Root>"
NOW = datetime(2026, 11, 16, 20)


class Core(unittest.TestCase):
    def setUp(self):
        self.prices = shufersal.parse_prices(PRICES)
        self.promos = shufersal.parse_promos(PROMOS, now=NOW)

    def test_promo_filtering(self):
        self.assertEqual(self.promos["111"]["eff_unit"], 2.50)   # 4 for 10, not expired/coupon/club
        self.assertNotIn("222", self.promos)                      # per-pack deal on by-weight item dropped
        self.assertEqual(self.promos["444"]["eff_unit"], 88.0)    # 250 g for 22 -> 88/kg

    def test_club_toggle(self):
        p = shufersal.parse_promos(PROMOS, now=NOW, include_club=True)
        self.assertEqual(p["111"]["eff_unit"], 1.0)

    def test_line_cost(self):
        pasta, deal = self.prices["111"], self.promos["111"]
        self.assertEqual(shufersal.line_cost(pasta, deal, 3), (23.7, False))   # below min qty
        self.assertEqual(shufersal.line_cost(pasta, deal, 5), (17.9, True))    # 10 + 7.90
        self.assertEqual(shufersal.line_cost(self.prices["222"], None, 1.5), (13.35, False))
        self.assertEqual(shufersal.line_cost(self.prices["444"], self.promos["444"], 0.5), (44.0, True))

    def test_catalogue_excludes_non_food(self):
        lines = [l for _, l in planner.build_candidates(self.prices, self.promos)]
        self.assertFalse(any("שמפו" in l for l in lines))
        self.assertTrue(any(l.startswith("111|") and "DEAL" in l for l in lines))

    def test_price_list_and_budget_retry(self):
        replies = iter([
            '{"summary":"a","shopping_list":[{"code":"111","qty":8},{"code":"222","qty":3},{"code":"999","qty":1}],"meal_plan":[]}',
            '```json\n{"summary":"b","shopping_list":[{"code":"111","qty":4},{"code":"222","qty":1}],"meal_plan":[]}\n```',
        ])
        orig = planner.call_claude
        planner.call_claude = lambda *a, **k: next(replies)
        try:
            r = planner.make_plan({"budget": 25}, self.prices, self.promos, "k", log=lambda *_: None)
        finally:
            planner.call_claude = orig
        self.assertEqual(r["summary"], "b")
        self.assertEqual(r["shopping"]["total"], 18.9)    # 10 + 8.90
        self.assertTrue(r["within_budget"])
        self.assertEqual(r["shopping"]["saved"], 21.6)    # 31.60 - 10

    def test_stores(self):
        xml = ('<?xml version="1.0" encoding="UTF-8"?><Chain><SubChains><SubChain><SubChainName>שופרסל שלי</SubChainName>'
               '<Stores><Store><StoreID>2</StoreID><StoreName>שלי ירושלים- אגרון</StoreName><Address>x</Address></Store>'
               '</Stores></SubChain></SubChains></Chain>')
        self.assertEqual(shufersal.parse_stores(xml)[0]["id"], 2)


if __name__ == "__main__":
    unittest.main()
