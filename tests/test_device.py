import unittest
from unittest.mock import patch

from csi_capture.core.device import (
    DeviceAccessError,
    list_serial_candidates,
    resolve_serial_device,
    validate_serial_device_access,
)


class DeviceResolutionTests(unittest.TestCase):
    def test_resolve_cli_device_has_priority(self):
        resolved = resolve_serial_device(cli_device="/dev/test_serial", env={})
        self.assertEqual(resolved.path, "/dev/test_serial")
        self.assertEqual(resolved.source, "cli")

    @patch("csi_capture.core.device.list_serial_candidates", return_value=["COM4"])
    @patch("csi_capture.core.device.os.path.exists", return_value=False)
    def test_resolve_auto_keyword_uses_detected_candidate(self, _exists_mock, _list_mock):
        resolved = resolve_serial_device(cli_device="auto", env={}, default="/dev/esp32_csi")
        self.assertEqual(resolved.path, "COM4")
        self.assertEqual(resolved.source, "auto")

    @patch("csi_capture.core.device.list_serial_candidates", return_value=["/dev/cu.usbmodem1101"])
    @patch("csi_capture.core.device.os.path.exists", return_value=False)
    def test_resolve_auto_device_when_default_missing(self, _exists_mock, _list_mock):
        resolved = resolve_serial_device(cli_device=None, env={}, default="/dev/esp32_csi")
        self.assertEqual(resolved.path, "/dev/cu.usbmodem1101")
        self.assertEqual(resolved.source, "auto")

    @patch("csi_capture.core.device.os.access", return_value=False)
    @patch("csi_capture.core.device.os.path.exists", return_value=True)
    @patch("csi_capture.core.device.platform.system", return_value="Darwin")
    def test_validate_serial_device_access_has_macos_hint(self, _platform_mock, _exists_mock, _access_mock):
        with self.assertRaises(DeviceAccessError) as exc:
            validate_serial_device_access("/dev/cu.usbmodem1101")
        self.assertIn("macOS fix", str(exc.exception))

    @patch("csi_capture.core.device.os.path.exists", side_effect=lambda path: path == "/dev/esp32_csi")
    @patch("csi_capture.core.device.glob.glob")
    @patch("csi_capture.core.device._list_pyserial_candidates", return_value=[])
    def test_list_serial_candidates_includes_linux_and_macos_patterns(
        self, _serial_mock, glob_mock, _exists_mock
    ):
        def fake_glob(pattern):
            mapping = {
                "/dev/ttyACM*": ["/dev/ttyACM0"],
                "/dev/ttyUSB*": ["/dev/ttyUSB0"],
                "/dev/tty.usbmodem*": ["/dev/tty.usbmodem1101"],
                "/dev/cu.usbmodem*": ["/dev/cu.usbmodem1101"],
                "/dev/tty.usbserial*": ["/dev/tty.usbserial-01"],
                "/dev/cu.usbserial*": ["/dev/cu.usbserial-01"],
            }
            return mapping.get(pattern, [])

        glob_mock.side_effect = fake_glob
        candidates = list_serial_candidates()
        self.assertIn("/dev/esp32_csi", candidates)
        self.assertIn("/dev/ttyACM0", candidates)
        self.assertIn("/dev/ttyUSB0", candidates)
        self.assertIn("/dev/cu.usbmodem1101", candidates)
        self.assertIn("/dev/tty.usbmodem1101", candidates)

    @patch("csi_capture.core.device.os.path.exists", return_value=False)
    @patch("csi_capture.core.device.glob.glob", return_value=[])
    @patch("csi_capture.core.device._list_pyserial_candidates", return_value=["COM4", "COM7"])
    def test_list_serial_candidates_includes_windows_com_ports(
        self, _serial_mock, _glob_mock, _exists_mock
    ):
        candidates = list_serial_candidates()
        self.assertIn("COM4", candidates)
        self.assertIn("COM7", candidates)

    @patch("csi_capture.core.device._list_pyserial_candidates", return_value=["COM4"])
    @patch("csi_capture.core.device.platform.system", return_value="Windows")
    def test_validate_serial_device_access_accepts_windows_com_port(self, _platform_mock, _list_mock):
        validate_serial_device_access("COM4")

    @patch("csi_capture.core.device._list_pyserial_candidates", return_value=["COM4"])
    @patch("csi_capture.core.device.platform.system", return_value="Windows")
    def test_validate_serial_device_access_has_windows_hint(self, _platform_mock, _list_mock):
        with self.assertRaises(DeviceAccessError) as exc:
            validate_serial_device_access("COM7")
        self.assertIn("Windows fix", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
