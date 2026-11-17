"""Desktop PyTorch server: python desktop_inference.py --device auto."""

import argparse
import hashlib
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from remote_inference import FRAME_SIZE, MAX_PNG_BYTES, validate_detections

MODEL_PATH = Path(__file__).resolve().parent / "models" / "yolo26m_1920_best.pt"


def detections_from_result(result, size):
    """Keep OBB corners and use their actual center for camera alignment."""
    detections = []
    if result.obb is not None:
        polygons = result.obb.xyxyxyxy.cpu().tolist()
        scores = result.obb.conf.cpu().tolist()
        labels = result.obb.cls.cpu().tolist()
        for corners, confidence, class_id in zip(polygons, scores, labels):
            xs, ys = zip(*corners)
            detections.append({
                "box": [max(0, min(xs)), max(0, min(ys)),
                        min(size[0], max(xs)), min(size[1], max(ys))],
                "obb_corners": corners, "confidence": confidence,
                "class_id": int(class_id), "class_name": result.names[int(class_id)]})
    elif result.boxes is not None:
        for x1, y1, x2, y2, confidence, class_id in result.boxes.data.cpu().tolist():
            detections.append({"box": [x1, y1, x2, y2], "confidence": confidence,
                               "class_id": int(class_id),
                               "class_name": result.names[int(class_id)]})
    return validate_detections(detections, size)


class TorchDetector:
    def __init__(self, model_path=MODEL_PATH, device="auto", classes=None, imgsz=1920):
        import torch
        from ultralytics import YOLO

        if not Path(model_path).is_file() or Path(model_path).suffix != ".pt":
            raise ValueError(f"PyTorch model not found: {model_path}")
        self.device = ("0" if torch.cuda.is_available() else "cpu") if device == "auto" else device
        self.model = YOLO(str(model_path))
        self.model_sha256 = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
        self.task = self.model.task
        if self.task not in ("detect", "obb"):
            raise ValueError(f"Unsupported camera tracking task: {self.task}")
        self.imgsz = imgsz
        self.classes = classes
        self.torch = torch

    def metadata(self):
        return {"task": self.task, "class_names": self.model.names,
                "imgsz": self.imgsz, "model_sha256": self.model_sha256}

    def run(self, image):
        with self.torch.inference_mode():
            result = self.model.predict(image, imgsz=self.imgsz, rect=True, device=self.device,
                                        conf=0.25, iou=0.45, max_det=50,
                                        classes=self.classes, verbose=False)[0]
        return detections_from_result(result, image.size)


class InferenceHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, status, payload):
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/health":
            self.reply(404, {"error": "Not found"})
            return
        metadata = self.server.detector.metadata() if callable(
            getattr(type(self.server.detector), "metadata", None)) else {}
        self.reply(200, {"ready": True, "backend": "pytorch", "width": 1920, "height": 1080,
                         "device": self.server.detector.device,
                         "model": self.server.model_name, **metadata})

    def do_POST(self):
        if self.path != "/infer":
            self.reply(404, {"error": "Not found"})
            return
        if self.headers.get("Content-Type") != "image/png":
            self.reply(415, {"error": "Send image/png"})
            return
        # Reject concurrent requests instead of queuing obsolete camera frames.
        if not self.server.inference_lock.acquire(blocking=False):
            self.reply(503, {"error": "Inference busy"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_PNG_BYTES:
                raise ValueError("Invalid PNG length")
            frame_id = self.headers.get("X-Frame-Id", "")
            if not frame_id or len(frame_id) > 64:
                raise ValueError("X-Frame-Id is required (max 64 characters)")
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("Incomplete PNG")
            with Image.open(io.BytesIO(body)) as source:
                if source.format != "PNG" or source.size != FRAME_SIZE:
                    raise ValueError("Only 1920x1080 PNG is accepted")
                image = source.convert("RGB")
            started = time.perf_counter()
            detections = self.server.detector.run(image)
            self.reply(200, {"frame_id": frame_id, "width": image.width, "height": image.height,
                             "detections": detections,
                             "inference_ms": round((time.perf_counter() - started) * 1000)})
        except (ValueError, UnidentifiedImageError, OSError) as exc:
            self.reply(400, {"error": str(exc)})
        except Exception as exc:
            self.log_error("Inference failed: %s", exc)
            self.reply(500, {"error": "PyTorch inference failed; see desktop log"})
        finally:
            self.server.inference_lock.release()


def make_server(address, detector, model_name=MODEL_PATH.name):
    server = ThreadingHTTPServer(address, InferenceHandler)
    server.detector = detector
    server.model_name = model_name
    server.inference_lock = threading.Lock()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--model", type=Path, default=MODEL_PATH)
    parser.add_argument("--device", default="auto", help="auto, cpu, or CUDA index (0)")
    parser.add_argument("--imgsz", type=int, default=1920, help="YOLO inference size")
    parser.add_argument("--expected-task", choices=("detect", "obb"))
    parser.add_argument("--require-blue-target", action="store_true",
                        help="Require the existing tracker labels: class 1 is ally")
    parser.add_argument("--classes", type=int, nargs="+", help="Optional class IDs; default: both classes")
    args = parser.parse_args()
    detector = TorchDetector(args.model, args.device, args.classes, args.imgsz)
    if args.expected_task and detector.task != args.expected_task:
        raise ValueError(f"Expected {args.expected_task}, checkpoint is {detector.task}")
    if args.require_blue_target and detector.model.names.get(1) != "ally":
        raise ValueError(f"Tracker expects 1=ally (blue); model labels: {detector.model.names}")
    print(json.dumps({"model_path": str(args.model.resolve()), **detector.metadata()},
                     ensure_ascii=False), flush=True)
    print("Warming up PyTorch at 1920x1080...", flush=True)
    detector.run(Image.new("RGB", FRAME_SIZE))
    server = make_server((args.host, args.port), detector, args.model.name)
    print(f"Ready: http://{args.host}:{args.port} | {detector.device} | {args.model.name}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
