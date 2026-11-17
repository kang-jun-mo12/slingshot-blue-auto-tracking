"""Shared 0.1-degree precision for the serial protocol and planner."""
import math

ANGLE_STEP = 0.1


def checked_angle(value, minimum=0, maximum=180):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not minimum <= value <= maximum):
        raise ValueError(f'각도는 {minimum}~{maximum}° 범위의 숫자여야 합니다')
    rounded = round(value, 1)
    if not math.isclose(value, rounded, rel_tol=0, abs_tol=1e-8):
        raise ValueError('각도는 0.1° 단위로 입력해 주세요')
    return 0.0 if rounded == 0 else rounded


def checked_angles(values):
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError('서보 각도 3개가 필요합니다')
    return [checked_angle(value) for value in values]


def format_angle(value):
    return f'{value:.1f}'.rstrip('0').rstrip('.')
