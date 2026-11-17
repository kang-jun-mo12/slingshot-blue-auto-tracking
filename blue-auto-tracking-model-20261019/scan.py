"""Separate, bounded servo-grid photo collection (no YOLO or firing)."""

import csv
import fcntl
import shutil
import struct
import subprocess
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from vision import ROOT


SCAN_DIR = ROOT / "scan_sessions"
CAMERA_LOCK_PATH = Path("/tmp/servo_camera_capture.lock")
DEFAULT_YAW_MIN = 30
DEFAULT_YAW_MAX = 150
YAW_STEP = 3
YAW_VALUES = tuple(range(DEFAULT_YAW_MIN, DEFAULT_YAW_MAX + 1, YAW_STEP))
PITCH_VALUES = tuple(range(90, 109, 3)) + (110,)
TOTAL = len(YAW_VALUES) * len(PITCH_VALUES)
LEASE_SECONDS = 10
SETTLE_SECONDS = 0.3


class ScanStopped(Exception):
    pass


def yaw_values_between(yaw_min, yaw_max):
    """Return 3-degree positions and always include the requested endpoints."""
    values = list(range(yaw_min, yaw_max + 1, YAW_STEP))
    if values[-1] != yaw_max:
        values.append(yaw_max)
    return tuple(values)


def grid_positions(yaw_values=YAW_VALUES):
    """Visit each grid position once, reversing yaw at the end of each row."""
    for row, pitch in enumerate(PITCH_VALUES):
        row_yaws = yaw_values if row % 2 == 0 else reversed(yaw_values)
        for yaw in row_yaws:
            yield yaw, pitch


