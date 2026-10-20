import json
import unittest
from decimal import Decimal

from worldbit_fiscal.contract import InvalidPayload, parse_json, parse_receipt, percent_key
from tests.fakes import receipt_json


class ContractTest(unittest.TestCase):
    def parse(self, text):
        return parse_receipt(parse_json(text))

    def test_valid_receipt(self):
        r = self.parse(receipt_json(card=40))
        self.assertEqual(r.total, Decimal("100.00"))
        self.assertEqual(r.card_total, Decimal("40.00"))
        self.assertEqual([p.method for p in r.payments], ["cash", "card"])

    def test_float_numbers_stay_exact(self):
        data = json.loads(receipt_json())
        data["lines"][0]["unit_price"] = 100.5
        data["total"] = 100.5
        data["payments"][0]["amount"] = 100.5
        r = self.parse(json.dumps(data))
        self.assertEqual(r.total, Decimal("100.5"))

    def test_total_must_match_lines_and_payments(self):
        data = json.loads(receipt_json())
        data["total"] = "99.00"
        with self.assertRaises(InvalidPayload):
            self.parse(json.dumps(data))
        data = json.loads(receipt_json())
        data["payments"][0]["amount"] = "90.00"
        with self.assertRaises(InvalidPayload):
            self.parse(json.dumps(data))

    def test_rejects_bad_input(self):
        base = json.loads(receipt_json())
        cases = {
            "external_id": lambda d: d.update(external_id="a b"),
            "no lines": lambda d: d.update(lines=[]),
            "qty 4 decimals": lambda d: d["lines"][0].update(quantity="1.0001"),
            "qty zero": lambda d: d["lines"][0].update(quantity=0),
            "price zero": lambda d: d["lines"][0].update(unit_price="0.00"),
            "vat 101": lambda d: d["lines"][0].update(vat_percent=101),
            "method": lambda d: d["payments"][0].update(method="credit"),
            "bool": lambda d: d["lines"][0].update(quantity=True),
            "cui": lambda d: d.update(buyer={"cui": "abc"}),
        }
        for label, mutate in cases.items():
            data = json.loads(json.dumps(base))
            mutate(data)
            with self.subTest(label), self.assertRaises(InvalidPayload):
                self.parse(json.dumps(data))

    def test_duplicate_payment_method_rejected(self):
        data = json.loads(receipt_json(card=40))
        data["payments"].append({"method": "cash", "amount": "1.00"})
        with self.assertRaises(InvalidPayload):
            self.parse(json.dumps(data))

    def test_invalid_json(self):
        with self.assertRaises(InvalidPayload):
            parse_json("{nu e json")

    def test_percent_key(self):
        self.assertEqual(percent_key(Decimal("21.00")), "21")
        self.assertEqual(percent_key(Decimal("9.5")), "9.5")
        self.assertEqual(percent_key(Decimal("100")), "100")
        self.assertEqual(percent_key(Decimal("0")), "0")


if __name__ == "__main__":
    unittest.main()
