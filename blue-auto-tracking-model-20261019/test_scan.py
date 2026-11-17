import io
import struct
import subprocess
import tempfile
import threading
import unittest
import zipfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from scan import (PhotoScanner, PITCH_VALUES, TOTAL, YAW_VALUES, capture_png,
                  grid_positions, yaw_values_between)


class ScanTests(unittest.TestCase):
    def test_grid_is_snake_with_bounded_steps_and_includes_endpoints(self):
        positions = list(grid_positions())
        self.assertEqual(len(positions), 328)
        self.assertEqual(len(set(positions)), TOTAL)
        self.assertEqual(positions[0], (30, 90))
        self.assertEqual(positions[len(YAW_VALUES) - 1], (150, 90))
        self.assertEqual(positions[len(YAW_VALUES)], (150, 93))
        self.assertEqual(positions[-1], (30, 110))
        self.assertEqual(PITCH_VALUES[-2:], (108, 110))
        for (yaw1, pitch1), (yaw2, pitch2) in zip(positions, positions[1:]):
            self.assertLessEqual(abs(yaw2 - yaw1), 3)
            self.assertLessEqual(abs(pitch2 - pitch1), 3)
            self.assertTrue((yaw1 == yaw2) != (pitch1 == pitch2))

    def test_custom_yaw_range_uses_three_degree_steps_and_includes_end(self):
        yaws = yaw_values_between(31, 39)
        self.assertEqual(yaws, (31, 34, 37, 39))
        positions = list(grid_positions(yaws))
        self.assertEqual(positions[:4], [(31, 90), (34, 90), (37, 90), (39, 90)])
        self.assertEqual(positions[4:8], [(39, 93), (37, 93), (34, 93), (31, 93)])
        self.assertEqual(len(positions), len(yaws) * len(PITCH_VALUES))

    @patch("scan.shutil.which", return_value="/usr/bin/rpicam-still")
    @patch("scan.subprocess.run")
    def test_capture_requests_1080p_png(self, run, _which):
        header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 1920, 1080)
        run.return_value = subprocess.CompletedProcess([], 0, header, b"")
        self.assertEqual(capture_png(), header)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--width") + 1], "1920")
        self.assertEqual(command[command.index("--height") + 1], "1080")
        self.assertEqual(command[command.index("--encoding") + 1], "png")

    @patch("scan.SETTLE_SECONDS", 0)
    @patch("scan.exclusive_camera", return_value=nullcontext())
    def test_full_scan_preserves_d9_and_packages_photos(self, _camera_lock):
        vision = Mock()
        vision.operation_lock = threading.Lock()
        arduino = Mock()
        arduino.status.return_value = {"angles": [90, 74, 90]}
        arduino.jog.side_effect = lambda yaw, pitch, expected_angles: [
            expected_angles[0] + yaw, expected_angles[1], expected_angles[2] + pitch]
        with tempfile.TemporaryDirectory() as directory:
            scanner = PhotoScanner(vision, arduino, output_dir=Path(directory),
                                   capture=lambda: b"fake png for a mock camera")
            initial = scanner.start()
            scanner.thread.join(timeout=10)
            self.assertFalse(scanner.thread.is_alive())
            result = scanner.status()
            self.assertEqual(result["phase"], "done")
            self.assertEqual(result["captured"], TOTAL)
            self.assertEqual(result["angles"], [30, 74, 110])
            self.assertEqual(arduino.send_angles.call_count, 0)
            self.assertFalse(vision.operation_lock.locked())
            for call in arduino.jog.call_args_list:
                yaw, pitch = call.args
                self.assertLessEqual(abs(yaw), 3)
                self.assertLessEqual(abs(pitch), 3)
                self.assertEqual(call.kwargs["expected_angles"][1], 74)
            archive_path = Path(directory) / f'{initial["session_id"]}.zip'
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
                self.assertEqual(len(names), TOTAL + 1)
                self.assertIn(f'{initial["session_id"]}/metadata.csv', names)
                self.assertIn(f'{initial["session_id"]}/0001_yaw030_pitch090.png', names)

    @patch("scan.SETTLE_SECONDS", 0)
    @patch("scan.exclusive_camera", return_value=nullcontext())
    def test_stop_keeps_partial_photos_downloadable(self, _camera_lock):
        vision = Mock()
        vision.operation_lock = threading.Lock()
        arduino = Mock()
        arduino.status.return_value = {"angles": [30, 80, 90]}
        arduino.jog.side_effect = lambda yaw, pitch, expected_angles: [
            expected_angles[0] + yaw, expected_angles[1], expected_angles[2] + pitch]
        shots = 0

        def capture():
            nonlocal shots
            shots += 1
            if shots == 3:
                scanner.stop()
            return b"mock photo"

        with tempfile.TemporaryDirectory() as directory:
            scanner = PhotoScanner(vision, arduino, output_dir=Path(directory), capture=capture)
            scanner.start()
            scanner.thread.join(timeout=5)
            state = scanner.status()
            self.assertFalse(state["active"])
            self.assertEqual(state["phase"], "stopped")
            self.assertEqual(state["captured"], 3)
            self.assertTrue((Path(directory) / f'{state["session_id"]}.zip').is_file())
            self.assertFalse(vision.operation_lock.locked())

    def test_uninitialized_board_never_moves_or_captures(self):
        vision = Mock()
        vision.operation_lock = threading.Lock()
        arduino = Mock()
        arduino.status.return_value = {"angles": None}
        capture = Mock()
        scanner = PhotoScanner(vision, arduino, capture=capture)
        with self.assertRaises(RuntimeError):
            scanner.start()
        arduino.jog.assert_not_called()
        capture.assert_not_called()
        self.assertFalse(vision.operation_lock.locked())

    def test_invalid_custom_yaw_range_never_moves_or_captures(self):
        vision = Mock()
        vision.operation_lock = threading.Lock()
        arduino = Mock()
        capture = Mock()
        scanner = PhotoScanner(vision, arduino, capture=capture)
        for yaw_min, yaw_max in ((-1, 30), (50, 50), (100, 99), (30, 181), (30.0, 90)):
            with self.subTest(yaw_min=yaw_min, yaw_max=yaw_max):
                with self.assertRaises(ValueError):
                    scanner.start(yaw_min, yaw_max)
        arduino.status.assert_not_called()
        arduino.jog.assert_not_called()
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
