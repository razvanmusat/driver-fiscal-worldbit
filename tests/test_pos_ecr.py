"""POS ECR Hypercom (Newland N910 Pro): codecul protocolului, driverul pe un port virtual și plata cap-coadă prin agent (Runner + FisCool fals)."""
import tempfile
import time
import unittest
from decimal import Decimal

from worldbit_fiscal import serial_io, setari
from worldbit_fiscal.drivers import DRIVERS
from worldbit_fiscal.errors import DriverError, NotConnected, PosDeclined, Uncertain
from worldbit_fiscal.pos import ecr
from worldbit_fiscal.pos.base import POS_DRIVERS, build_pos
from worldbit_fiscal.pos.hypercom_ecr import HypercomEcr
from tests.fake_pos import FakeEcrPos, approved_body, declined_body
from tests.fakes import FakeFiscool, fiscool_cfg, make_runner, receipt_json, resolve_now, wait_status

FS = chr(ecr.FS)


def pos_settings(path, **over):
    s = {"port": path, "baud": 9600, "bytesize": 8, "parity": "N", "stopbits": 1, "ack_timeout_s": 0.3, "retries": 2,
         "start_timeout_s": 1.0, "payment_timeout_s": 1.0, "confirm_delay_s": 0.02}
    s.update(over)
    return s


class FakeJob:
    """Ce vede driverul POS din job: `set_pos` și `state`."""

    def __init__(self):
        self.pos, self.state, self.saved = None, {}, []

    def set_pos(self, d):
        self.pos = d
        self.saved.append(dict(d))

    def save(self):
        pass


class CodecTest(unittest.TestCase):
    def test_frame_has_stx_etx_and_lrc_over_everything_after_stx(self):
        data = ecr.frame("MOL10.000000010000")
        self.assertEqual((data[0], data[-2]), (ecr.STX, ecr.ETX))
        x = 0
        for b in data[1:-1]:
            x ^= b
        self.assertEqual(data[-1], x)

    def test_reader_roundtrip_one_byte_at_a_time(self):
        reader, events = ecr.Reader(), []
        for b in ecr.frame("MOL11."):
            events += reader.feed(bytes([b]))
        self.assertEqual(events, [("frame", "MOL11.", True)])

    def test_reader_ack_nak_noise_and_bad_lrc(self):
        reader = ecr.Reader()
        bad = bytearray(ecr.frame("MOL12.00"))
        bad[-1] ^= 0x01
        events = reader.feed(b"\x00\x04" + bytes([ecr.ACK, ecr.NAK]) + bytes(bad) + ecr.frame("MOL13."))
        self.assertEqual(events, [("ack",), ("nak",), ("frame", "MOL12.00", False), ("frame", "MOL13.", True)])

    def test_reader_restarts_on_a_new_stx_and_ignores_the_parity_bit(self):
        reader = ecr.Reader()
        with_parity = bytes(b | 0x80 for b in ecr.frame("MOL11."))   # bitul 8 = paritate
        events = reader.feed(bytes([ecr.STX]) + b"junk" + with_parity)
        self.assertEqual(events, [("frame", "MOL11.", True)])

    def test_oversized_frame_is_dropped(self):
        reader = ecr.Reader()
        self.assertEqual(reader.feed(bytes([ecr.STX]) + b"A" * 200 + bytes([ecr.ETX, 0])), [])
        self.assertEqual(reader.feed(ecr.frame("MOL11.")), [("frame", "MOL11.", True)])

    def test_amount_has_12_digits_without_decimal_point(self):
        self.assertEqual(ecr.amount12(Decimal("100.00")), "000000010000")
        self.assertEqual(ecr.amount12(Decimal("0.01")), "000000000001")
        self.assertEqual(ecr.amount12(Decimal("10.5")), "000000001050")
        for bad in (Decimal("0"), Decimal("-1"), Decimal("1.005"), Decimal("10000000000.00")):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    ecr.amount12(bad)

    def test_parse_message(self):
        self.assertEqual(ecr.parse_message("MOL12.00xx"), ("MOL", "12", "00xx"))
        for bad in ("", "MOL1", "MOL1x.", "123 10.", "PIN01X"):
            self.assertIsNone(ecr.parse_message(bad), bad)

    def test_authorization_approved(self):
        a = ecr.parse_authorization(approved_body()[6:])
        self.assertTrue(a["approved"])
        self.assertEqual((a["invoice"], a["approval_code"], a["date"], a["time"], a["issuer"]),
                         ("000123", "A1B2C3", "261021", "103045", "VISA"))
        self.assertEqual(a["card"], "************1111")           # PAN-ul complet nu iese din parser
        self.assertNotIn("4111111111111111", str(a))

    def test_authorization_declined_with_empty_or_missing_fields(self):
        for body in (declined_body("51")[6:], declined_body("51", empty_fields=False)[6:]):
            a = ecr.parse_authorization(body)
            self.assertEqual((a["approved"], a["response_code"], a["invoice"], a["card"]), (False, "51", None, None))

    def test_authorization_with_masked_pan_keeps_it(self):
        a = ecr.parse_authorization("00" + "************1111" + FS + "****000123A1B2C3261021103045MASTERCD" + FS)
        self.assertEqual(a["card"], "************1111")

    def test_decline_messages(self):
        self.assertEqual(ecr.decline_message("51"), "Fonduri insuficiente")
        self.assertEqual(ecr.decline_message("77"), "Plata a fost respinsă (cod 77)")


