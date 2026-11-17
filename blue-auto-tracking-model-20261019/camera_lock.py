"""Share the camera with the existing data collector on port 8081."""

import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def camera_lock():
    import fcntl

    with Path("/tmp/servo_camera_capture.lock").open("a+b") as handle:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise BlockingIOError("다른 화면에서 카메라를 사용 중입니다") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
