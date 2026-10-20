import json
import unittest

from tests.fakes import (FakeFisco, fisco_cfg, free_port, make_runner, receipt_json, resolve_now, wait_status)


class FiscoDriverTest(unittest.TestCase):
    def setUp(self):
        self.fisco = FakeFisco().start()
        self.addCleanup(self.fisco.stop)
        self.runner = make_runner(fisco_cfg(self.fisco.url))

    def submit(self, text):
        row, created = self.runner.submit_receipt(text)
        return row, created

    def test_happy_path_commands_and_result(self):
        self.submit(receipt_json(card=40))
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(self.fisco.print_count, 1)
        body = self.fisco.posts[0]
        self.assertEqual(body["syntax"], "fprint")
        self.assertEqual(body["external_id"], "r1")
        self.assertEqual(body["commands"], [
            "S,1,______,_,__;Rochie roșie M;100.00;1.000;1;1;21%;0;0;buc",
            "T,1,______,_,__;0;60.00;;;;",
            "T,1,______,_,__;1;40.00;;;;",
        ])
        self.assertEqual(row["fiscal_job_id"], "http_0001")
        self.assertEqual(row["result"]["slip_number"], "6001")
        self.assertEqual(row["result"]["z_report_number"], "430")
        self.assertEqual(row["result"]["receipt_in_z"], "1")
        self.assertEqual(row["result"]["device_serial"], "DB41")

    def test_same_external_id_is_idempotent(self):
        self.submit(receipt_json())
        wait_status(self.runner, "r1", ("printed",))
        row, created = self.submit(receipt_json())
        self.assertFalse(created)
        self.assertEqual(self.fisco.print_count, 1)

    def test_vat_override_from_settings(self):
        runner = make_runner(fisco_cfg(self.fisco.url, vat_map={"21": "5"}))
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("printed",))
        self.assertIn(";1;1;5;0;0;buc", self.fisco.posts[0]["commands"][0])

    def test_buyer_cui_goes_first_with_K_command(self):
        data = json.loads(receipt_json())
        data["buyer"] = {"cui": "RO22352741"}
        self.runner.submit_receipt(json.dumps(data))
        wait_status(self.runner, "r1", ("printed",))
        commands = self.fisco.posts[0]["commands"]
        self.assertEqual(commands[0], "K,1,______,_,__;RO22352741;")
        self.assertTrue(commands[1].startswith("S,1,"))

    def test_printer_not_ready_sends_nothing(self):
        self.fisco.ready = False
        self.submit(receipt_json())
        row = wait_status(self.runner, "r1", ("failed", "uncertain", "printed"))
        self.assertEqual(row["error"]["code"], "NOT_CONNECTED")
        self.assertIn("24h", row["error"]["message"])
        self.assertEqual(self.fisco.posts, [])

    def test_fisco_down(self):
        runner = make_runner(fisco_cfg(f"http://127.0.0.1:{free_port()}"))
        runner.submit_receipt(receipt_json())
        row = wait_status(runner, "r1", ("failed", "uncertain", "printed"))
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error"]["code"], "NOT_CONNECTED")
        self.assertTrue(row["error"]["retry_safe"])

    def test_rejected_is_safe_to_retry(self):
        self.fisco.mode = "reject"
        self.submit(receipt_json())
        row = wait_status(self.runner, "r1", ("failed", "uncertain", "printed"))
        self.assertEqual(row["error"]["code"], "REJECTED")
        self.assertTrue(row["error"]["retry_safe"])
        self.assertEqual(self.fisco.print_count, 0)

    def test_confirmed_print_failure(self):
        self.fisco.mode = "fail"
        self.submit(receipt_json())
        row = wait_status(self.runner, "r1", ("failed", "uncertain", "printed"))
        self.assertEqual(row["error"]["code"], "PRINT_FAILED")
        self.assertTrue(row["error"]["retry_safe"])
        self.assertEqual(row["fiscal_job_id"], "http_0001")

    def test_lost_response_after_processing_adopts_job_without_second_print(self):
        self.fisco.mode = "drop_after"
        self.submit(receipt_json())
        row = wait_status(self.runner, "r1", ("failed", "uncertain", "printed"))
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["error"]["code"], "LOST_RESPONSE")
        self.assertIsNone(row["fiscal_job_id"])
        self.fisco.mode = "normal"
        row = resolve_now(self.runner, "r1")
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(row["fiscal_job_id"], "http_0001")
        self.assertEqual(self.fisco.print_count, 1)   # niciun bon dublu

    def test_lost_response_before_processing_then_retry_with_new_key(self):
        self.fisco.mode = "drop_before"
        self.submit(receipt_json())
        wait_status(self.runner, "r1", ("uncertain",))
        self.fisco.mode = "normal"
        row = resolve_now(self.runner, "r1")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error"]["code"], "NOT_SENT")
        self.assertTrue(row["error"]["retry_safe"])
        self.assertEqual(self.fisco.print_count, 0)
        row, problem = self.runner.retry("r1", False)
        self.assertIsNone(problem)
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(row["attempt"], 2)
        self.assertEqual(self.fisco.posts[-1]["external_id"], "r1-2")
        self.assertEqual(self.fisco.print_count, 1)

    def test_lost_response_ignores_jobs_owned_by_other_receipts(self):
        self.submit(receipt_json("r1"))
        wait_status(self.runner, "r1", ("printed",))
        self.fisco.mode = "drop_before"
        self.submit(receipt_json("r2"))
        wait_status(self.runner, "r2", ("uncertain",))
        self.fisco.mode = "normal"
        row = resolve_now(self.runner, "r2")
        self.assertEqual(row["error"]["code"], "NOT_SENT")   # jobul lui r1 nu e atribuit greșit lui r2

    def test_timeout_keeps_job_pending_then_resolves(self):
        runner = make_runner(fisco_cfg(self.fisco.url, poll_timeout_s=0.1))
        self.fisco.polls_until_printed = 10 ** 6
        runner.submit_receipt(receipt_json())
        row = wait_status(runner, "r1", ("uncertain", "printed", "failed"))
        self.assertEqual(row["error"]["code"], "TIMEOUT")
        self.assertEqual(row["fiscal_job_id"], "http_0001")
        row = resolve_now(runner, "r1")
        self.assertEqual(row["error"]["code"], "STILL_PROCESSING")
        self.fisco.polls_until_printed = 1
        row = resolve_now(runner, "r1")
        self.assertEqual(row["status"], "printed")
        self.assertEqual(self.fisco.print_count, 1)

    def test_forgotten_job_is_confirmed_failure(self):
        runner = make_runner(fisco_cfg(self.fisco.url, poll_timeout_s=0.1))
        self.fisco.polls_until_printed = 10 ** 6
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("uncertain",))
        self.fisco.forgotten.add("http_0001")
        row = resolve_now(runner, "r1")
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["error"]["retry_safe"])

    def test_report_z_uses_simple_syntax(self):
        self.runner.submit_report("z-1", "Z")
        row = wait_status(self.runner, "z-1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(self.fisco.posts[0]["syntax"], "simplu")
        self.assertEqual(self.fisco.posts[0]["commands"], ["Z^"])
        self.assertEqual(row["result"]["report_number"], "431")

    def test_agent_restart_marks_running_jobs_uncertain(self):
        row, _ = self.runner.store.create("r9", "receipt", receipt_json("r9"))
        self.runner.store.update("r9", status="running")
        self.runner.recover()
        self.assertEqual(self.runner.store.get("r9")["status"], "uncertain")
        row = resolve_now(self.runner, "r9")
        self.assertEqual(row["error"]["code"], "NOT_SENT")   # nu apucase să trimită nimic
        self.assertEqual(self.fisco.print_count, 0)

    def test_name_sanitized_and_truncated(self):
        runner = make_runner(fisco_cfg(self.fisco.url, max_name_length=10))
        data = json.loads(receipt_json())
        data["lines"][0]["name"] = "Rochie; cu^separatori lungi"
        runner.submit_receipt(json.dumps(data))
        wait_status(runner, "r1", ("printed",))
        self.assertIn(";Rochie cu;", self.fisco.posts[0]["commands"][0])

    def test_retry_requires_failed_status(self):
        self.submit(receipt_json())
        wait_status(self.runner, "r1", ("printed",))
        _, problem = self.runner.retry("r1", False)
        self.assertEqual(problem, "NOT_FAILED")

    def test_retry_of_uncertain_job_always_needs_confirmation(self):
        self.fisco.mode = "drop_before"
        self.submit(receipt_json())
        wait_status(self.runner, "r1", ("uncertain",))
        _, problem = self.runner.retry("r1", False)
        self.assertEqual(problem, "CONFIRMATION_REQUIRED")
        self.fisco.mode = "normal"
        _, problem = self.runner.retry("r1", True)
        self.assertIsNone(problem)
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)

    def test_report_not_blocked_by_not_ready_health(self):
        self.fisco.ready = False   # ex. depășire 24h: tocmai raportul Z deblochează aparatul
        self.runner.submit_report("z-1", "Z")
        row = wait_status(self.runner, "z-1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)


if __name__ == "__main__":
    unittest.main()
