import unittest

from tests.fakes import FakeFiscool, fiscool_cfg, make_runner, receipt_json, resolve_now, wait_status


class FiscoolDriverTest(unittest.TestCase):
    def setUp(self):
        self.fiscool = FakeFiscool().start()
        self.addCleanup(self.fiscool.stop)
        self.runner = make_runner(fiscool_cfg(self.fiscool.url))

    def run_receipt(self, external_id="r1", **kw):
        self.runner.submit_receipt(receipt_json(external_id, **kw))
        return wait_status(self.runner, external_id, ("printed", "failed", "uncertain"))

    def test_happy_path_request_and_result(self):
        row = self.run_receipt(card=40)
        self.assertEqual(row["status"], "printed", row)
        key, body = self.fiscool.requests[0]
        self.assertEqual(key, "r1")
        self.assertEqual(body["device_id"], FakeFiscool.DEVICE)
        op = body["operation"]
        self.assertEqual(op["type"], "print_receipt")
        self.assertEqual(op["close_action"], "close")
        self.assertEqual(op["lines"], [{
            "type": "item", "description": "Rochie roșie M", "quantity_thousandths": 1000,
            "unit_price_cents": 10000, "vat": "21%", "um": "buc"}])
        self.assertEqual(op["payments"], [{"method": "cash", "amount_cents": 6000},
                                          {"method": "card", "amount_cents": 4000}])
        result = row["result"]
        self.assertEqual(result["slip_number"], "1234")
        self.assertEqual(result["z_report_number"], "45")
        self.assertIsNone(result["receipt_in_z"])               # fiscal_receipt_count = contor dispozitiv, nu nFNum
        self.assertEqual(result["raw"]["fiscal_receipt_count"], 1050)
        self.assertEqual(result["device_serial"], FakeFiscool.SERIAL)
        self.assertIsNone(row["fiscal_job_id"])                 # FisCool nu are job_id

    def test_buyer_cui_sent_as_vat_number(self):
        import json
        data = json.loads(receipt_json())
        data["buyer"] = {"cui": "RO123456"}
        self.runner.submit_receipt(json.dumps(data))
        wait_status(self.runner, "r1", ("printed",))
        self.assertEqual(self.fiscool.requests[0][1]["operation"]["buyer"], {"vat_number": "RO123456"})

    def test_vat_override(self):
        runner = make_runner(fiscool_cfg(self.fiscool.url, vat_map={"21": "A"}))
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("printed",))
        self.assertEqual(self.fiscool.requests[0][1]["operation"]["lines"][0]["vat"], "A")

    def test_lost_response_resends_same_key_and_prints_once(self):
        self.fiscool.mode = "drop_after"
        row = self.run_receipt()
        self.assertEqual(row["status"], "uncertain")
        row = resolve_now(self.runner, "r1")
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(self.fiscool.executed, 1)
        self.assertEqual({k for k, _ in self.fiscool.requests}, {"r1"})   # aceeași cheie

    def test_transient_error_is_uncertain_and_resolves_on_same_key(self):
        self.fiscool.error = (408, 408, "Dispozitivul nu a răspuns la timp")
        row = self.run_receipt()
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["error"]["code"], "FISCOOL_408")
        self.fiscool.error = None
        row = resolve_now(self.runner, "r1")
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual({k for k, _ in self.fiscool.requests}, {"r1"})

    def test_documented_safe_errors(self):
        for http, code in ((400, 400), (400, 505), (400, 530), (400, 544), (400, 540), (404, 404)):
            with self.subTest(code=code):
                fiscool = FakeFiscool().start()
                self.addCleanup(fiscool.stop)
                runner = make_runner(fiscool_cfg(fiscool.url))
                fiscool.error = (http, code, "mesaj")
                runner.submit_receipt(receipt_json())
                row = wait_status(runner, "r1", ("printed", "failed", "uncertain"))
                self.assertEqual(row["status"], "failed")
                self.assertTrue(row["error"]["retry_safe"])

    def test_pos_declined_codes_are_safe(self):
        self.fiscool.error = (400, 601, "Fonduri insuficiente.")
        row = self.run_receipt(card=100)
        self.assertEqual(row["error"]["code"], "FISCOOL_601")
        self.assertTrue(row["error"]["retry_safe"])

    def test_undocumented_error_needs_operator_confirmation_and_new_key(self):
        self.fiscool.error = (503, 500, "Lipsă hârtie în imprimantă.")
        row = self.run_receipt()
        self.assertEqual(row["status"], "failed")
        self.assertFalse(row["error"]["retry_safe"])
        self.assertIn("Lipsă hârtie", row["error"]["message"])
        _, problem = self.runner.retry("r1", False)
        self.assertEqual(problem, "CONFIRMATION_REQUIRED")
        self.fiscool.error = None
        _, problem = self.runner.retry("r1", True)
        self.assertIsNone(problem)
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(self.fiscool.requests[-1][0], "r1-2")

    def test_connection_error_1004_is_not_connected(self):
        self.fiscool.error = (503, 1004, "Dispozitiv offline")
        row = self.run_receipt()
        self.assertEqual(row["error"]["code"], "FISCOOL_1004")
        self.assertTrue(row["error"]["retry_safe"])

    def test_device_not_connected_is_not_ready_and_sends_nothing(self):
        self.fiscool.device_status = "Disconnected"
        row = self.run_receipt()
        self.assertEqual(row["error"]["code"], "NOT_CONNECTED")
        self.assertEqual(self.fiscool.requests, [])

    def test_device_missing_or_not_set(self):
        self.fiscool.device_listed = False
        row = self.run_receipt()
        self.assertEqual(row["error"]["code"], "NOT_CONNECTED")
        runner = make_runner(fiscool_cfg(self.fiscool.url, device_id=""))
        runner.submit_receipt(receipt_json("r2"))
        row = wait_status(runner, "r2", ("printed", "failed", "uncertain"))
        self.assertEqual(row["error"]["code"], "NOT_CONNECTED")
        self.assertEqual(self.fiscool.requests, [])

    def test_report_z(self):
        self.runner.submit_report("z-1", "Z")
        row = wait_status(self.runner, "z-1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(self.fiscool.requests[0][1]["operation"], {"type": "print_z"})
        self.assertEqual(row["result"]["report_number"], "46")
        self.assertEqual(row["result"]["raw"]["grand_total_sell"], 12345)

    def test_report_not_blocked_by_health(self):
        self.fiscool.device_status = "Disconnected"
        self.runner.submit_report("z-1", "Z")
        wait_status(self.runner, "z-1", ("printed", "failed", "uncertain"))
        self.assertEqual(len(self.fiscool.requests), 1)   # driverul decide (1004 la aparat deconectat), nu agentul

    def test_discover_lists_devices(self):
        text = self.runner.fiscal.discover()
        self.assertIn(FakeFiscool.DEVICE, text)
        self.assertIn(FakeFiscool.SERIAL, text)


if __name__ == "__main__":
    unittest.main()
