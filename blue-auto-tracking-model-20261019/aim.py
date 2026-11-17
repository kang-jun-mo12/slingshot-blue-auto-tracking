"""Small, camera-feedback steps for centering a detected target."""

import math
from target_policy import centering_targets


DEADBAND_X = 15
DEADBAND_Y = 12
MAX_TRACK_DISTANCE = 160


def _step_for_error(error, deadband):
    if abs(error) <= deadband:
        return 0
    size = 3 if abs(error) > 120 else 2 if abs(error) > 50 else 1
    return size if error > 0 else -size


def _select_target(detections, image_size, previous_center):
    detections = centering_targets(detections)
    if not detections:
        return None
    reference = previous_center or [image_size[0] / 2, image_size[1] / 2]
    def distance(center):
        return math.hypot((center[0] - reference[0]) * 640 / image_size[0],
                          (center[1] - reference[1]) * 480 / image_size[1])
    target = min(detections, key=lambda item: distance(item["center"]))
    if previous_center is not None and distance(target["center"]) > MAX_TRACK_DISTANCE:
        return None
    return target


class AimController:
    def __init__(self, vision, arduino):
        self.vision = vision
        self.arduino = arduino

    def step(self, data):
        expected = data.get("expected_angles")
        if (not isinstance(expected, list) or len(expected) != 3 or
                any(type(value) is not int or not 0 <= value <= 180 for value in expected)):
            raise ValueError("현재 서보 각도 3개가 필요합니다")
        reverse_yaw = data.get("reverse_yaw")
        reverse_pitch = data.get("reverse_pitch")
        if type(reverse_yaw) is not bool or type(reverse_pitch) is not bool:
            raise ValueError("좌우·상하 방향 설정이 필요합니다")
        observe_only = data.get("observe_only", False)
        if type(observe_only) is not bool:
            raise ValueError("촬영 전용 설정이 잘못되었습니다")
        previous_center = data.get("previous_center")
        if previous_center is not None and (
            not isinstance(previous_center, list) or len(previous_center) != 2 or
            any(type(value) not in (int, float) or not math.isfinite(value)
                for value in previous_center)
        ):
            raise ValueError("이전 표적 중심 좌표가 잘못되었습니다")
        previous_distance = data.get("previous_distance")
        if previous_distance is not None and (
            type(previous_distance) not in (int, float) or
            not math.isfinite(previous_distance) or previous_distance < 0
        ):
            raise ValueError("이전 중심 오차가 잘못되었습니다")

        # Never open a serial port on an aim request: opening it resets the board.
        self.arduino.require_angles(expected)
        result = self.vision.capture_and_detect()
        target = _select_target(result["detections"],
                                (result["width"], result["height"]), previous_center)
        payload = {"vision": result, "target": target, "angles": list(expected),
                   "moved": False, "centered": False, "reason": None}
        if target is None:
            payload["reason"] = "target_lost" if previous_center else "no_target"
            return payload

        dx = target["center"][0] - result["width"] / 2
        dy = target["center"][1] - result["height"] / 2
        distance = math.hypot(dx, dy)
        payload["error_px"] = [round(dx, 1), round(dy, 1)]
        payload["distance_px"] = round(distance, 1)

        # A worsening measurement is more likely a reversed axis, moving target,
        # or tracking error than a useful next step.
        if previous_distance is not None and distance > previous_distance + 25 * min(result["width"] / 640, result["height"] / 480):
            payload["reason"] = "diverging"
            return payload

        x_step = _step_for_error(dx * 640 / result["width"], DEADBAND_X)
        y_step = _step_for_error(dy * 480 / result["height"], DEADBAND_Y)
        if not x_step and not y_step:
            payload["centered"] = True
            payload["reason"] = "centered"
            return payload
        if observe_only:
            payload["reason"] = "max_steps"
            return payload

        # Positive screen error needs camera motion toward that side. The UI's
        # D/S directions are negative servo deltas unless an axis is reversed.
        yaw = -x_step * (-1 if reverse_yaw else 1)
        pitch = -y_step * (-1 if reverse_pitch else 1)
        angles = self.arduino.jog(yaw, pitch, expected_angles=expected)
        payload["angles"] = angles
        payload["moved"] = angles != expected
        payload["reason"] = "moved" if payload["moved"] else "limit"
        return payload
