import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from live_tracker import LiveTracker, iter_jpegs, select_and_plan


class ChunkedStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def read(self, _size):
        return next(self.chunks, b"")


class LiveTrackerTests(unittest.TestCase):
    def test_mjpeg_parser_handles_split_markers_and_extra_bytes(self):
        stream = ChunkedStream([
            b"noise\xff", b"\xd8first\xff\xd9junk\xff\xd8sec",
            b"ond\xff", b"\xd9",
        ])
        self.assertEqual(list(iter_jpegs(stream)),
                         [b"\xff\xd8first\xff\xd9", b"\xff\xd8second\xff\xd9"])

    def test_only_highest_confidence_target_moves_camera(self):
        detections = [
            {"class_id": 1, "class_name": "ally", "confidence": 0.5, "center": [320, 240], "box": [300, 220, 340, 260]},
            {"class_id": 1, "class_name": "ally", "confidence": 0.93, "center": [100, 100], "box": [80, 80, 120, 120]},
        ]
        target, delta, error = select_and_plan(detections)
        self.assertIs(target, detections[1])
        self.assertEqual(delta, (2, 2))
        self.assertEqual(error, [-220, -140])
        _, reverse_delta, _ = select_and_plan(detections, True, True)
        self.assertEqual(reverse_delta, (-2, -2))

    def test_centered_and_missing_target_never_move(self):
        self.assertEqual(select_and_plan([]), (None, (0, 0), None))
        target = {"class_id": 1, "class_name": "ally", "confidence": 0.9, "center": [328, 234], "box": [0, 0, 20, 20]}
        self.assertEqual(select_and_plan([target])[1], (0, 0))

    def test_start_initializes_without_prior_apply(self):
        vision = Mock()
        vision.operation_lock = threading.Lock()
        arduino = Mock()
        tracker = LiveTracker(vision, arduino, model_path=Path(__file__))
        tracker.detector = Mock()
        def initialize():
            tracker.stop_event.set()
            return [90, 90, 90]
        arduino.ensure_ready.side_effect = initialize
        with patch("live_tracker.subprocess.Popen") as camera:
            tracker.start(False, False)
            tracker.thread.join(timeout=2)
        arduino.ensure_ready.assert_called_once()
        arduino.require_angles.assert_not_called()
        camera.assert_not_called()
        self.assertEqual(tracker.status()["angles"], [90, 90, 90])
        self.assertFalse(vision.operation_lock.locked())

    def test_busy_camera_does_not_initialize_servos(self):
        vision = Mock()
        vision.operation_lock = threading.Lock()
        arduino = Mock()
        tracker = LiveTracker(vision, arduino, model_path=Path(__file__))
        with vision.operation_lock:
            with self.assertRaises(BlockingIOError):
                tracker.start(False, False)
        arduino.ensure_ready.assert_not_called()

    def test_initialization_failure_releases_camera_and_stops(self):
        vision = Mock()
        vision.operation_lock = threading.Lock()
        arduino = Mock()
        arduino.ensure_ready.side_effect = ConnectionError("no Arduino")
        tracker = LiveTracker(vision, arduino, model_path=Path(__file__))
        tracker.detector = Mock()
        with patch("live_tracker.subprocess.Popen") as camera:
            tracker.start(False, False)
            tracker.thread.join(timeout=2)
        camera.assert_not_called()
        self.assertEqual(tracker.status()["phase"], "error")
        self.assertFalse(tracker.status()["active"])
        self.assertFalse(vision.operation_lock.locked())

    def test_live_model_is_independent_of_one_shot_model(self):
        vision = Mock()
        tracker = LiveTracker(vision, Mock())
        self.assertEqual(tracker.model_path.name, "best.onnx")
        self.assertIsNone(tracker.detector)
        self.assertIsNot(tracker.detector, vision.detector)


if __name__ == "__main__":
    unittest.main()
