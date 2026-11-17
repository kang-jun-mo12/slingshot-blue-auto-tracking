import io
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from remote_inference import RemoteDetector
from camera_lock import camera_lock


ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "models" / "target_yolo11s_640_best.onnx"
CAPTURE_DIR = ROOT / "captures"


def prepare_image(image, size=640):
    image = image.convert("RGB")
    scale = min(size / image.width, size / image.height)
    width, height = round(image.width * scale), round(image.height * scale)
    left, top = (size - width) // 2, (size - height) // 2
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    canvas.paste(image.resize((width, height), Image.Resampling.BILINEAR), (left, top))
    tensor = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    return np.ascontiguousarray(tensor), scale, left, top


def decode_output(output, image_size, scale, left, top, threshold=0.25, iou_limit=0.45):
    predictions = np.asarray(output)
    if predictions.ndim != 3 or predictions.shape[0] != 1:
        raise ValueError(f"Unexpected model output: {predictions.shape}")
    predictions = predictions[0]
    if predictions.shape[0] == 5:
        predictions = predictions.T
    if predictions.shape[1] != 5:
        raise ValueError("Expected a single-class YOLO detection model")
    predictions = predictions[
        np.isfinite(predictions).all(axis=1) & (predictions[:, 4] >= threshold)
    ]
    if not len(predictions):
        return []
    scores = predictions[:, 4]
    centers, sizes = predictions[:, :2], predictions[:, 2:4]
    boxes = np.concatenate((centers - sizes / 2, centers + sizes / 2), axis=1)
    boxes = (boxes - np.array([left, top, left, top])) / scale
    width, height = image_size
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, width - 1)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, height - 1)
    valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
    boxes, scores = boxes[valid], scores[valid]
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    order = np.argsort(scores)[::-1]
    keep = []
    while len(order) and len(keep) < 50:
        current, rest = int(order[0]), order[1:]
        keep.append(current)
        intersection_start = np.maximum(boxes[current, :2], boxes[rest, :2])
        intersection_end = np.minimum(boxes[current, 2:], boxes[rest, 2:])
        intersection = np.maximum(0, intersection_end - intersection_start).prod(axis=1)
        union = areas[current] + areas[rest] - intersection
        order = rest[intersection / np.maximum(union, 1e-9) <= iou_limit]
    detections = []
    for idx in keep:
        x1, y1, x2, y2 = boxes[idx]
        detections.append({
            "class_name": "target",
            "confidence": round(float(scores[idx]), 4),
            "box": [round(float(value), 1) for value in boxes[idx]],
            "center": [round(float((x1 + x2) / 2), 1), round(float((y1 + y2) / 2), 1)],
        })
    return detections


class Detector:
    def __init__(self, model_path):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        model_input = self.session.get_inputs()[0]
        if model_input.shape != [1, 3, 640, 640] or model_input.type != "tensor(float)":
            raise ValueError(f"Unsupported model input: {model_input.shape} {model_input.type}")
        self.input_name = model_input.name

    def run(self, image):
        tensor, scale, left, top = prepare_image(image)
        output = self.session.run(None, {self.input_name: tensor})[0]
        return decode_output(output, image.size, scale, left, top)


def capture_photo():
    executable = shutil.which("rpicam-still") or shutil.which("libcamera-still")
    if executable is None:
        raise RuntimeError("rpicam-still is not installed")
    with camera_lock():
        completed = subprocess.run(
            [executable, "--nopreview", "--timeout", "1500", "--width", "1920",
             "--height", "1080", "--encoding", "png", "--output", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False,
        )
    if completed.returncode != 0:
        error = completed.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError("Camera capture failed: " + (error[-1] if error else "unknown error"))
    with Image.open(io.BytesIO(completed.stdout)) as image:
        return image.convert("RGB")


class OneShotVision:
    def __init__(self, model_path=MODEL_PATH, capture_dir=CAPTURE_DIR, inference_url=None):
        self.inference_url = inference_url
        self.model_path = Path(model_path)
        self.capture_dir = Path(capture_dir)
        self.detector = None
        self.operation_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.state = {"busy": False, "result": None, "error": None}

    def status(self):
        with self.state_lock:
            return {**self.state, "model_available": (bool(self.inference_url) if self.inference_url is not None
                                         else self.model_path.is_file()),
                    "backend": "desktop-pytorch" if self.inference_url is not None else "onnx",
                    "inference_url": self.inference_url,
                    "calibrated": False, "servo_motion": False}

    def capture_and_detect(self):
        if not self.operation_lock.acquire(blocking=False):
            raise BlockingIOError("A capture is already running")
        started = time.perf_counter()
        with self.state_lock:
            self.state.update(busy=True, error=None)
        try:
            if self.inference_url is None and not self.model_path.is_file():
                raise FileNotFoundError("YOLO ONNX model is missing")
            load_start = time.perf_counter()
            if self.detector is None:
                self.detector = (RemoteDetector(self.inference_url) if self.inference_url is not None
                                 else Detector(self.model_path))
            load_ms = (time.perf_counter() - load_start) * 1000
            capture_start = time.perf_counter()
            image = capture_photo()
            capture_ms = (time.perf_counter() - capture_start) * 1000
            inference_start = time.perf_counter()
            detections = self.detector.run(image)
            inference_ms = (time.perf_counter() - inference_start) * 1000
            capture_id = uuid.uuid4().hex
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            image.save(self.capture_dir / f"{capture_id}-raw.png", compress_level=1)
            draw = ImageDraw.Draw(image)
            for number, detection in enumerate(detections, 1):
                x1, y1, x2, y2 = detection["box"]
                draw.rectangle((x1, y1, x2, y2), outline=(69, 224, 155), width=3)
                label = f"{number}: {detection.get('class_name', 'target')} {detection['confidence']:.2f}"
                draw.text((x1 + 3, max(0, y1 - 15)), label, fill=(255, 230, 110),
                          stroke_width=1, stroke_fill=(0, 0, 0))
            image.save(self.capture_dir / f"{capture_id}.png", compress_level=1)
            result = {
                "id": capture_id, "captured_at": time.time(),
                "image_url": f"/api/vision/image/{capture_id}.png",
                "width": image.width, "height": image.height,
                "detections": detections,
                "timing_ms": {"model_load": round(load_ms), "capture": round(capture_ms),
                              "inference": round(inference_ms),
                              "total": round((time.perf_counter() - started) * 1000)},
                "servo_motion": False,
            }
            with self.state_lock:
                self.state["result"] = result
            return result
        except Exception as exc:
            with self.state_lock:
                self.state["error"] = str(exc)
            raise
        finally:
            with self.state_lock:
                self.state["busy"] = False
            self.operation_lock.release()
