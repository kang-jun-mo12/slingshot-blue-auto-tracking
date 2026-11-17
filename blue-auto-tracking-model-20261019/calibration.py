"""Empirical camera motion field measured in pixels per servo degree."""
import json
import hashlib
from pathlib import Path

import numpy as np
from servo_angles import ANGLE_STEP, checked_angles

MODEL_PATH = Path(__file__).resolve().parent / 'calibration' / 'model.json'


def coefficient_digest(model):
    return hashlib.sha256(json.dumps(model['coefficients'], sort_keys=True).encode()).hexdigest()


def qualify(model, validation):
    """Require both held-out historical pairs and current bidirectional checks.

    A 3-degree historical test may have up to 12 px median / 36 px p95
    error; current on-rig checks must be tighter (20 / 30 px). This is a
    coarse feed-forward estimate, always followed by a fresh observation.
    """
    historical = all(m['pairs'] >= 30 and m['validation_p95_px_per_degree'] < 12
                     and m['validation_median_px_per_degree'] < 4 for m in model['metrics'])
    records = validation.get('measurements', [])
    directions = set()
    for record in records:
        delta = np.asarray(record['to']) - record['from']
        if delta[1] != 0 or np.count_nonzero(delta) != 1:
            return False
        axis = 0 if delta[0] else 2
        directions.add((axis, int(np.sign(delta[axis]))))
    return bool(historical and validation.get('coefficient_digest') == coefficient_digest(model)
                and directions == {(0, -1), (0, 1), (2, -1), (2, 1)}
                and all(r['matches'] >= 30 and r['median_error_px'] < 20
                        and r['p95_error_px'] < 30 for r in records))


def features(x, y, yaw, pitch):
    u, v = (np.asarray(x) - 960) / 960, (np.asarray(y) - 540) / 540
    a, b = (np.asarray(yaw) - 90) / 60, (np.asarray(pitch) - 100) / 10
    u, v, a, b = np.broadcast_arrays(u, v, a, b)
    return np.stack([np.ones_like(u), u, v, u*u, u*v, v*v,
                     a, b, a*a, b*b, a*b, u*a, v*a, u*b, v*b], axis=-1)


def robust_fit(x, y):
    weights = np.ones(len(x))
    for _ in range(12):
        root = np.sqrt(weights)[:, None]
        coef = np.linalg.lstsq(x * root, y * root, rcond=None)[0]
        error = np.linalg.norm(x @ coef - y, axis=1)
        scale = max(.4, float(np.median(error)))
        weights = np.minimum(1, 1.5 * scale / np.maximum(error, .001))
    return coef


def fit_axis(samples):
    x = features(*samples[:, 2:6].T)
    y = samples[:, 6:8]
    # Hold out whole image pairs; points in a single pair are not independent.
    ids = np.unique(samples[:, 1])
    validation = np.isin(samples[:, 1], ids[::5])
    if validation.all() or not validation.any():
        raise ValueError('Insufficient independent calibration pairs')
    coef = robust_fit(x[~validation], y[~validation])
    errors = np.linalg.norm(x[validation] @ coef - y[validation], axis=1)
    metrics = {'pairs': len(ids), 'points': len(samples),
               'validation_pairs': int(len(ids[::5])),
               'validation_median_px_per_degree': round(float(np.median(errors)), 3),
               'validation_p95_px_per_degree': round(float(np.percentile(errors, 95)), 3)}
    return robust_fit(x, y), metrics


