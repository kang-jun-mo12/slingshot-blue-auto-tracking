import io
import json
import struct
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from desktop_inference import make_server
from live_tracker import LiveTracker, select_and_plan
from png_camera import iter_pngs
from remote_inference import RemoteDetector, RetryableInferenceError, validate_detections


def png(size=(1920, 1080), fmt="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", size, (255, 0, 0)).save(buffer, format=fmt)
    return buffer.getvalue()


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = Mock(device="cpu")
        cls.server = make_server(("127.0.0.1", 0), cls.detector)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def setUp(self):
        self.detector.reset_mock()
        self.detector.run.side_effect = None
        self.detector.run.return_value = []

    def test_png_round_trip_retains_rgb_and_full_resolution(self):
        self.detector.run.return_value = [{"box": [900, 500, 1020, 580],
                                           "confidence": 0.9, "class_name": "ally"}]
        detections = RemoteDetector(self.url).run_png(png())
        self.assertEqual(detections[0]["center"], [960, 540])
        image = self.detector.run.call_args.args[0]
        self.assertEqual(image.size, (1920, 1080))
        self.assertEqual(image.getpixel((100, 100)), (255, 0, 0))

    def test_wrong_resolution_and_jpeg_rejected_before_inference(self):
        for body in [png((640, 480)), png(fmt="JPEG"), b"not an image"]:
            request = Request(self.url + "/infer", data=body,
                              headers={"Content-Type": "image/png", "X-Frame-Id": "test"})
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 400)
        self.detector.run.assert_not_called()

    def test_busy_server_does_not_queue_frame(self):
        with self.server.inference_lock:
            with self.assertRaises(RuntimeError):
                RemoteDetector(self.url).run_png(png())
        self.detector.run.assert_not_called()

    def test_model_failure_is_recoverable(self):
        self.detector.run.side_effect = RuntimeError("model failed")
        with self.assertRaises(RuntimeError):
            RemoteDetector(self.url).run_png(png())
        self.detector.run.side_effect = None
        self.assertEqual(RemoteDetector(self.url).run_png(png()), [])

    def test_health(self):
        with urlopen(self.url + "/health") as response:
            self.assertEqual(json.load(response)["backend"], "pytorch")


