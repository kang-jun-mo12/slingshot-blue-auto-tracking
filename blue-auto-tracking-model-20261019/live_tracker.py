"""Continuous camera preview and one-target visual servoing.

The camera remains open while tracking; inference always consumes the newest
available frame. Only the yaw and pitch axes are ever passed to Arduino.jog.
"""

import io
import math
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from PIL import Image, UnidentifiedImageError

from vision import Detector, ROOT
from remote_inference import RemoteDetector, FRAME_SIZE, REMOTE_TIMEOUT_SECONDS, RetryableInferenceError
from png_camera import iter_pngs
from target_policy import centering_targets


WIDTH = 640
HEIGHT = 480
DEADBAND_X = 15
DEADBAND_Y = 12
LEASE_SECONDS = 7
LIVE_MODEL_PATH = ROOT / "models" / "best.onnx"


def iter_jpegs(stream):
    """Yield complete JPEG frames from an MJPEG byte stream."""
    buffer = bytearray()
    while chunk := stream.read(65536):
        buffer.extend(chunk)
        while True:
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                # Preserve a possible split start marker at a read boundary.
                if buffer[-1:] == b"\xff":
                    buffer[:] = b"\xff"
                else:
                    buffer.clear()
                break
            if start:
                del buffer[:start]
            end = buffer.find(b"\xff\xd9", 2)
            if end < 0:
                if len(buffer) > 2_000_000:
                    buffer.clear()
                break
            yield bytes(buffer[:end + 2])
            del buffer[:end + 2]


def select_and_plan(detections, reverse_yaw=False, reverse_pitch=False, image_size=(WIDTH, HEIGHT)):
    """Return the highest confidence blue target and bounded yaw/pitch deltas."""
    detections = centering_targets(detections)
    if not detections:
        return None, (0, 0), None
    target = max(detections, key=lambda item: item["confidence"])
    dx = target["center"][0] - image_size[0] / 2
    dy = target["center"][1] - image_size[1] / 2

    def amount(error, deadband):
        if abs(error) <= deadband:
            return 0
        return math.copysign(2 if abs(error) > 90 else 1, error)

    yaw = -int(amount(dx * WIDTH / image_size[0], DEADBAND_X)) * (-1 if reverse_yaw else 1)
    pitch = -int(amount(dy * HEIGHT / image_size[1], DEADBAND_Y)) * (-1 if reverse_pitch else 1)
    return target, (yaw, pitch), [round(dx, 1), round(dy, 1)]