class PixelCalibration:
    def __init__(self, path=MODEL_PATH):
        self.path = Path(path)
        self.model = None
        if self.path.is_file():
            self.model = json.loads(self.path.read_text())
        self.coefficients = None
        if self.model is not None:
            coefficients = np.asarray(self.model.get('coefficients'), dtype=float)
            if (self.model.get('version') != 1 or self.model.get('image_size') != [1920, 1080]
                    or coefficients.shape != (2, 15, 2) or not np.isfinite(coefficients).all()):
                raise ValueError('잘못된 픽셀·각도 보정 파일입니다')
            self.coefficients = coefficients

    def status(self):
        if self.model is None:
            return {'valid': False, 'error': '각도별 사진 보정 자료가 없습니다'}
        summary = {k: v for k, v in self.model.items() if k != 'coefficients'}
        summary['valid'] = bool(self.model.get('valid') and qualify(
            self.model, self.model.get('current_validation', {})))
        return summary

    def require_valid(self):
        if self.model is None or not self.model.get('valid') or not qualify(
                self.model, self.model.get('current_validation', {})):
            raise RuntimeError('픽셀·서보 보정 검증이 필요합니다. 보정 상태를 확인하세요')

    def jacobian(self, pixel, pose):
        basis = features(*pixel, *pose)
        return np.stack([basis @ coefficient for coefficient in self.coefficients], axis=1)

    def is_centered(self, pixel):
        error = np.asarray(pixel, dtype=float) - [960, 540]
        band = self.model['deadband']
        radius = self.model.get('center_radius_px', float(np.linalg.norm(band)))
        return bool(np.all(np.abs(error) <= band) and np.linalg.norm(error) <= radius)

    def predict(self, pixel, start, end):
        """Integrate measured image motion along a simultaneous two-axis move."""
        point = np.array(pixel, dtype=float)
        start, end = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
        steps = max(1, int(np.ceil(np.max(np.abs(end-start)) * 4)))
        delta = (end-start) / steps
        for i in range(steps):
            pose = start + delta * i
            first = self.jacobian(point, pose) @ delta
            point += self.jacobian(point + first/2, pose + delta/2) @ delta
        return point

    def plan(self, pixel, angles):
        self.require_valid()
        angles = checked_angles(angles)
        point = np.asarray(pixel, dtype=float)
        if point.shape != (2,) or not np.isfinite(point).all() or not (
                0 <= point[0] <= 1920 and 0 <= point[1] <= 1080):
            raise ValueError('표적 픽셀 좌표가 잘못되었습니다')
        start = np.asarray([angles[0], angles[2]], dtype=float)
        bounds = np.asarray([self.model['yaw_range'], self.model['pitch_range']], dtype=float)
        if np.any(start < bounds[:, 0]) or np.any(start > bounds[:, 1]):
            raise RuntimeError('현재 각도가 보정 범위 밖입니다 (좌우 30~150°, 상하 90~110°)')
        error = point - [960, 540]
        if self.is_centered(point):
            return {'angles': list(angles), 'predicted_center': point.tolist(), 'reason': 'centered'}
        # Compute the complete destination within measured bounds. Numerical
        # solver increments do not cause intermediate motor commands.
        low, high = bounds[:, 0], bounds[:, 1]
        destination = start.copy()
        for _ in range(32):
            predicted = self.predict(point, start, destination)
            residual = predicted - [960, 540]
            jac = self.jacobian(predicted, destination)
            if np.linalg.cond(jac) > 30 or abs(np.linalg.det(jac)) < 4:
                raise RuntimeError('이 위치의 보정값이 불안정하여 이동하지 않습니다')
            correction = np.linalg.solve(jac, -residual)
            updated = np.clip(destination + np.clip(correction, -5, 5), low, high)
            if np.max(np.abs(updated-destination)) < .01:
                break
            destination = updated
        # Servo firmware accepts 0.1-degree angles: choose the best nearby
        # combination rather than independently rounding the two axes.
        candidates = []
        for yaw in {round(float(np.floor(destination[0]/ANGLE_STEP)*ANGLE_STEP), 1),
                    round(float(np.ceil(destination[0]/ANGLE_STEP)*ANGLE_STEP), 1)}:
            for pitch in {round(float(np.floor(destination[1]/ANGLE_STEP)*ANGLE_STEP), 1),
                          round(float(np.ceil(destination[1]/ANGLE_STEP)*ANGLE_STEP), 1)}:
                if not (low[0] <= yaw <= high[0] and low[1] <= pitch <= high[1]):
                    continue
                predicted = self.predict(point, start, [yaw, pitch])
                candidates.append((float(np.linalg.norm(predicted-[960,540])), yaw, pitch, predicted))
        _, yaw, pitch, predicted = min(candidates, key=lambda c: c[0])
        result = [yaw, angles[1], pitch]
        improvement = np.linalg.norm(error) - np.linalg.norm(predicted-[960,540])
        # A small but useful step into the centered region must not be rejected
        # by the ordinary 3 px improvement threshold.
        if result == list(angles) or improvement <= 0 or (
                improvement < 3 and not self.is_centered(predicted)):
            boundary = np.any(np.isclose(destination, low) | np.isclose(destination, high))
            return {'angles': list(angles), 'predicted_center': point.tolist(),
                    'reason': 'limit' if boundary else 'resolution_limit'}
        return {'angles': result, 'predicted_center': np.round(predicted, 1).tolist(), 'reason': 'moved'}