class SerialTest(unittest.TestCase):
    def test_roundtrip_timeout_and_purge(self):
        pos = FakeEcrPos().start()
        self.addCleanup(pos.stop)
        with serial_io.SerialPort(pos.path, 9600, 7, "E", 1) as port:
            self.assertEqual(port.read(0.05), b"")                       # timeout: nimic venit
            import os
            os.write(pos.master, b"abc")
            time.sleep(0.05)
            self.assertEqual(port.read(0.5), b"abc")
            os.write(pos.master, b"zzz")
            time.sleep(0.05)
            port.purge()
            self.assertEqual(port.read(0.05), b"")

    def test_a_hung_up_device_raises_instead_of_spinning_until_the_timeout(self):
        import os
        pos = FakeEcrPos().start()
        port = serial_io.SerialPort(pos.path, 9600, 8, "N", 1)
        port.open()
        pos._stop = True
        os.close(pos.master)
        t = time.monotonic()
        with self.assertRaises(serial_io.SerialError):
            for _ in range(50):
                port.read(5)
        self.assertLess(time.monotonic() - t, 3)
        port.close()

    def test_missing_port_raises_serial_error(self):
        with self.assertRaises(serial_io.SerialError) as ctx:
            serial_io.SerialPort("/dev/nu-exista-pos", 9600, 7, "E", 1).open()
        self.assertIn("nu există", str(ctx.exception))

    def test_invalid_parameters(self):
        for args in ((9600, 6, "E", 1), (9600, 7, "X", 1), (9600, 7, "E", 3), (1234, 7, "E", 1)):
            with self.subTest(args):
                with self.assertRaises(ValueError):
                    serial_io.SerialPort("COM4", *args)

    def test_port_names(self):
        self.assertTrue(serial_io.valid_port_name("COM4"))
        self.assertTrue(serial_io.valid_port_name("com12"))
        self.assertTrue(serial_io.valid_port_name("/dev/ttyUSB0"))
        for bad in ("", "COM", "COM1234", "LPT1", "C:\\x", "4"):
            self.assertFalse(serial_io.valid_port_name(bad), bad)
        self.assertEqual(serial_io.device_path("com4"), "\\\\.\\COM4")