class LiveTracker:
    def __init__(self, vision, arduino, model_path=LIVE_MODEL_PATH, inference_url=None):
        self.inference_url = inference_url
        self.image_size = FRAME_SIZE if inference_url is not None else (WIDTH, HEIGHT)
        self.frame_time = 0
        self.reader_error = None
        self.vision = vision
        self.arduino = arduino
        self.model_path = Path(model_path)
        self.detector = None
        self.condition = threading.Condition()
        self.thread = None
        self.stop_event = threading.Event()
        self.process = None
        self.frame = None
        self.frame_seq = 0
        self.preview_seq = 0
        self.preview = None
        self.preview_lock = threading.Lock()
        self.lease_deadline = 0
        self.state = {
            "active": False, "phase": "idle", "error": None,
            "target": None, "detections": 0, "error_px": None,
            "angles": None, "inference_ms": None, "processed_seq": 0,
            "retry_count": 0,
        }

    def status(self):
        with self.condition:
            return {**self.state, "frame_seq": self.frame_seq,
                    "width": self.image_size[0], "height": self.image_size[1]}

    def frame_after(self, after):
        with self.condition:
            if self.frame is None or self.frame_seq <= after:
                return None
            sequence, frame = self.frame_seq, self.frame
        if self.inference_url is None:
            return sequence, frame
        # Only browser preview is compressed. Inference consumes self.frame PNG.
        with self.preview_lock:
            if self.preview_seq != sequence:
                with Image.open(io.BytesIO(frame)) as source:
                    preview = source.convert("RGB")
                    preview.thumbnail((960, 540), Image.Resampling.BILINEAR)
                buffer = io.BytesIO()
                preview.save(buffer, format="JPEG", quality=75)
                self.preview_seq, self.preview = sequence, buffer.getvalue()
            return sequence, self.preview

    def start(self, reverse_yaw, reverse_pitch):
        if type(reverse_yaw) is not bool or type(reverse_pitch) is not bool:
            raise ValueError("좌우·상하 방향 설정이 필요합니다")
        if self.inference_url is not None:
            RemoteDetector(self.inference_url)  # Validate configuration before reserving the camera.
        if self.inference_url is None and not self.model_path.is_file():
            raise RuntimeError(f"실시간 추적 YOLO 모델 파일이 없습니다: {self.model_path}")
        if not self.vision.operation_lock.acquire(blocking=False):
            raise BlockingIOError("카메라가 이미 사용 중입니다")
        try:
            with self.condition:
                if self.thread and self.thread.is_alive():
                    raise BlockingIOError("추적이 이미 실행 중입니다")
                self.stop_event = threading.Event()
                self.lease_deadline = time.monotonic() + LEASE_SECONDS
                self.frame = None
                self.reader_error = None
                self.state.update(active=True, phase="starting", error=None,
                                  target=None, detections=0, error_px=None,
                                  angles=None, inference_ms=None, retry_count=0)
                self.thread = threading.Thread(
                    target=self._run,
                    args=(self.stop_event, None, reverse_yaw, reverse_pitch),
                    daemon=True,
                    name="live-target-tracker",
                )
                self.thread.start()
            return self.status()
        except Exception:
            self.vision.operation_lock.release()
            raise

    def heartbeat(self):
        with self.condition:
            if self.state["active"]:
                self.lease_deadline = time.monotonic() + LEASE_SECONDS
            return {**self.state, "frame_seq": self.frame_seq,
                    "width": self.image_size[0], "height": self.image_size[1]}

    def stop(self):
        with self.condition:
            self.stop_event.set()
            process = self.process
            if self.state["active"]:
                self.state["phase"] = "stopping"
            self.condition.notify_all()
        if process and process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        return self.status()

    def _read_frames(self, process, stop_event):
        try:
            frames = iter_pngs(process.stdout) if self.inference_url is not None else iter_jpegs(process.stdout)
            for frame in frames:
                if stop_event.is_set():
                    return
                with self.condition:
                    self.frame = frame
                    self.frame_time = time.monotonic()
                    self.frame_seq += 1
                    self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.reader_error = str(exc)
                self.condition.notify_all()

    def _run(self, stop_event, angles, reverse_yaw, reverse_pitch):
        process = None
        reader = None
        try:
            detector = self.detector
            if detector is None:
                detector = (RemoteDetector(self.inference_url) if self.inference_url is not None
                            else Detector(self.model_path))
                self.detector = detector
            if stop_event.is_set():
                return
            if angles is None:
                angles = self.arduino.ensure_ready()
                with self.condition:
                    self.state["angles"] = list(angles)
                if stop_event.is_set():
                    return
            if self.inference_url is not None:
                command = [sys.executable, "-u", str(ROOT / "png_camera.py")]
            else:
                executable = shutil.which("rpicam-vid")
                if executable is None:
                    raise RuntimeError("rpicam-vid를 찾을 수 없습니다")
                command = [executable, "--nopreview", "--timeout", "0", "--width", str(WIDTH),
                           "--height", str(HEIGHT), "--framerate", "15", "--codec", "mjpeg",
                           "--flush", "--output", "-"]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=0)
            with self.condition:
                self.process = process
                self.state["phase"] = "searching"
            reader = threading.Thread(target=self._read_frames, args=(process, stop_event),
                                      daemon=True, name="live-camera-reader")
            reader.start()

            processed_seq = 0
            camera_started = time.monotonic()
            last_valid_frame = camera_started
            previous_distance = None
            movement_enabled = True
            retry_count = 0
            while not stop_event.is_set():
                with self.condition:
                    if time.monotonic() > self.lease_deadline:
                        self.state["phase"] = "timeout"
                        stop_event.set()
                        break
                    if self.reader_error:
                        raise RuntimeError(self.reader_error)
                    if self.frame is None or self.frame_seq <= processed_seq:
                        self.condition.wait(timeout=0.25)
                        if stop_event.is_set():
                            break
                        if self.frame is not None and self.frame_seq > processed_seq:
                            continue
                        if process.poll() is not None:
                            raise RuntimeError("카메라 영상이 종료되었습니다")
                        last_camera_frame = self.frame_time if self.frame is not None else camera_started
                        if time.monotonic() - last_camera_frame > 5:
                            raise RuntimeError("카메라 영상이 도착하지 않습니다")
                        continue
                    if self.reader_error:
                        raise RuntimeError(self.reader_error)
                    frame = self.frame
                    frame_time = self.frame_time
                    processed_seq = self.frame_seq
                try:
                    with Image.open(io.BytesIO(frame)) as source:
                        image = source.convert("RGB")
                except (UnidentifiedImageError, OSError) as exc:
                    if time.monotonic() - last_valid_frame > 5:
                        raise RuntimeError("카메라 영상 디코딩이 계속 실패합니다") from exc
                    continue
                last_valid_frame = time.monotonic()
                started = time.perf_counter()
                try:
                    detections = (detector.run_png(frame) if self.inference_url is not None
                                  else detector.run(image))
                except RetryableInferenceError as exc:
                    retry_count += 1
                    with self.condition:
                        if stop_event.is_set():
                            break
                        self.state.update(phase="reconnecting", error=str(exc),
                                          target=None, detections=0, error_px=None,
                                          retry_count=retry_count)
                    if retry_count >= 3:
                        raise RuntimeError(f"데스크톱 추론 연결이 3회 연속 실패했습니다: {exc}") from exc
                    if stop_event.wait(1):
                        break
                    continue
                if self.inference_url is not None and time.monotonic() - frame_time > REMOTE_TIMEOUT_SECONDS:
                    raise RuntimeError("오래된 추론 프레임이므로 추적을 중지합니다")
                retry_count = 0
                inference_ms = round((time.perf_counter() - started) * 1000)
                target, delta, error_px = select_and_plan(
                    detections, reverse_yaw, reverse_pitch, image.size)

                with self.condition:
                    if time.monotonic() > self.lease_deadline:
                        self.state["phase"] = "timeout"
                        stop_event.set()
                    if stop_event.is_set():
                        break
                phase = "no_target" if target is None else "centered" if delta == (0, 0) else "tracking"
                if target is None:
                    previous_distance = None
                else:
                    distance = math.hypot(error_px[0] * WIDTH / image.width,
                                          error_px[1] * HEIGHT / image.height)
                    if previous_distance is not None and distance > previous_distance + 25:
                        movement_enabled = False
                        phase = "direction_error"
                    if not movement_enabled:
                        phase = "direction_error" if phase != "no_target" else phase
                    elif delta != (0, 0):
                        updated = self.arduino.jog(*delta, expected_angles=angles)
                        if updated == angles:
                            movement_enabled = False
                            phase = "limit"
                        else:
                            angles = updated
                            phase = "moving"
                            previous_distance = distance
                            with self.condition:
                                processed_seq = self.frame_seq
                    else:
                        previous_distance = None

                with self.condition:
                    self.state.update(phase=phase, target=target,
                                      error=None, retry_count=0,
                                      detections=len(detections), error_px=error_px,
                                      angles=list(angles), inference_ms=inference_ms,
                                      processed_seq=processed_seq)
        except Exception as exc:
            with self.condition:
                self.state.update(phase="error", error=str(exc))
        finally:
            stop_event.set()
            if process:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                if process.stdout:
                    process.stdout.close()
            if reader:
                reader.join(timeout=2)
            with self.condition:
                self.process = None
                self.state["active"] = False
                if self.state["phase"] not in ("error", "timeout"):
                    self.state["phase"] = "stopped"
                self.condition.notify_all()
            self.vision.operation_lock.release()
