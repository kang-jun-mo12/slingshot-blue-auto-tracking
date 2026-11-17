"""Pi-side PNG transport. No PyTorch dependency on the Raspberry Pi."""

import io
import json
import math
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler

FRAME_SIZE = (1920, 1080)
MAX_PNG_BYTES = 12 * 1024 * 1024
REMOTE_TIMEOUT_SECONDS = 15


class RetryableInferenceError(RuntimeError):
    """Temporary transport failure; retry with a new camera frame."""


def validate_detections(detections, size=FRAME_SIZE):
    if not isinstance(detections, list) or len(detections) > 50:
        raise ValueError("Invalid detection list")
    for item in detections:
        box = item.get("box") if isinstance(item, dict) else None
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError("Invalid detection box")
        score = item.get("confidence")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in [*box, score]):
            raise ValueError("Non-finite detection")
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1] and 0 <= score <= 1):
            raise ValueError("Detection outside image")
        corners = item.get("obb_corners")
        if corners is not None:
            if (not isinstance(corners, list) or len(corners) != 4 or
                    any(not isinstance(p, list) or len(p) != 2 for p in corners) or
                    any(type(v) not in (int, float) or not math.isfinite(v)
                        for p in corners for v in p)):
                raise ValueError("Invalid OBB corners")
            xs, ys = zip(*corners)
            enclosure = [max(0, min(xs)), max(0, min(ys)),
                         min(size[0], max(xs)), min(size[1], max(ys))]
            if any(abs(a-b) > .001 for a, b in zip(box, enclosure)):
                raise ValueError("OBB enclosure disagrees with corners")
            cx, cy = sum(xs)/4, sum(ys)/4
            if not (0 <= cx <= size[0] and 0 <= cy <= size[1]):
                raise ValueError("OBB center outside image")
            # Clipping the display enclosure must not shift the real OBB center.
            item["center"] = [round(cx, 1), round(cy, 1)]
        else:
            # Never trust a center unrelated to the actual box geometry.
            item["center"] = [round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1)]
        if not isinstance(item.get("class_name"), str):
            raise ValueError("Missing class name")
    return detections


class RemoteDetector:
    def __init__(self, base_url, timeout=REMOTE_TIMEOUT_SECONDS):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("데스크톱 주소를 --inference-url http://DESKTOP_IP:8090 으로 설정하세요")
        self.url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}))
        self.last_timing = {}

    def run(self, image):
        if image.size != FRAME_SIZE:
            raise ValueError("Inference requires a 1920x1080 image")
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG", compress_level=1)
        return self.run_png(buffer.getvalue())

    def run_png(self, png):
        frame_id = uuid.uuid4().hex
        request = Request(self.url + "/infer", data=png, headers={
            "Content-Type": "image/png", "X-Frame-Id": frame_id,
        })
        started = time.monotonic()
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(128 * 1024 + 1)
            if len(body) > 128 * 1024:
                raise ValueError("Inference response too large")
            result = json.loads(body)
        except HTTPError as exc:
            message = f"데스크톱 추론 서버 응답 오류 ({exc.code})"
            if exc.code >= 500 or exc.code == 429:
                raise RetryableInferenceError(message) from exc
            raise RuntimeError(message) from exc
        except (URLError, OSError) as exc:
            raise RetryableInferenceError(
                f"PNG 전송·응답 지연 또는 연결 끊김 ({self.url}, 대기 {self.timeout}초)"
            ) from exc
        except ValueError as exc:
            raise RuntimeError("데스크톱 추론 응답 형식이 잘못되었습니다") from exc
        elapsed = time.monotonic() - started
        if elapsed > self.timeout:
            raise RuntimeError("추론 응답이 너무 늦어 프레임을 폐기했습니다")
        if (result.get("frame_id") != frame_id or
                (result.get("width"), result.get("height")) != FRAME_SIZE):
            raise RuntimeError("추론 프레임 ID 또는 해상도가 일치하지 않습니다")
        self.last_timing = {"round_trip": round(elapsed * 1000),
                            "inference": result.get("inference_ms")}
        return validate_detections(result.get("detections"))


if __name__ == "__main__":
    import argparse
    from PIL import Image

    parser = argparse.ArgumentParser(description="Send one image to the desktop without moving servos")
    parser.add_argument("url")
    parser.add_argument("image")
    args = parser.parse_args()
    detector = RemoteDetector(args.url)
    with Image.open(args.image) as image:
        print(json.dumps(detector.run(image), ensure_ascii=False, indent=2))
    print(detector.last_timing)