class DriverTest(unittest.TestCase):
    def setUp(self):
        self.job = FakeJob()

    def pos(self, **kw):
        fake = FakeEcrPos(**kw).start()
        self.addCleanup(fake.stop)
        return fake

    def driver(self, fake, **over):
        return HypercomEcr(pos_settings(fake.path, **over))

    def test_approved_payment_sends_the_amount_and_completes_the_conversation(self):
        fake = self.pos()
        saved_before_ack = []
        fake.on_ack12 = lambda: saved_before_ack.append(self.job.pos is not None)
        result = self.driver(fake).sale(Decimal("40.00"), self.job)
        self.assertEqual((result.status, result.transaction_id, result.auth_code, result.card_masked),
                         ("approved", "000123", "A1B2C3", "************1111"))
        self.assertIsNone(result.rrn)
        self.assertEqual(fake.received("10"), ["MOL10.000000004000"])
        time.sleep(0.2)
        self.assertEqual(fake.received("13"), ["MOL13."])
        self.assertEqual(fake.count("ack"), 2)                           # ACK la 11 și la 12
        self.assertEqual(saved_before_ack, [True])                        # aprobarea era deja salvată când POS-ul a primit ACK-ul

    def test_the_card_number_is_never_stored(self):
        fake = self.pos()
        self.driver(fake).sale(Decimal("1.00"), self.job)
        self.assertNotIn("4111111111111111", str(self.job.pos))
        self.assertNotIn("2712", str(self.job.pos))                       # nici data de expirare

    def test_declined_payment_raises_with_a_clear_message_and_still_closes_the_conversation(self):
        fake = self.pos(mode="decline", decline_code="51")
        with self.assertRaises(PosDeclined) as ctx:
            self.driver(fake).sale(Decimal("40.00"), self.job)
        self.assertEqual(str(ctx.exception), "Fonduri insuficiente")
        self.assertTrue(ctx.exception.retry_safe)
        self.assertEqual(ctx.exception.to_dict()["driver_code"], "51")
        self.assertEqual(self.job.pos["status"], "declined")
        time.sleep(0.2)
        self.assertEqual(fake.received("13"), ["MOL13."])

    def test_no_ack_to_the_start_command_means_offline_and_is_safe(self):
        fake = self.pos(mode="silent")
        t = time.monotonic()
        with self.assertRaises(NotConnected) as ctx:
            self.driver(fake).sale(Decimal("40.00"), self.job)
        self.assertEqual(ctx.exception.code, "POS_OFFLINE")
        self.assertTrue(ctx.exception.retry_safe)
        self.assertEqual(len(fake.received("10")), 3)                     # 1 + 2 retrimiteri
        self.assertLess(time.monotonic() - t, 5)
        self.assertIsNone(self.job.pos)

    def test_nak_then_ack_retransmits_and_succeeds(self):
        fake = self.pos(nak_first=2)
        result = self.driver(fake).sale(Decimal("5.00"), self.job)
        self.assertEqual(result.status, "approved")
        self.assertEqual(len(fake.received("10")), 3)

    def test_start_not_confirmed_in_time_is_offline(self):
        fake = self.pos(mode="no_start")
        with self.assertRaises(NotConnected) as ctx:
            self.driver(fake, start_timeout_s=0.5).sale(Decimal("5.00"), self.job)
        self.assertEqual(ctx.exception.code, "POS_OFFLINE")

    def test_no_result_after_start_is_a_failure_the_cashier_can_retry(self):
        fake = self.pos(mode="no_result")
        with self.assertRaises(DriverError) as ctx:
            self.driver(fake, payment_timeout_s=0.5).sale(Decimal("5.00"), self.job)
        self.assertEqual((ctx.exception.code, ctx.exception.retry_safe), ("POS_NO_RESULT", True))
        self.assertNotIsInstance(ctx.exception, Uncertain)
        self.assertIn("nu relua plata", str(ctx.exception))
        self.assertEqual(len(fake.received("10")), 1)                     # comanda de plată nu se retrimite singură
        self.assertIsNone(self.job.pos)

    def test_pin_pad_traffic_and_noise_are_ignored_and_not_acknowledged(self):
        fake = self.pos(noise=True)
        result = self.driver(fake).sale(Decimal("5.00"), self.job)
        self.assertEqual(result.status, "approved")
        self.assertEqual(fake.count("ack"), 2)
        self.assertEqual(fake.count("nak"), 0)

    def test_bytes_arriving_one_at_a_time(self):
        fake = self.pos(dribble=True)
        self.assertEqual(self.driver(fake).sale(Decimal("5.00"), self.job).status, "approved")

    def test_corrupted_result_is_nak_ed_and_the_retransmission_is_accepted(self):
        fake = self.pos(bad_lrc_once=True)
        result = self.driver(fake).sale(Decimal("5.00"), self.job)
        self.assertEqual(result.status, "approved")
        self.assertEqual(fake.count("nak"), 1)

    def test_unconfirmed_13_does_not_change_the_approved_result(self):
        fake = self.pos(mode="no_ack13")
        result = self.driver(fake).sale(Decimal("5.00"), self.job)
        self.assertEqual(result.status, "approved")
        self.assertEqual(self.job.pos["status"], "approved")

    def test_link_dropping_during_payment_is_a_failure_the_cashier_can_retry(self):
        fake = self.pos(mode="hangup_after_start")
        with self.assertRaises(DriverError) as ctx:
            self.driver(fake, payment_timeout_s=2).sale(Decimal("5.00"), self.job)
        self.assertEqual((ctx.exception.code, ctx.exception.retry_safe), ("POS_NO_RESULT", True))

    def test_missing_port_is_a_safe_failure(self):
        with self.assertRaises(NotConnected) as ctx:
            HypercomEcr(pos_settings("/dev/nu-exista-pos")).sale(Decimal("5.00"), self.job)
        self.assertEqual(ctx.exception.code, "POS_PORT")
        self.assertTrue(ctx.exception.retry_safe)

    def test_a_retransmitted_start_message_is_acknowledged_again(self):
        fake = self.pos(dup11=True)
        self.assertEqual(self.driver(fake).sale(Decimal("5.00"), self.job).status, "approved")
        self.assertEqual(fake.count("ack"), 3)                            # 11, 11 retrimis, 12

    def test_a_port_that_fails_before_the_command_left_is_a_safe_failure(self):
        class Broken:
            def __init__(self, *a): pass
            def open(self): pass
            def close(self): pass
            def purge(self): pass
            def write(self, d): raise serial_io.SerialError("Portul COM4: dispozitivul nu mai e conectat")
            def read(self, t): return b""
        driver = HypercomEcr(pos_settings("COM4"))
        driver.port_factory = Broken
        with self.assertRaises(NotConnected) as ctx:
            driver.sale(Decimal("5.00"), self.job)
        self.assertEqual((ctx.exception.code, ctx.exception.retry_safe), ("POS_PORT", True))

    def test_a_second_payment_or_a_port_check_cannot_interrupt_a_payment_in_progress(self):
        fake = self.pos(mode="no_result")
        driver = self.driver(fake, payment_timeout_s=1.0)
        import threading
        t = threading.Thread(target=lambda: self._ignore(driver))
        t.start()
        time.sleep(0.3)
        self.assertIn("în curs", driver.check_port())
        with self.assertRaises(Exception) as ctx:
            driver.sale(Decimal("5.00"), FakeJob())
        self.assertEqual(ctx.exception.code, "POS_BUSY")
        t.join()
        self.assertEqual(len(fake.received("10")), 1)

    def _ignore(self, driver):
        try:
            driver.sale(Decimal("5.00"), FakeJob())
        except DriverError:
            pass

    # ------------------------------------------------------------------ anularea (VOI)
    def approved_job(self, fake):
        job = FakeJob()
        self.driver(fake).sale(Decimal("40.00"), job)
        return job

    def test_void_sends_the_receipt_number_and_marks_the_payment_voided_before_the_ack(self):
        fake = self.pos()
        job = self.approved_job(fake)
        statuses = []
        fake.on_ack12 = lambda: statuses.append(job.pos["status"])       # la ACK-ul rezultatului anulării, starea trebuie să fie deja „voided"
        self.assertTrue(self.driver(fake).void("000123", job))
        self.assertEqual(fake.received("10"), ["MOL10.000000004000", "VOI10.000000000123"])    # numărul chitanței, pe 12 cifre, în câmpul sumei
        self.assertEqual(statuses, ["voided"])
        self.assertEqual((job.pos["status"], job.pos["transaction_id"]), ("voided", "000123"))
        self.assertEqual(job.pos["raw"]["void"]["response_code"], "00")
        time.sleep(0.2)
        self.assertEqual(fake.received("13"), ["MOL13.", "VOI13."])

    def test_void_declined_by_the_terminal_keeps_the_payment_approved(self):
        fake = self.pos()
        job = self.approved_job(fake)
        fake.void_mode, fake.decline_code = "decline", "05"
        with self.assertRaises(DriverError) as ctx:
            self.driver(fake).void("000123", job)
        self.assertEqual(ctx.exception.code, "POS_VOID_DECLINED")
        self.assertEqual(job.pos["status"], "approved")

    def test_void_without_confirmation_keeps_the_payment_approved(self):
        fake = self.pos()
        job = self.approved_job(fake)
        fake.void_mode = "no_result"
        with self.assertRaises(DriverError) as ctx:
            self.driver(fake, payment_timeout_s=0.5).void("000123", job)
        self.assertEqual((ctx.exception.code, ctx.exception.retry_safe), ("POS_NO_RESULT", False))
        self.assertEqual(job.pos["status"], "approved")

    def test_void_with_the_terminal_off_is_a_failure(self):
        fake = self.pos()
        job = self.approved_job(fake)
        fake.void_mode = "silent"
        with self.assertRaises(NotConnected):
            self.driver(fake).void("000123", job)
        self.assertEqual(job.pos["status"], "approved")

    def test_void_refuses_a_missing_or_invalid_receipt_number_without_sending_anything(self):
        fake = self.pos()
        for bad in (None, "", "abc", "1234567890123"):
            with self.subTest(bad):
                with self.assertRaises(DriverError) as ctx:
                    self.driver(fake).void(bad, FakeJob())
                self.assertEqual(ctx.exception.code, "POS_VOID_INVOICE")
        self.assertEqual(fake.received(), [])

    def test_settlement_sends_stl_and_acknowledges_the_start(self):
        fake = self.pos()
        self.assertTrue(self.driver(fake).settlement())
        time.sleep(0.2)
        self.assertEqual(fake.received("10"), ["STL10.000000000000"])
        self.assertEqual(fake.settled, 1)

    def test_settlement_never_raises_when_it_cannot_be_done(self):
        self.assertFalse(self.driver(self.pos(mode="silent")).settlement())
        self.assertFalse(self.driver(self.pos(mode="no_start"), start_timeout_s=0.4).settlement())
        self.assertFalse(HypercomEcr(pos_settings("/dev/nu-exista-pos")).settlement())

    def test_resolve_never_resends_the_payment(self):
        fake = self.pos()
        with self.assertRaises(Uncertain):
            self.driver(fake).resolve(self.job)
        self.assertEqual(fake.received(), [])

    def test_check_port_sends_nothing(self):
        fake = self.pos()
        self.assertIn("s-a deschis", self.driver(fake).check_port())
        self.assertIn("nu există", HypercomEcr(pos_settings("/dev/nu-exista-pos")).check_port())
        self.assertEqual(fake.received(), [])

    def test_settings_validation(self):
        ok = pos_settings("COM4")
        HypercomEcr.validate_settings(ok)
        for change in ({"port": ""}, {"port": "USB"}, {"baud": 1}, {"bytesize": 6}, {"parity": "X"}, {"stopbits": 3},
                       {"payment_timeout_s": 0}, {"payment_timeout_s": 9999}, {"retries": 9}, {"confirm_delay_s": -1}):
            with self.subTest(change):
                with self.assertRaises(ValueError):
                    HypercomEcr.validate_settings({**ok, **change})


