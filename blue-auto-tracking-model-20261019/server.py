#!/usr/bin/env python3
import argparse
import glob
import json
import os
import re
import shutil
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import serial
from serial.tools import list_ports

from aim import AimController
from live_tracker import LiveTracker
from flag_cycle import FlagCycleTracker
from scan import PhotoScanner
from vision import OneShotVision
from servo_angles import checked_angle, checked_angles, format_angle


BAUD_RATE = 115200
WEB_ROOT = Path(__file__).parent / "web"


class ArduinoConnection:
    def __init__(self, preferred_port=None):
        self.preferred_port = preferred_port
        self.connection = None
        self.port = None
        self.last_error = "Arduino 연결 대기 중"
        self.lock = threading.Lock()
        self.last_angles = None

    def candidate_ports(self):
        if self.preferred_port:
            yield self.preferred_port
            return

        seen = set()
        for info in list_ports.comports():
            if info.device not in seen:
                seen.add(info.device)
                yield info.device
        for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
            for device in glob.glob(pattern):
                if device not in seen:
                    seen.add(device)
                    yield device

    def connect(self):
        if self.connection and self.connection.is_open:
            return True

        for device in self.candidate_ports():
            try:
                connection = serial.Serial(device, BAUD_RATE, timeout=1, write_timeout=2)
                time.sleep(2.0)  # Most Arduino boards reset when serial opens.
                connection.reset_input_buffer()
                connection.write(b'CAPS\n')
                connection.flush()
                if connection.readline().strip() != b'CAPS fractional-0.1 pulse-us':
                    connection.close()
                    self.last_error = f'{device}: 소수점 제어 펌웨어를 업로드해 주세요'
                    continue
                self.connection = connection
                self.port = device
                self.last_error = ""
                self.last_angles = None
                return True
            except (OSError, serial.SerialException) as exc:
                self.last_error = f"{device}: {exc}"

        self.port = None
        if not self.last_error:
            self.last_error = "Arduino USB 포트를 찾지 못했습니다"
        return False

    def close(self):
        if self.connection:
            try:
                self.connection.close()
            except serial.SerialException:
                pass
        self.connection = None
        self.port = None
        self.last_angles = None

    def _write_angles(self, angles):
        angles = checked_angles(angles)
        command = ",".join(format_angle(angle) for angle in angles) + "\n"
        try:
            self.connection.reset_input_buffer()
            self.connection.write(command.encode("ascii"))
            self.connection.flush()
            response = self.connection.readline().decode("ascii", errors="replace").strip()
            if response != "OK " + command.strip():
                raise RuntimeError(response or "Arduino 응답 시간 초과: 적용 여부를 확인하지 못했습니다")
            self.last_angles = list(angles)
            return response
        except (OSError, serial.SerialException):
            self.close()
            raise ConnectionError("Arduino 연결이 끊어졌습니다")
        except RuntimeError as exc:
            self.close()
            self.last_error = str(exc)
            raise

    def send_angles(self, angles):
        angles = checked_angles(angles)
        with self.lock:
            if not self.connect():
                raise ConnectionError(self.last_error)
            return self._write_angles(angles)

    def ensure_ready(self):
        """Initialize on an explicit tracking action, preserving known positions."""
        with self.lock:
            if not self.connect():
                raise ConnectionError(self.last_error)
            if self.last_angles is None:
                # Matches firmware startup; never apply unsubmitted UI drafts.
                self._write_angles([90, 90, 90])
            return list(self.last_angles)

    def require_angles(self, expected_angles):
        with self.lock:
            if not self.connection or not self.connection.is_open or self.last_angles is None:
                raise RuntimeError("먼저 각도 적용을 눌러 현재 위치를 설정해 주세요")
            if self.last_angles != expected_angles:
                raise RuntimeError("서보 위치가 다른 창에서 변경되었습니다. 상태를 새로 확인해 주세요")
            return list(self.last_angles)

    def jog(self, yaw, pitch, expected_angles=None):
        yaw, pitch = checked_angle(yaw, -5, 5), checked_angle(pitch, -5, 5)
        with self.lock:
            if not self.connection or not self.connection.is_open or self.last_angles is None:
                raise RuntimeError("먼저 각도 적용을 눌러 현재 위치를 설정해 주세요")
            if expected_angles is not None and self.last_angles != expected_angles:
                raise RuntimeError("서보 위치가 다른 창에서 변경되었습니다. 상태를 새로 확인해 주세요")
            angles = list(self.last_angles)
            angles[0] = round(max(0, min(180, angles[0] + yaw)), 1)
            angles[2] = round(max(0, min(180, angles[2] + pitch)), 1)
            if angles != self.last_angles:
                self._write_angles(angles)
            return angles

    def move_flag(self, return_to_zero=False, expected_angles=None, before_go=None):
        if type(return_to_zero) is not bool:
            raise ValueError("복귀 설정은 참 또는 거짓이어야 합니다")
        with self.lock:
            if not self.connection or not self.connection.is_open or self.last_angles is None:
                raise RuntimeError("먼저 각도 적용 또는 추적 시작으로 서보를 연결해 주세요")
            if expected_angles is not None and self.last_angles != expected_angles:
                raise RuntimeError("거리 보정 후 서보 위치가 변경되었습니다")
            if before_go is not None:
                before_go()
            angles = list(self.last_angles)
            angles[1] = 180
            self._write_angles(angles)
            if return_to_zero:
                time.sleep(1)
                angles[1] = 0
                self._write_angles(angles)
            return list(angles)

    def status(self):
        with self.lock:
            # Opening serial resets this firmware and moves every servo to 90.
            # A status poll must only enumerate ports, never open one.
            if self.port and not Path(self.port).exists():
                self.close()
            connected = bool(self.connection and self.connection.is_open)
            ready = connected and self.last_angles is not None
            available = [port for port in self.candidate_ports() if Path(port).exists()]
            detected_port = self.port or next(iter(available), None)
            return {
                "connected": connected,
                "ready": ready,
                "available": bool(detected_port),
                "angles": list(self.last_angles) if self.last_angles is not None else None,
                "angle_step": 0.1,
                "port": detected_port,
                "message": (f"{detected_port} 연결됨" if connected else
                            f"{detected_port} 감지됨" if detected_port else "Arduino 연결 대기 중"),
            }

    def move_camera_to(self, yaw, pitch, expected_angles):
        """One acknowledged command for a calibrated two-axis move; preserve D9."""
        yaw, pitch = checked_angle(yaw), checked_angle(pitch)
        with self.lock:
            if not self.connection or not self.connection.is_open or self.last_angles != expected_angles:
                raise RuntimeError("서보 현재 위치를 확인할 수 없습니다")
            angles = [yaw, self.last_angles[1], pitch]
            if angles != self.last_angles:
                self._write_angles(angles)
            return list(angles)


