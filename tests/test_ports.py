"""Detectarea porturilor seriale: Linux pe un arbore /dev și /sys de test, Windows pe ieșiri înregistrate."""
import os
import tempfile
import unittest
from pathlib import Path

from worldbit_fiscal import ports


def arbore(*dispozitive):
    """dispozitive: (tty, by_id sau None, {vid, pid, manufacturer, product, serial}). Întoarce (dev, sys)."""
    root = Path(tempfile.mkdtemp())
    dev, sys_ = root / "dev", root / "sys"
    (dev / "serial" / "by-id").mkdir(parents=True)
    for tty, by_id, atribute in dispozitive:
        (dev / tty).write_text("")
        usb = sys_ / "devices" / "pci0" / f"usb-{tty}"
        iface = usb / f"{tty}-if0"
        iface.mkdir(parents=True)
        for k, nume in (("vid", "idVendor"), ("pid", "idProduct"), ("manufacturer", "manufacturer"), ("product", "product"), ("serial", "serial")):
            if atribute.get(k):
                (usb / nume).write_text(atribute[k] + "\n")
        (sys_ / "class" / "tty" / tty).mkdir(parents=True)
        os.symlink(iface, sys_ / "class" / "tty" / tty / "device")
        if by_id:
            os.symlink(dev / tty, dev / "serial" / "by-id" / by_id)
    return str(dev), str(sys_)


NEWLAND = {"vid": "19d1", "pid": "0001", "manufacturer": "FuJian Newland Payment", "product": "Newland N910 Pro", "serial": "NL123456"}


class LinuxPortsTest(unittest.TestCase):
    def test_a_device_with_a_serial_number_is_offered_by_its_stable_by_id_path(self):
        dev, sys_ = arbore(("ttyACM0", "usb-Newland_N910_Pro_NL123456-if00", NEWLAND))
        [p] = ports.linux_ports(dev, sys_)
        self.assertTrue(p["path"].endswith("/serial/by-id/usb-Newland_N910_Pro_NL123456-if00"))
        self.assertTrue(p["stable"])
        self.assertEqual(p["label"], "Newland N910 Pro (FuJian Newland Payment)")
        self.assertIn("USB 19d1:0001", p["detail"])
        self.assertIn("serie NL123456", p["detail"])
        self.assertIn("acum ttyACM0", p["detail"])
        self.assertEqual(p["note"], "")

    def test_the_stable_path_stays_valid_when_the_tty_number_changes(self):
        dev, sys_ = arbore(("ttyACM0", "usb-Newland_N910_Pro_NL123456-if00", NEWLAND))
        cale = ports.linux_ports(dev, sys_)[0]["path"]
        # echipamentul e mutat în alt port: acum e ttyACM3, dar by-id arată spre el
        os.remove(cale)
        Path(dev, "ttyACM3").write_text("")
        os.symlink(Path(dev, "ttyACM3"), cale)
        self.assertEqual(os.path.realpath(cale), os.path.realpath(Path(dev, "ttyACM3")))
        self.assertTrue(os.path.exists(cale))

    def test_a_device_without_serial_number_gets_a_warning_but_keeps_the_stable_path(self):
        dev, sys_ = arbore(("ttyUSB0", "usb-Prolific_USB-Serial_Controller-if00-port0", {"vid": "067b", "pid": "2303", "manufacturer": "Prolific", "product": "USB-Serial"}))
        [p] = ports.linux_ports(dev, sys_)
        self.assertTrue(p["stable"])
        self.assertIn("fără număr de serie", p["detail"])
        self.assertIn("nu se pot deosebi", p["note"])

    def test_a_tty_without_any_stable_path_is_flagged_with_a_suggested_udev_rule(self):
        dev, sys_ = arbore(("ttyUSB1", None, {"vid": "067b", "pid": "2303", "product": "USB-Serial"}))
        [p] = ports.linux_ports(dev, sys_)
        self.assertEqual((p["path"].endswith("/ttyUSB1"), p["stable"]), (True, False))
        self.assertIn("udev", p["note"])
        self.assertIn('ATTRS{idVendor}=="067b"', p["udev_rule"])
        self.assertIn('SYMLINK+="pos"', p["udev_rule"])

    def test_the_serial_number_goes_into_the_suggested_rule_when_known(self):
        dev, sys_ = arbore(("ttyACM0", None, NEWLAND))
        [p] = ports.linux_ports(dev, sys_)
        self.assertIn('ATTRS{serial}=="NL123456"', p["udev_rule"])

    def test_no_devices_means_an_empty_list_and_a_device_is_not_listed_twice(self):
        dev, sys_ = arbore()
        self.assertEqual(ports.linux_ports(dev, sys_), [])
        dev, sys_ = arbore(("ttyACM0", "usb-A-if00", NEWLAND), ("ttyACM1", None, {"vid": "1", "pid": "2", "product": "Altceva"}))
        listate = ports.linux_ports(dev, sys_)
        self.assertEqual(len(listate), 2)
        self.assertEqual([p["stable"] for p in listate], [True, False])           # întâi cele stabile

    def test_missing_sysfs_information_does_not_break_the_listing(self):
        dev, sys_ = arbore(("ttyACM0", "usb-X-if00", {}))
        [p] = ports.linux_ports(dev, sys_)
        self.assertEqual(p["label"], "usb-X-if00")


class WindowsPortsTest(unittest.TestCase):
    CIM = ('[{"Name":"FuJian Newland Payment USB2UART (COM4)","PNPDeviceID":"USB\\\\VID_19D1&PID_0001\\\\NL123456"},'
           '{"Name":"USB Serial Port (COM7)","PNPDeviceID":"USB\\\\VID_067B&PID_2303\\\\5&2F3C5E5&0&2"},'
           '{"Name":"Communications Port (COM1)","PNPDeviceID":"ACPI\\\\PNP0501\\\\1"}]')

    def test_com_ports_with_names_sorted_by_number(self):
        listate = ports.windows_ports({"\\Device\\Serial0": "COM1", "\\Device\\USBSER000": "COM4", "\\Device\\Silabser0": "COM7"}, self.CIM)
        self.assertEqual([p["path"] for p in listate], ["COM1", "COM4", "COM7"])
        self.assertEqual(listate[1]["label"], "FuJian Newland Payment USB2UART")

    def test_a_usb_device_without_serial_number_is_flagged_because_its_com_follows_the_usb_port(self):
        listate = {p["path"]: p for p in ports.windows_ports({"a": "COM4", "b": "COM7"}, self.CIM)}
        self.assertTrue(listate["COM4"]["stable"])
        self.assertEqual(listate["COM4"]["note"], "")
        self.assertFalse(listate["COM7"]["stable"])
        self.assertIn("același port", listate["COM7"]["note"])

    def test_without_powershell_output_the_registry_ports_are_still_listed(self):
        self.assertEqual([p["path"] for p in ports.windows_ports({"a": "COM12", "b": "COM3"}, "")], ["COM3", "COM12"])
        self.assertEqual([p["path"] for p in ports.windows_ports({"a": "COM3"}, "nu e json")], ["COM3"])

    def test_a_single_device_comes_back_from_powershell_as_an_object_not_a_list(self):
        unul = '{"Name":"USB Serial Port (COM5)","PNPDeviceID":"USB\\\\VID_1&PID_2\\\\ABC"}'
        self.assertEqual([p["path"] for p in ports.windows_ports({}, unul)], ["COM5"])


if __name__ == "__main__":
    unittest.main()