class RunnerWithPosTest(unittest.TestCase):
    def setUp(self):
        self.fiscool = FakeFiscool().start()
        self.addCleanup(self.fiscool.stop)

    def runner(self, fake, **over):
        cfg = fiscool_cfg(self.fiscool.url)
        cfg["pos"] = {"model": "hypercom_ecr", "settings": pos_settings(fake.path, **over)}
        return make_runner(cfg)

    def pos(self, **kw):
        fake = FakeEcrPos(**kw).start()
        self.addCleanup(fake.stop)
        return fake

    def test_card_payment_goes_to_the_pos_first_then_to_the_fiscal_printer(self):
        fake = self.pos()
        runner = self.runner(fake)
        runner.submit_receipt(receipt_json(card=40))
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"), timeout=10)
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(fake.received("10"), ["MOL10.000000004000"])    # doar partea de card
        self.assertEqual(self.fiscool.executed, 1)
        self.assertEqual((row["pos"]["status"], row["pos"]["auth_code"], row["pos"]["transaction_id"]), ("approved", "A1B2C3", "000123"))

    def test_declined_card_prints_nothing_and_is_safe_to_retry(self):
        fake = self.pos(mode="decline", decline_code="55")
        runner = self.runner(fake)
        runner.submit_receipt(receipt_json(card=100))
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"), timeout=10)
        self.assertEqual((row["status"], row["error"]["code"], row["error"]["retry_safe"]), ("failed", "POS_DECLINED", True))
        self.assertEqual(row["error"]["message"], "PIN incorect")
        self.assertEqual(self.fiscool.executed, 0)
        self.assertEqual(row["pos"]["status"], "declined")

    def test_no_pos_result_fails_without_printing_and_a_retry_asks_the_pos_again(self):
        fake = self.pos(mode="no_result")
        runner = self.runner(fake, payment_timeout_s=0.5)
        runner.submit_receipt(receipt_json(card=100))
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"), timeout=10)
        self.assertEqual((row["status"], row["error"]["code"], row["error"]["retry_safe"]), ("failed", "POS_NO_RESULT", True))
        self.assertEqual(self.fiscool.executed, 0)
        self.assertEqual(len(fake.received("10")), 1)                    # singur nu retrimite plata
        _, problem = runner.retry("r1", True)                            # „Reîncearcă plata" a casierului
        self.assertIsNone(problem)
        wait_status(runner, "r1", ("failed",), timeout=10)
        self.assertEqual(len(fake.received("10")), 2)
        self.assertEqual(self.fiscool.executed, 0)

    def test_pos_offline_fails_safely_without_printing(self):
        fake = self.pos(mode="silent")
        runner = self.runner(fake)
        runner.submit_receipt(receipt_json(card=100))
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"), timeout=10)
        self.assertEqual((row["status"], row["error"]["code"], row["error"]["retry_safe"]), ("failed", "POS_OFFLINE", True))
        self.assertEqual(self.fiscool.executed, 0)

    def test_approved_card_is_not_charged_again_when_the_fiscal_print_is_retried(self):
        fake = self.pos()
        runner = self.runner(fake)
        self.fiscool.error = (503, 505, "Casa ocupată")
        self.fiscool.error_times = 99
        runner.submit_receipt(receipt_json(card=100))
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"), timeout=10)
        self.assertEqual(row["status"], "failed", row)
        self.assertEqual(row["pos"]["status"], "approved")
        self.fiscool.error = None
        _, problem = runner.retry("r1", True)
        self.assertIsNone(problem)
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"), timeout=10)
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(len(fake.received("10")), 1)                    # POS-ul a fost cerut o singură dată

    def test_approval_saved_before_a_crash_is_recovered_without_asking_the_pos_again(self):
        fake = self.pos()
        runner = self.runner(fake)
        runner.submit_receipt(receipt_json(card=100))
        wait_status(runner, "r1", ("printed",), timeout=10)
        # cădere simulată: aprobarea e în baza agentului, dar jobul a rămas în pasul POS, incert
        runner.store.update("r1", status="uncertain", result=None, state={"stage": "pos"},
                            error={"code": "RESTARTED", "message": "x", "retry_safe": False})
        row = resolve_now(runner, "r1")
        self.assertEqual((row["status"], row["error"]["code"], row["error"]["retry_safe"]), ("failed", "POS_CONFIRMED", True))
        self.assertNotIn("stage", row["state"])
        _, problem = runner.retry("r1", False)
        self.assertIsNone(problem)
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"), timeout=10)
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(len(fake.received("10")), 1)

    def z_report(self, runner, external_id="z1"):
        runner.submit_report(external_id, "Z")
        return wait_status(runner, external_id, ("printed", "failed", "uncertain"), timeout=10)

    def test_settlement_is_sent_after_the_z_report_printed(self):
        fake = self.pos()
        runner = self.runner(fake)
        self.assertEqual(self.z_report(runner)["status"], "printed")
        time.sleep(0.5)
        self.assertEqual(fake.received("10"), ["STL10.000000000000"])
        self.assertEqual(self.fiscool.executed, 1)

    def test_no_settlement_when_the_z_report_fails(self):
        fake = self.pos()
        runner = self.runner(fake)
        self.fiscool.error = (503, 500, "Eroare casă")
        self.assertEqual(self.z_report(runner)["status"], "failed")
        time.sleep(0.3)
        self.assertEqual(fake.received(), [])

    def test_z_report_is_printed_even_if_the_pos_is_off(self):
        fake = self.pos(mode="silent")
        runner = self.runner(fake)
        self.assertEqual(self.z_report(runner)["status"], "printed")

    def test_x_report_and_receipts_never_trigger_a_settlement(self):
        fake = self.pos()
        runner = self.runner(fake)
        runner.submit_report("x1", "X")
        wait_status(runner, "x1", ("printed",), timeout=10)
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("printed",), timeout=10)
        time.sleep(0.3)
        self.assertEqual(fake.received(), [])

    def test_settlement_is_sent_only_once_per_z_job(self):
        fake = self.pos()
        runner = self.runner(fake)
        job = self.z_report(runner)
        time.sleep(0.5)
        from worldbit_fiscal.runner import JobHandle
        runner._printed(JobHandle(runner.store, runner.store.get("z1")), type("R", (), {"to_dict": lambda self: {}})())
        time.sleep(0.3)
        self.assertEqual(len(fake.received("10")), 1)

    def test_z_report_without_a_pos_configured_just_prints(self):
        runner = make_runner(fiscool_cfg(self.fiscool.url))
        self.assertEqual(self.z_report(runner)["status"], "printed")

    def test_the_fiscal_print_window_starts_after_the_card_is_approved(self):
        fake = self.pos(delay_result=1.2)                # clientul stă 1,2 s la PIN, mai mult decât termenul bonului (1 s)
        cfg = fiscool_cfg(self.fiscool.url)
        cfg["pos"] = {"model": "hypercom_ecr", "settings": pos_settings(fake.path, payment_timeout_s=5)}
        cfg["max_wait_s"] = 1
        runner = make_runner(cfg)
        self.fiscool.error = (503, 505, "Casa ocupată")   # prima încercare la casă: eroare trecătoare, se reîncearcă singură
        self.fiscool.error_times = 1
        runner.submit_receipt(receipt_json(card=100))
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"), timeout=15)
        self.assertEqual(row["status"], "printed", row)   # fără repornirea cronometrului, termenul ar fi trecut și jobul ar fi eșuat
        self.assertEqual(self.fiscool.executed, 1)

    # ------------------------------------------------------------------ anularea plății când bonul nu poate ieși
    def failed_after_approval(self, runner, external_id="r1", receipt=None):
        self.fiscool.error = (503, 505, "Casa ocupată")
        self.fiscool.error_times = 99
        runner.submit_receipt(receipt or receipt_json(external_id, card=100))
        row = wait_status(runner, external_id, ("printed", "failed", "uncertain"), timeout=10)
        self.assertEqual((row["status"], row["pos"]["status"]), ("failed", "approved"))
        return row

    def test_voiding_the_pos_payment_when_the_receipt_cannot_be_printed(self):
        fake = self.pos()
        runner = self.runner(fake)
        self.failed_after_approval(runner)
        row, problem, error = runner.void_pos("r1")
        self.assertEqual((problem, error), (None, None))
        self.assertEqual(row["pos"]["status"], "voided")
        self.assertEqual(fake.received("10"), ["MOL10.000000010000", "VOI10.000000000123"])
        self.assertEqual(self.fiscool.executed, 0)
        self.assertEqual(row["status"], "failed")

    def test_after_a_void_the_retry_asks_the_pos_again_because_the_payment_is_no_longer_approved(self):
        fake = self.pos()
        runner = self.runner(fake)
        self.failed_after_approval(runner)
        runner.void_pos("r1")
        self.fiscool.error = None
        _, problem = runner.retry("r1", True)
        self.assertIsNone(problem)
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"), timeout=10)
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(len(fake.received("10")), 3)                     # plata, anularea, plata nouă

    def test_a_payment_is_voided_only_once(self):
        fake = self.pos()
        runner = self.runner(fake)
        self.failed_after_approval(runner)
        self.assertIsNone(runner.void_pos("r1")[1])
        self.assertEqual(runner.void_pos("r1")[1], "NOT_VOIDABLE")
        self.assertEqual(len(fake.received("10")), 2)

    def test_a_printed_receipt_is_never_voided(self):
        fake = self.pos()
        runner = self.runner(fake)
        runner.submit_receipt(receipt_json(card=100))
        wait_status(runner, "r1", ("printed",), timeout=10)
        self.assertEqual(runner.void_pos("r1")[1], "NOT_VOIDABLE")
        self.assertEqual(len(fake.received("10")), 1)

    def test_a_declined_or_unpaid_job_is_not_voidable(self):
        fake = self.pos(mode="decline")
        runner = self.runner(fake)
        runner.submit_receipt(receipt_json(card=100))
        wait_status(runner, "r1", ("failed",), timeout=10)
        self.assertEqual(runner.void_pos("r1")[1], "NOT_VOIDABLE")
        self.assertEqual(runner.void_pos("nu-exista")[1], "NOT_FOUND")
        self.assertEqual(len(fake.received("10")), 1)

    def test_the_void_is_refused_while_another_operation_is_running(self):
        import threading
        fake = self.pos()
        runner = self.runner(fake)
        self.failed_after_approval(runner)
        held, release = threading.Event(), threading.Event()

        def hold():
            with runner.send_lock:
                held.set()
                release.wait(5)
        t = threading.Thread(target=hold)
        t.start()
        held.wait(2)
        self.assertEqual(runner.void_pos("r1")[1], "BUSY")
        release.set()
        t.join()
        self.assertIsNone(runner.void_pos("r1")[1])

    def test_a_void_refused_by_the_terminal_is_reported_and_the_payment_stays_approved(self):
        fake = self.pos()
        fake.void_mode, fake.decline_code = "decline", "05"
        runner = self.runner(fake)
        self.failed_after_approval(runner)
        row, problem, error = runner.void_pos("r1")
        self.assertEqual((problem, error.code), ("VOID_FAILED", "POS_VOID_DECLINED"))
        self.assertEqual(row["pos"]["status"], "approved")

    def test_a_mirror_job_with_the_same_payment_key_voids_the_payment_of_the_primary_job(self):
        import json
        fake = self.pos()
        runner = self.runner(fake)
        primary = json.loads(receipt_json("r1", card=100))
        primary["payment_key"] = "pk-1"
        self.failed_after_approval(runner, "r1", json.dumps(primary))
        mirror = {**primary, "external_id": "r2"}
        runner.submit_receipt(json.dumps(mirror))
        self.assertEqual(runner.store.get("r2")["status"], "alias")
        row, problem, _ = runner.void_pos("r2")
        self.assertIsNone(problem)
        self.assertEqual(runner.store.get("r1")["pos"]["status"], "voided")

    def test_a_restart_during_the_card_payment_fails_with_retry_instead_of_waiting_forever(self):
        fake = self.pos()
        runner = self.runner(fake)
        runner.store.create("r1", "receipt", receipt_json("r1", card=100))
        runner.store.update("r1", status="running", state={"stage": "pos"})      # agentul a căzut cât clientul era la POS
        runner.recover()
        for _ in range(3):
            row = resolve_now(runner, "r1")
            self.assertEqual((row["status"], row["error"]["code"], row["error"]["retry_safe"]), ("failed", "POS_NO_RESULT", True))
        self.assertNotIn("stage", row["state"])
        self.assertEqual(fake.received(), [])                                   # nimic nu s-a retrimis la POS
        self.assertEqual(self.fiscool.executed, 0)

    def test_cash_only_never_touches_the_pos(self):
        fake = self.pos()
        runner = self.runner(fake)
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("printed",), timeout=10)
        self.assertEqual(fake.received(), [])


