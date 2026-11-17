"""Lossless Pi camera worker: length-prefixed 1920x1080 PNG on stdout."""

import io
import struct
import sys

from remote_inference import FRAME_SIZE, MAX_PNG_BYTES
from camera_lock import camera_lock


def read_exact(stream, length):
    data = bytearray()
    while len(data) < length:
        chunk = stream.read(length - len(data))
        if not chunk:
            if data:
                raise EOFError("Incomplete camera frame")
            return None
        data.extend(chunk)
    return bytes(data)


def iter_pngs(stream):
    while header := read_exact(stream, 4):
        length, = struct.unpack("!I", header)
        if not 0 < length <= MAX_PNG_BYTES:
            raise ValueError("Invalid camera frame length")
        frame = read_exact(stream, length)
        if frame is None:
            raise EOFError("Missing camera frame")
        yield frame


def main():
    from picamera2 import Picamera2

    output = sys.stdout.buffer
    # Third-party camera diagnostics must not corrupt the binary frame stream.
    sys.stdout = sys.stderr
    with camera_lock(), Picamera2() as camera:
        config = camera.create_video_configuration(
            main={"size": FRAME_SIZE, "format": "RGB888"},
            controls={"FrameRate": 10}, buffer_count=4, queue=False,
        )
        camera.configure(config)
        camera.start()
        while True:
            image = camera.capture_image("main")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", compress_level=1)
            frame = buffer.getvalue()
            output.write(struct.pack("!I", len(frame)))
            output.write(frame)
            output.flush()


if __name__ == "__main__":
    main()
