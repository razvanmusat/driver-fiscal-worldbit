import unittest
from unittest import mock

from worldbit_fiscal.errors import PosDeclined, Uncertain
from worldbit_fiscal.pos import base as pos_base
from tests.fakes import FakeFisco, fisco_cfg, make_runner, receipt_json, resolve_now, wait_status


class FakePos(pos_base.PosDriver):
    name = "fake"
    behaviour = "approve"
    calls = []
    fisco = None
    fiscal_prints_at_call = []

    def sale(self, amount, job):
        FakePos.calls.append(str(amount))
        FakePos.fiscal_prints_at_call.append(FakePos.fisco.print_count)
        if FakePos.behaviour == "decline":
            raise PosDeclined("Card respins")
        if FakePos.behaviour == "uncertain":
            raise Uncertain("Fără răspuns de la POS", code="POS_UNCERTAIN")
        return pos_base.PosResult("approved", transaction_id="T1", auth_code="A1", rrn="R1", card_masked="****1234")


class PosFlowTest(unittest.TestCase):
    def setUp(self):
        self.fisco = FakeFisco().start()
        self.addCleanup(self.fisco.stop)
        FakePos.behaviour, FakePos.calls, FakePos.fiscal_prints_at_call, FakePos.fisco = "approve", [], [], self.fisco
        patcher = mock.patch.dict(pos_base.POS_DRIVERS, {"fake": FakePos})
        patcher.start()
        self.addCleanup(patcher.stop)
        cfg = fisco_cfg(self.fisco.url)
        cfg["pos"] = {"model": "fake", "settings": {}}
        self.runner = make_runner(cfg)

    def test_pos_is_called_before_fiscal_print_with_card_amount(self):
        self.runner.submit_receipt(receipt_json(card=40))
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(FakePos.calls, ["40.00"])
        self.assertEqual(FakePos.fiscal_prints_at_call, [0])      # POS a fost ÎNAINTE de bon
        self.assertEqual(row["pos"]["auth_code"], "A1")
        self.assertEqual(row["pos"]["rrn"], "R1")

    def test_cash_only_does_not_call_pos(self):
        self.runner.submit_receipt(receipt_json())
        wait_status(self.runner, "r1", ("printed",))
        self.assertEqual(FakePos.calls, [])

    def test_declined_card_prints_nothing(self):
        FakePos.behaviour = "decline"
        self.runner.submit_receipt(receipt_json(card=100))
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["error"]["code"], "POS_DECLINED")
        self.assertTrue(row["error"]["retry_safe"])
        self.assertEqual(self.fisco.posts, [])

    def test_approved_payment_is_not_charged_again_on_retry(self):
        self.fisco.mode = "reject"
        self.runner.submit_receipt(receipt_json(card=100))
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["error"]["code"], "REJECTED")
        self.assertEqual(row["pos"]["status"], "approved")
        self.fisco.mode = "normal"
        _, problem = self.runner.retry("r1", False)
        self.assertIsNone(problem)
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(FakePos.calls, ["100.00"])                # POS apelat o singură dată

    def test_a_payment_without_a_result_is_never_re_requested_and_becomes_a_failure_the_cashier_can_retry(self):
        FakePos.behaviour = "uncertain"
        self.runner.submit_receipt(receipt_json(card=100))
        wait_status(self.runner, "r1", ("uncertain",))
        for _ in range(3):
            row = resolve_now(self.runner, "r1")
            self.assertEqual((row["status"], row["error"]["code"], row["error"]["retry_safe"]), ("failed", "POS_NO_RESULT", True))
        self.assertEqual(FakePos.calls, ["100.00"])                # POS-ul nu e cerut a doua oară singur
        self.assertEqual(self.fisco.posts, [])                     # și nu se tipărește nimic

    def test_no_pos_configured_marks_card_as_skipped(self):
        runner = make_runner(fisco_cfg(self.fisco.url))
        runner.submit_receipt(receipt_json("r2", card=100))
        row = wait_status(runner, "r2", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed")
        self.assertEqual(row["pos"], {"status": "skipped", "reason": "pos_not_configured"})


if __name__ == "__main__":
    unittest.main()