class PosVoidHttpTest(unittest.TestCase):
    """POST /jobs/<id>/pos-void, așa cum îl apelează fereastra de plată din Dressbit."""

    ORIGIN = "https://dressbit.test"

    def setUp(self):
        import threading
        from worldbit_fiscal.server import App, make_server
        self.fiscool = FakeFiscool().start()
        self.addCleanup(self.fiscool.stop)
        self.fake = FakeEcrPos().start()
        self.addCleanup(self.fake.stop)
        cfg = fiscool_cfg(self.fiscool.url)
        cfg["allowed_origins"] = [self.ORIGIN]
        cfg["pos"] = {"model": "hypercom_ecr", "settings": pos_settings(self.fake.path)}
        self.runner = make_runner(cfg)
        self.httpd = make_server(App(cfg, self.runner, tempfile.mkdtemp()), port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, path):
        import http.client
        import json
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", path, body="{}", headers={"Origin": self.ORIGIN, "Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read() or b"null")
        conn.close()
        return resp.status, data

    def failed_job(self):
        self.fiscool.error = (503, 505, "Casa ocupată")
        self.fiscool.error_times = 99
        self.runner.submit_receipt(receipt_json(card=100))
        wait_status(self.runner, "r1", ("failed",), timeout=10)

    def test_voids_the_payment_and_reports_the_new_state(self):
        self.failed_job()
        status, data = self.post("/jobs/r1/pos-void")
        self.assertEqual(status, 200, data)
        self.assertEqual((data["pos"]["status"], data["status"]), ("voided", "failed"))

    def test_refusals(self):
        self.assertEqual(self.post("/jobs/nu-exista/pos-void")[0], 404)
        self.runner.submit_receipt(receipt_json(card=100))
        wait_status(self.runner, "r1", ("printed",), timeout=10)
        status, data = self.post("/jobs/r1/pos-void")                    # bon tipărit: nu se anulează
        self.assertEqual((status, data["error"]["code"]), (409, "NOT_VOIDABLE"))

    def test_a_void_the_terminal_does_not_confirm_tells_the_cashier_the_receipt_number(self):
        self.fake.void_mode, self.fake.decline_code = "decline", "05"
        self.failed_job()
        status, data = self.post("/jobs/r1/pos-void")
        self.assertEqual((status, data["error"]["code"]), (502, "POS_VOID_DECLINED"))
        self.assertIn("Anulează chitanța nr. 000123 direct de pe POS", data["error"]["message"])


