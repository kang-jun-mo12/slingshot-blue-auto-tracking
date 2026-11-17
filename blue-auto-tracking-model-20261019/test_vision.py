import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from server import ArduinoConnection
from vision import OneShotVision, decode_output, prepare_image


class DetectionTests(unittest.TestCase):
    def test_camera_letterbox_and_rgb_order(self):
        tensor, scale, left, top = prepare_image(Image.new("RGB", (640, 480), (255, 0, 0)))
        self.assertEqual((scale, left, top), (1, 0, 80))
        np.testing.assert_allclose(tensor[0, :, 80, 0], [1, 0, 0])
        np.testing.assert_allclose(tensor[0, :, 0, 0], [114 / 255] * 3)

    def test_boxes_restore_camera_coordinates_and_suppress_overlap(self):
        predictions = np.array([
            [320, 320, 100, 100, 0.9],
            [322, 322, 100, 100, 0.8],
            [100, 150, 20, 20, 0.7],
            [400, 300, 50, 50, 0.1],
        ], dtype=np.float32).T[None]
        result = decode_output(predictions, (640, 480), 1, 0, 80)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["center"], [320, 240])
        self.assertEqual(result[0]["box"], [270, 190, 370, 290])

    def test_invalid_and_padding_boxes_do_not_become_targets(self):
        predictions = np.array([
            [320, 30, 10, 10, 0.9],
            [320, 320, -10, 10, 0.9],
            [float("nan"), 320, 10, 10, 0.9],
        ], dtype=np.float32).T[None]
        self.assertEqual(decode_output(predictions, (640, 480), 1, 0, 80), [])

    def test_capture_runs_once_and_saves_matching_image(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "test.onnx"
            model.touch()
            service = OneShotVision(model, Path(directory) / "captures")
            service.detector = Mock()
            service.detector.run.return_value = []
            with patch("vision.capture_photo", return_value=Image.new("RGB", (640, 480))) as capture:
                result = service.capture_and_detect()
            capture.assert_called_once()
            service.detector.run.assert_called_once()
            self.assertFalse(result["servo_motion"])
            self.assertFalse(service.status()["busy"])
            self.assertTrue((service.capture_dir / f"{result['id']}.png").is_file())
            self.assertEqual(service.status()["result"]["id"], result["id"])

    def test_busy_and_failed_capture_are_recoverable(self):
        service = OneShotVision("/missing-test-model.onnx")
        with service.operation_lock:
            with self.assertRaises(BlockingIOError):
                service.capture_and_detect()
        with self.assertRaises(FileNotFoundError):
            service.capture_and_detect()
        self.assertFalse(service.status()["busy"])
        self.assertTrue(service.operation_lock.acquire(blocking=False))
        service.operation_lock.release()


class SerialBehaviorTests(unittest.TestCase):
    def test_automatic_ready_initializes_and_checks_ack(self):
        connection = ArduinoConnection()
        connection.connection = Mock(is_open=True)
        connection.connection.readline.return_value = b"OK 90,90,90\n"
        self.assertEqual(connection.ensure_ready(), [90, 90, 90])
        connection.connection.write.assert_called_once_with(b"90,90,90\n")

    def test_automatic_ready_preserves_existing_release_and_angles(self):
        connection = ArduinoConnection()
        connection.connection = Mock(is_open=True)
        connection.last_angles = [73, 37, 108]
        self.assertEqual(connection.ensure_ready(), [73, 37, 108])
        connection.connection.write.assert_not_called()

    def test_automatic_ready_rejects_unacknowledged_initialization(self):
        connection = ArduinoConnection()
        connection.connection = Mock(is_open=True)
        serial_connection = connection.connection
        connection.connection.readline.return_value = b""
        with self.assertRaises(RuntimeError):
            connection.ensure_ready()
        self.assertIsNone(connection.last_angles)
        serial_connection.close.assert_called_once()
        self.assertIsNone(connection.connection)

    def test_usb_open_is_not_ready_without_an_acknowledged_position(self):
        connection = ArduinoConnection()
        connection.connection = Mock(is_open=True)
        with patch.object(connection, "candidate_ports", return_value=[]):
            self.assertFalse(connection.status()["ready"])
            connection.last_angles = [90, 90, 90]
            self.assertTrue(connection.status()["ready"])

    def test_keyboard_motion_preserves_acknowledged_release_angle(self):
        connection = ArduinoConnection()
        connection.connection = Mock(is_open=True)
        connection.last_angles = [90, 37, 90]
        connection.connection.readline.return_value = b"OK 91,37,89\n"
        self.assertEqual(connection.jog(1, -1), [91, 37, 89])
        connection.connection.write.assert_called_once_with(b"91,37,89\n")

    def test_keyboard_never_opens_uninitialized_board(self):
        connection = ArduinoConnection()
        with patch("server.serial.Serial") as open_port:
            with self.assertRaises(RuntimeError):
                connection.jog(1, 0)
        open_port.assert_not_called()

    def test_jog_limits_and_invalid_deltas(self):
        connection = ArduinoConnection()
        connection.connection = Mock(is_open=True)
        connection.last_angles = [0, 37, 180]
        self.assertEqual(connection.jog(-5, 5), [0, 37, 180])
        for yaw, pitch in [(6, 0), (0, -6), (True, 0), (0, 1.55)]:
            with self.assertRaises(ValueError):
                connection.jog(yaw, pitch)
        connection.connection.write.assert_not_called()

    def test_page_status_never_opens_or_resets_arduino(self):
        connection = ArduinoConnection("/dev/ttyUSB0")
        with patch("server.Path.exists", return_value=True), patch("server.serial.Serial") as open_port:
            status = connection.status()
        self.assertTrue(status["available"])
        self.assertFalse(status["connected"])
        open_port.assert_not_called()

    def test_missing_or_wrong_ack_is_not_success(self):
        for reply in (b"", b"OK 0,0,0\n"):
            connection = ArduinoConnection()
            connection.connection = Mock(is_open=True)
            connection.connection.readline.return_value = reply
            with self.assertRaises(RuntimeError):
                connection.send_angles([90, 90, 90])


if __name__ == "__main__":
    unittest.main()