class RequestHandler(SimpleHTTPRequestHandler):
    arduino = None
    vision = None
    aim = None
    tracker = None
    scanner = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/tracker.html":
            self.send_response(302)
            self.send_header("Location", "/#tracking")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/status":
            self.send_json(200, self.arduino.status())
            return
        if path == "/api/vision":
            self.send_json(200, self.vision.status())
            return
        if path == "/api/tracker/status":
            self.send_json(200, self.tracker.status())
            return
        if path == "/api/calibration":
            self.send_json(200, self.tracker.calibration.status())
            return
        if path == "/api/scan/status":
            self.send_json(200, self.scanner.status())
            return
        if path == "/api/tracker/frame":
            value = parse_qs(urlsplit(self.path).query).get("after", ["0"])[0]
            if not value.isdecimal() or len(value) > 12:
                self.send_json(400, {"error": "Invalid frame number"})
                return
            frame = self.tracker.frame_after(int(value))
            if frame is None:
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            sequence, body = frame
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Frame-Seq", str(sequence))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        match = re.fullmatch(r"/api/vision/image/([a-f0-9]{32})\.(png|jpg)", path)
        if match:
            file = self.vision.capture_dir / f"{match[1]}.{match[2]}"
            if not file.is_file():
                self.send_json(404, {"error": "Image not found"})
                return
            body = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png" if match[2] == "png" else "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        match = re.fullmatch(r"/api/scan/photo/([a-f0-9]{32})/(\d{4}_yaw\d{3}_pitch\d{3}\.png)", path)
        if match:
            file = self.scanner.photo_path(match[1], match[2])
            if file is None:
                self.send_json(404, {"error": "Photo not found"})
                return
            body = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        match = re.fullmatch(r"/api/scan/download/([a-f0-9]{32})\.zip", path)
        if match:
            file = self.scanner.download_path(match[1])
            if file is None:
                self.send_json(404, {"error": "Scan archive not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="scan_{match[1]}.zip"')
            self.send_header("Content-Length", str(file.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with file.open("rb") as source:
                shutil.copyfileobj(source, self.wfile)
            return
        super().do_GET()

    def do_POST(self):
        if self.path not in ("/api/flag", "/api/angles", "/api/jog", "/api/servo/ready", "/api/vision/capture",
                             "/api/aim/step", "/api/tracker/start",
                             "/api/tracker/stop", "/api/tracker/heartbeat",
                             "/api/scan/start", "/api/scan/stop", "/api/scan/heartbeat"):
            self.send_json(404, {"error": "Not found"})
            return

        try:
            if self.headers.get_content_type() != "application/json":
                self.send_json(415, {"ok": False, "error": "Expected application/json"})
                return
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self.send_json(403, {"ok": False, "error": "Cross-origin request rejected"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            if self.path == "/api/tracker/start":
                self.send_json(200, {"ok": True, **self.tracker.start(
                    data["reverse_yaw"], data["reverse_pitch"])})
                return
            if self.path == "/api/tracker/stop":
                self.send_json(200, {"ok": True, **self.tracker.stop()})
                return
            if self.path == "/api/tracker/heartbeat":
                self.send_json(200, {"ok": True, **self.tracker.heartbeat()})
                return
            if self.path == "/api/scan/start":
                self.send_json(200, {"ok": True, **self.scanner.start(
                    data["yaw_min"], data["yaw_max"])})
                return
            if self.path == "/api/scan/stop":
                self.send_json(200, {"ok": True, **self.scanner.stop()})
                return
            if self.path == "/api/scan/heartbeat":
                self.send_json(200, {"ok": True, **self.scanner.heartbeat()})
                return
            if self.tracker.status()["active"]:
                raise BlockingIOError("실시간 추적 중에는 다른 촬영·서보 조작을 할 수 없습니다")
            if self.scanner.status()["active"]:
                raise BlockingIOError("자동 촬영 중에는 다른 촬영·서보 조작을 할 수 없습니다")
            if self.path == "/api/flag":
                return_to_zero = data.get("return_to_zero", False)
                if type(return_to_zero) is not bool:
                    raise ValueError("복귀 설정은 참 또는 거짓이어야 합니다")
                # Reject cached browsers that still request the removed automatic handoff.
                if "tracking_session" in data:
                    raise ValueError("자동 추적은 최종 서보 보정까지만 진행합니다. 브라우저를 새로고침해 주세요")
                if not self.vision.operation_lock.acquire(blocking=False):
                    raise BlockingIOError("다른 촬영·이동 작업이 진행 중입니다")
                try:
                    angles = self.arduino.move_flag(return_to_zero)
                finally:
                    self.vision.operation_lock.release()
                self.send_json(200, {"ok": True, "angles": angles})
                return
            if self.path == "/api/servo/ready":
                if not self.vision.operation_lock.acquire(blocking=False):
                    raise BlockingIOError("카메라가 이미 사용 중입니다")
                try:
                    self.send_json(200, {"ok": True, "angles": self.arduino.ensure_ready()})
                finally:
                    self.vision.operation_lock.release()
                return
            if self.path == "/api/vision/capture":
                self.send_json(200, {"ok": True, **self.vision.capture_and_detect()})
                return
            if self.path == "/api/aim/step":
                self.send_json(200, {"ok": True, **self.aim.step(data)})
                return
            if self.path == "/api/jog":
                angles = self.arduino.jog(data["yaw"], data["pitch"])
                self.send_json(200, {"ok": True, "angles": angles})
                return
            angles = checked_angles(data["angles"])
            response = self.arduino.send_angles(angles)
            self.send_json(200, {"ok": True, "message": response, "angles": angles})
        except BlockingIOError as exc:
            self.send_json(409, {"ok": False, "error": str(exc)})
        except (KeyError, UnicodeDecodeError, ValueError) as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except (ConnectionError, RuntimeError) as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.log_error("Request failed: %s", exc)
            self.send_json(503, {"ok": False, "error": str(exc)})

    def log_message(self, format_string, *args):
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def main():
    parser = argparse.ArgumentParser(description="Arduino 3-servo web controller")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--serial-port", help="예: /dev/ttyACM0 (생략 시 자동 검색)")
    parser.add_argument("--inference-url", default=os.environ.get("YOLO_INFERENCE_URL", ""),
                        help="Desktop URL, e.g. http://192.168.0.10:8090")
    parser.add_argument("--local-onnx", action="store_true", help="Use legacy Pi ONNX inference")
    args = parser.parse_args()
    inference_url = None if args.local_onnx else args.inference_url

    RequestHandler.arduino = ArduinoConnection(args.serial_port)
    RequestHandler.vision = OneShotVision(inference_url=inference_url)
    RequestHandler.aim = AimController(RequestHandler.vision, RequestHandler.arduino)
    RequestHandler.tracker = FlagCycleTracker(RequestHandler.vision, RequestHandler.arduino, inference_url=inference_url)
    RequestHandler.scanner = PhotoScanner(RequestHandler.vision, RequestHandler.arduino)
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    print(f"Servo controller: http://localhost:{args.port}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        RequestHandler.arduino.close()
        server.server_close()


if __name__ == "__main__":
    main()