class SetariPosTest(unittest.TestCase):
    def setUp(self):
        self.cfg = fiscool_cfg("http://127.0.0.1:1")
        self.driver_cls = DRIVERS["fiscool"]

    def form(self, **extra):
        return {"port": "4747", "allowed_origins": "", "d_base_url": "http://127.0.0.1:10123", "d_device_id": "x", "d_vat_map": "",
                "d_max_name_length": "36", "d_request_timeout_s": "25", **extra}

    def test_model_is_in_the_list_with_its_fields(self):
        page = setari.settings_page(self.cfg, self.driver_cls, {f.key: f.default for f in self.driver_cls.fields})
        self.assertIn("<optgroup label='Raiffeisen'><option value='hypercom_ecr'>ECR Hypercom (Newland N910 Pro)</option></optgroup>", page)
        self.assertIn("name='p_hypercom_ecr_port'", page)
        self.assertIn("name='p_hypercom_ecr_baud' value='9600'", page)
        self.assertIn("Verifică portul POS", page)
        self.assertIn("Parametrii de mai jos sunt cei din documentația protocolului", page)
        self.assertIn("Documentul: 30 de secunde", page)                  # fiecare câmp are o explicație, nu doar o valoare

    def test_port_typed_by_hand_is_saved_with_the_defaults_of_the_document(self):
        new = setari.parse_settings_form(self.form(pos_model="hypercom_ecr", p_hypercom_ecr_port="com4"), self.cfg, self.driver_cls)
        self.assertEqual(new["pos"]["model"], "hypercom_ecr")
        self.assertEqual(new["pos"]["settings"]["port"], "com4")
        self.assertEqual(build_pos(new).settings["parity"], "E")          # implicit din document
        self.assertEqual(build_pos(new).settings["baud"], 9600)

    def test_invalid_pos_settings_are_refused(self):
        for extra in ({"p_hypercom_ecr_port": ""}, {"p_hypercom_ecr_port": "USB1"}, {"p_hypercom_ecr_port": "COM4", "p_hypercom_ecr_baud": "12"},
                      {"p_hypercom_ecr_port": "COM4", "p_hypercom_ecr_baud": "abc"}):
            with self.subTest(extra):
                with self.assertRaises(ValueError):
                    setari.parse_settings_form(self.form(pos_model="hypercom_ecr", **extra), self.cfg, self.driver_cls)

    def test_switching_to_none_and_back_does_not_keep_the_old_settings(self):
        new = setari.parse_settings_form(self.form(pos_model="hypercom_ecr", p_hypercom_ecr_port="COM4", p_hypercom_ecr_baud="19200"),
                                         self.cfg, self.driver_cls)
        none = setari.parse_settings_form(self.form(pos_model="none"), new, self.driver_cls)
        self.assertEqual(none["pos"]["model"], "none")
        self.assertIsNone(build_pos(none))
        again = setari.parse_settings_form(self.form(pos_model="hypercom_ecr", p_hypercom_ecr_port="COM5"), none, self.driver_cls)
        self.assertEqual(again["pos"]["settings"]["baud"] if "baud" in again["pos"]["settings"] else 9600, 9600)

    def test_unsent_fields_keep_their_current_value(self):
        new = setari.parse_settings_form(self.form(pos_model="hypercom_ecr", p_hypercom_ecr_port="COM4", p_hypercom_ecr_baud="19200"),
                                         self.cfg, self.driver_cls)
        again = setari.parse_settings_form(self.form(pos_model="hypercom_ecr", p_hypercom_ecr_port="COM6"), new, self.driver_cls)
        self.assertEqual((again["pos"]["settings"]["port"], again["pos"]["settings"]["baud"]), ("COM6", 19200))


if __name__ == "__main__":
    unittest.main()