def capture_png():
    executable = shutil.which("rpicam-still") or shutil.which("libcamera-still")
    if executable is None:
        raise RuntimeError("rpicam-still을 찾을 수 없습니다")
    completed = subprocess.run(
        [executable, "--nopreview", "--immediate", "--timeout", "1",
         "--width", "1920", "--height", "1080", "--encoding", "png",
         "--output", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25, check=False,
    )
    if completed.returncode:
        lines = completed.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError("카메라 촬영 실패: " + (lines[-1] if lines else "알 수 없는 오류"))
    data = completed.stdout
    if (len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n")
            or data[12:16] != b"IHDR" or struct.unpack(">II", data[16:24]) != (1920, 1080)):
        raise RuntimeError("카메라가 1920×1080 PNG를 반환하지 않았습니다")
    return data


@contextmanager
def exclusive_camera(stop_event):
    """Keep the independent 8081 preview from taking the camera mid-scan."""
    with CAMERA_LOCK_PATH.open("a+b") as lock_file:
        deadline = time.monotonic() + 5
        while True:
            if stop_event.is_set():
                raise ScanStopped("stopped")
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BlockingIOError("다른 화면에서 카메라를 사용 중입니다")
                stop_event.wait(0.1)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


class PhotoScanner:
    def __init__(self, vision, arduino, output_dir=SCAN_DIR, capture=capture_png):
        self.vision = vision
        self.arduino = arduino
        self.output_dir = Path(output_dir)
        self.capture = capture
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.lease_deadline = 0
        self.state = {
            "active": False, "phase": "idle", "error": None,
            "session_id": None, "captured": 0, "total": TOTAL,
            "angles": None, "last_photo": None, "download_url": None,
        }

    def status(self):
        with self.lock:
            return dict(self.state)

    def start(self, yaw_min=DEFAULT_YAW_MIN, yaw_max=DEFAULT_YAW_MAX):
        if (type(yaw_min) is not int or type(yaw_max) is not int
                or not 0 <= yaw_min <= 180 or not 0 <= yaw_max <= 180):
            raise ValueError("좌우 시작·끝 각도는 0°부터 180°까지의 정수여야 합니다")
        if yaw_min >= yaw_max:
            raise ValueError("좌우 끝 각도는 시작 각도보다 커야 합니다")
        yaw_values = yaw_values_between(yaw_min, yaw_max)
        total = len(yaw_values) * len(PITCH_VALUES)
        with self.lock:
            if self.state["active"]:
                raise BlockingIOError("자동 촬영이 이미 진행 중입니다")
            angles = self.arduino.status()["angles"]
            if angles is None:
                raise RuntimeError("먼저 서보 조종 화면에서 각도 적용을 눌러 주세요")
            if not 90 <= angles[2] <= 110:
                raise ValueError("현재 상하 각도는 90°~110°여야 합니다")
            self.arduino.require_angles(angles)
            if not self.vision.operation_lock.acquire(blocking=False):
                raise BlockingIOError("카메라가 이미 사용 중입니다")
            try:
                session_id = uuid.uuid4().hex
                self.stop_event = threading.Event()
                self.lease_deadline = time.monotonic() + LEASE_SECONDS
                self.state.update(active=True, phase="starting", error=None,
                                  session_id=session_id, captured=0,
                                  total=total, yaw_min=yaw_min, yaw_max=yaw_max,
                                  angles=list(angles), last_photo=None, download_url=None)
                self.thread = threading.Thread(
                    target=self._run,
                    args=(session_id, list(angles), yaw_values, self.stop_event),
                    daemon=True, name="servo-photo-scanner")
                self.thread.start()
            except Exception:
                self.vision.operation_lock.release()
                raise
            return dict(self.state)

    def heartbeat(self):
        with self.lock:
            if self.state["active"]:
                self.lease_deadline = time.monotonic() + LEASE_SECONDS
            return dict(self.state)

    def stop(self):
        with self.lock:
            self.stop_event.set()
            if self.state["active"]:
                self.state["phase"] = "stopping"
            return dict(self.state)

    def _check_running(self, stop_event):
        if stop_event.is_set():
            raise ScanStopped("stopped")
        with self.lock:
            if time.monotonic() > self.lease_deadline:
                stop_event.set()
                raise ScanStopped("timeout")

    def _move_to(self, angles, yaw, pitch, stop_event):
        while angles[0] != yaw or angles[2] != pitch:
            self._check_running(stop_event)
            dyaw = max(-3, min(3, yaw - angles[0]))
            dpitch = max(-3, min(3, pitch - angles[2]))
            updated = self.arduino.jog(dyaw, dpitch, expected_angles=angles)
            if updated == angles or updated[1] != angles[1]:
                raise RuntimeError("서보 이동을 확인하지 못했습니다")
            angles = list(updated)
            with self.lock:
                self.state["angles"] = list(angles)
            if stop_event.wait(SETTLE_SECONDS):
                raise ScanStopped("stopped")
        return angles

    def _run(self, session_id, angles, yaw_values, stop_event):
        session_dir = self.output_dir / session_id
        phase = "done"
        error = None
        try:
            with exclusive_camera(stop_event):
                session_dir.mkdir(parents=True, exist_ok=False)
                with (session_dir / "metadata.csv").open("w", newline="", encoding="utf-8") as manifest:
                    writer = csv.writer(manifest)
                    writer.writerow(("filename", "yaw_deg", "pitch_deg", "captured_at"))
                    for number, (yaw, pitch) in enumerate(grid_positions(yaw_values), 1):
                        self._check_running(stop_event)
                        with self.lock:
                            self.state["phase"] = "moving"
                        angles = self._move_to(angles, yaw, pitch, stop_event)
                        self._check_running(stop_event)
                        with self.lock:
                            self.state["phase"] = "capturing"
                        data = self.capture()
                        filename = f"{number:04d}_yaw{yaw:03d}_pitch{pitch:03d}.png"
                        (session_dir / filename).write_bytes(data)
                        writer.writerow((filename, yaw, pitch, time.strftime("%Y-%m-%d %H:%M:%S")))
                        manifest.flush()
                        with self.lock:
                            self.state.update(captured=number, last_photo=filename,
                                              angles=list(angles))
        except ScanStopped as exc:
            phase = str(exc)
        except Exception as exc:
            phase, error = "error", str(exc)
        finally:
            with self.lock:
                if self.state["captured"]:
                    self.state["phase"] = "packaging"
            try:
                if self.state["captured"]:
                    self.output_dir.mkdir(parents=True, exist_ok=True)
                    temp_zip = self.output_dir / f"{session_id}.partial.zip"
                    final_zip = self.output_dir / f"{session_id}.zip"
                    with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
                        for photo in sorted(session_dir.glob("*.png")):
                            archive.write(photo, arcname=f"{session_id}/{photo.name}")
                        archive.write(session_dir / "metadata.csv", arcname=f"{session_id}/metadata.csv")
                    temp_zip.replace(final_zip)
                    with self.lock:
                        self.state["download_url"] = f"/api/scan/download/{session_id}.zip"
            except Exception as exc:
                phase, error = "error", f"ZIP 만들기 실패: {exc} (PNG 원본은 서버에 보관됨)"
            with self.lock:
                self.state.update(active=False, phase=phase, error=error)
            self.vision.operation_lock.release()

    def photo_path(self, session_id, filename):
        path = self.output_dir / session_id / filename
        return path if path.is_file() else None

    def download_path(self, session_id):
        path = self.output_dir / f"{session_id}.zip"
        return path if path.is_file() else None