class ValidationTests(unittest.TestCase):
    def test_slow_inference_does_not_count_as_camera_silence(self):
        vision = SimpleNamespace(operation_lock=threading.Lock())
        arduino = Mock()
        tracker = LiveTracker(vision, arduino, inference_url="http://localhost:8090")
        tracker.detector = Mock()
        tracker.frame = png()
        tracker.frame_seq = 1
        tracker.frame_time = 100
        tracker.lease_deadline = 200
        clock = SimpleNamespace(now=100)
        def fresh_frame(**kwargs):
            tracker.frame_seq += 1
            tracker.frame_time = clock.now
        def infer(frame):
            clock.now += 6
            fresh_frame()
            return [{"class_id": 1, "class_name": "ally", "confidence": 0.9, "center": [1600, 900]}]
        def jog(yaw, pitch, expected_angles=None):
            if arduino.jog.call_count == 2:
                tracker.stop_event.set()
            return [expected_angles[0] + yaw, 37, expected_angles[2] + pitch]
        tracker.detector.run_png.side_effect = infer
        arduino.jog.side_effect = jog
        process = Mock()
        process.poll.return_value = None
        vision.operation_lock.acquire()
        with patch("live_tracker.subprocess.Popen", return_value=process), \
                patch.object(tracker, "_read_frames"), \
                patch.object(tracker.condition, "wait", side_effect=fresh_frame), \
                patch("live_tracker.time.monotonic", side_effect=lambda: clock.now):
            tracker._run(tracker.stop_event, [90, 37, 90], False, False)
        self.assertEqual(arduino.jog.call_count, 2)
        self.assertIsNone(tracker.status()["error"])

    def test_six_second_transfer_succeeds_but_over_budget_result_is_rejected(self):
        for elapsed in (6, 16):
            detector = RemoteDetector("http://localhost:8090")
            body = json.dumps({"frame_id": "test", "width": 1920, "height": 1080,
                               "detections": []}).encode()
            with patch.object(detector.opener, "open", return_value=io.BytesIO(body)), \
                    patch("remote_inference.uuid.uuid4", return_value=SimpleNamespace(hex="test")), \
                    patch("remote_inference.time.monotonic", side_effect=[0, elapsed]):
                if elapsed == 6:
                    self.assertEqual(detector.run_png(b"png"), [])
                else:
                    with self.assertRaises(RuntimeError):
                        detector.run_png(b"png")

    def test_browser_preview_does_not_replace_full_resolution_inference_png(self):
        tracker = LiveTracker(Mock(), Mock(), inference_url="http://localhost:8090")
        tracker.frame = png()
        original = tracker.frame
        tracker.frame_seq = 3
        sequence, preview = tracker.frame_after(0)
        self.assertEqual(sequence, 3)
        with Image.open(io.BytesIO(preview)) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (960, 540)))
        self.assertIs(tracker.frame, original)
        self.assertIs(tracker.frame_after(0)[1], preview)
        self.assertIsNone(tracker.frame_after(3))

    def test_retry_uses_new_frame_and_does_not_move_until_success(self):
        for recover in (True, False):
            vision = SimpleNamespace(operation_lock=threading.Lock())
            arduino = Mock()
            tracker = LiveTracker(vision, arduino, inference_url="http://localhost:8090")
            tracker.detector = Mock()
            tracker.frame = png()
            tracker.frame_seq = 1
            tracker.frame_time = time.monotonic()
            tracker.lease_deadline = time.monotonic() + 100
            frames = []
            def infer(frame):
                frames.append(frame)
                if recover and len(frames) == 2:
                    return [{"class_id": 1, "class_name": "ally", "confidence": 0.9, "center": [1600, 900]}]
                arduino.jog.assert_not_called()
                tracker.frame = png() + b"\0" * len(frames)
                tracker.frame_seq += 1
                tracker.frame_time = time.monotonic()
                raise RetryableInferenceError("temporary timeout")
            def jog(*args, **kwargs):
                tracker.stop_event.set()
                return [88, 37, 88]
            tracker.detector.run_png.side_effect = infer
            arduino.jog.side_effect = jog
            process = Mock()
            process.poll.return_value = None
            vision.operation_lock.acquire()
            with patch("live_tracker.subprocess.Popen", return_value=process), \
                    patch.object(tracker, "_read_frames"), \
                    patch.object(tracker.stop_event, "wait", return_value=False):
                tracker._run(tracker.stop_event, [90, 37, 90], False, False)
            self.assertEqual(len(frames), 2 if recover else 3)
            self.assertNotEqual(frames[0], frames[1])
            self.assertEqual(arduino.jog.call_count, 1 if recover else 0)
            self.assertIsNone(tracker.status()["error"] if recover else None)
            if not recover:
                self.assertEqual(tracker.status()["phase"], "error")
            self.assertFalse(vision.operation_lock.locked())

    def test_reject_nonfinite_and_out_of_bounds_boxes(self):
        for box in [[0, 0, float("nan"), 10], [-1, 0, 10, 10], [0, 0, 1921, 10]]:
            with self.assertRaises(ValueError):
                validate_detections([{"box": box, "confidence": 0.9, "class_name": "enemy"}])

    def test_mismatched_response_is_rejected(self):
        detector = RemoteDetector("http://localhost:8090")
        detector.opener = Mock()
        detector.opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(
            json.dumps({"frame_id": "wrong", "width": 1920, "height": 1080,
                        "detections": []}).encode()))
        detector.opener.open.return_value.__exit__ = Mock(return_value=False)
        with self.assertRaises(RuntimeError):
            detector.run_png(png())

    def test_length_prefixed_frames_and_truncation(self):
        frame = png()
        self.assertEqual(list(iter_pngs(io.BytesIO(struct.pack("!I", len(frame)) + frame))), [frame])
        for body in [b"\0\0", struct.pack("!I", 100) + b"short", struct.pack("!I", 100)]:
            with self.assertRaises(EOFError):
                list(iter_pngs(io.BytesIO(body)))

    def test_full_hd_center_and_scaled_steps(self):
        center = {"class_id": 1, "class_name": "ally", "confidence": 0.9, "center": [960, 540]}
        self.assertEqual(select_and_plan([center], image_size=(1920, 1080))[1], (0, 0))
        center["center"] = [990, 550]
        self.assertEqual(select_and_plan([center], image_size=(1920, 1080))[1], (0, 0))
        center["center"] = [1600, 900]
        self.assertEqual(select_and_plan([center], image_size=(1920, 1080))[1], (-2, -2))

    def test_remote_tracker_stop_and_stale_result_do_not_move(self):
        for mode in ("stop", "stale", "network_error"):
            vision = SimpleNamespace(operation_lock=threading.Lock())
            arduino = Mock()
            tracker = LiveTracker(vision, arduino, inference_url="http://localhost:8090")
            tracker.detector = Mock()
            tracker.lease_deadline = time.monotonic() + 100
            tracker.frame = png()
            tracker.frame_seq = 1
            tracker.frame_time = time.monotonic() - (30 if mode == "stale" else 0)
            def infer(_frame):
                if mode == "stop":
                    tracker.stop_event.set()
                if mode == "network_error":
                    raise RuntimeError("connection lost")
                return [{"class_id": 1, "class_name": "ally", "confidence": 0.9, "center": [1600, 900]}]
            tracker.detector.run_png.side_effect = infer
            process = Mock()
            process.poll.return_value = None
            vision.operation_lock.acquire()
            with patch("live_tracker.subprocess.Popen", return_value=process), \
                    patch.object(tracker, "_read_frames"):
                tracker._run(tracker.stop_event, [90, 37, 90], False, False)
            arduino.jog.assert_not_called()
            self.assertFalse(vision.operation_lock.locked())
            self.assertFalse(tracker.status()["active"])


if __name__ == "__main__":
    unittest.main()
