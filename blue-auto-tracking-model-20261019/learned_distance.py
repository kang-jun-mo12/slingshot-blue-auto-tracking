"""NumPy-only inference of the trained module-3 Gaussian process models."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from distance_calibration import DEFAULT_PATH, DistanceCalibration, DistanceMeasurementError

MODEL_PATH = Path(__file__).resolve().parent / 'calibration' / 'distance-model.json'


class LearnedDistanceCalibration(DistanceCalibration):
    """Keep input/servo validation; replace distance and pitch interpolation."""
    def __init__(self, path=DEFAULT_PATH, model_path=MODEL_PATH):
        super().__init__(path)
        self.learned = json.loads(Path(model_path).read_text())
        expected = hashlib.sha256(json.dumps(self.rows, sort_keys=True).encode()).hexdigest()
        if (self.learned.get('format_version') != 1
                or self.learned.get('original_measurements_sha256') != expected):
            raise ValueError('학습 모델과 거리 보정 설정이 일치하지 않습니다')
        for name in ('distance_gp', 'pitch_gp'):
            gp = self.learned[name]
            x, alpha = np.asarray(gp['x'], dtype=float), np.asarray(gp['alpha'], dtype=float)
            numbers = [gp['amplitude'], gp['length_scale'], gp['mean'], gp['std'], *x, *alpha]
            if (gp['nu'] != 1.5 or x.ndim != 1 or alpha.shape != x.shape or not len(x)
                    or not all(math.isfinite(n) for n in numbers)
                    or min(gp['amplitude'], gp['length_scale'], gp['std']) <= 0):
                raise ValueError('거리 학습 모델의 계수가 올바르지 않습니다')
        for name in ('scale_min', 'scale_span', 'distance_min', 'distance_span'):
            value = self.learned[name]
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('거리 학습 모델의 정규화 값이 올바르지 않습니다')
        if min(self.learned['scale_span'], self.learned['distance_span']) <= 0:
            raise ValueError('거리 학습 모델의 범위가 올바르지 않습니다')

    def _predict_gp(self, name, value):
        gp = self.learned[name]
        radius = math.sqrt(3) * np.abs(np.asarray(gp['x']) - value) / gp['length_scale']
        kernel = gp['amplitude'] * (1 + radius) * np.exp(-radius)
        return float(kernel @ np.asarray(gp['alpha']) * gp['std'] + gp['mean'])

    def _estimate_size(self, median, used_samples):
        scale = math.sqrt(median[0] * median[1])
        if scale > self.scales[0] * 1.02 or scale < self.scales[-1] * .98:
            raise DistanceMeasurementError('박스 크기가 측정표의 1.50~2.00m 범위를 벗어났습니다.')
        clamped_scale = min(self.scales[0], max(self.scales[-1], scale))
        for index in range(len(self.rows) - 1):
            if self.scales[index] >= clamped_scale >= self.scales[index + 1]:
                break
        a, b = self.rows[index:index + 2]
        fraction = (self.scales[index] - clamped_scale) / (self.scales[index] - self.scales[index + 1])
        # The measured table is retained only for box geometry/domain checks.
        # Neither distance nor pitch is taken from its interpolated values.
        ratio = ((a['width_px'] + fraction * (b['width_px'] - a['width_px']))
                 / (a['height_px'] + fraction * (b['height_px'] - a['height_px'])))
        if abs(median[0] / median[1] / ratio - 1) > .12:
            raise DistanceMeasurementError('박스의 가로·세로 비율이 측정표와 다릅니다. 같은 표적과 자세인지 확인하세요.')
        normalized = (1 / clamped_scale - self.learned['scale_min']) / self.learned['scale_span']
        predicted_distance = self._predict_gp('distance_gp', normalized)
        distance = min(self.rows[-1]['distance_m'], max(self.rows[0]['distance_m'], predicted_distance))
        normalized_distance = (distance - self.learned['distance_min']) / self.learned['distance_span']
        raw_pitch = self._predict_gp('pitch_gp', normalized_distance)
        if not all(math.isfinite(v) for v in (distance, raw_pitch)):
            raise DistanceMeasurementError('거리 학습 모델이 유효한 값을 반환하지 못했습니다.')
        return dict(distance_m=round(distance, 4), box_size_px=[round(x, 2) for x in median],
                    yaw_offset_deg=round(self.data['yaw_offset_deg'] + self.data.get('yaw_trim_deg', 0), 1),
                    pitch_offset_deg=round(raw_pitch * self.data['pitch_direction'] + self.data.get('pitch_trim_deg', 0), 1),
                    used_samples=used_samples, sample_count=len(used_samples),
                    range_clamped=not math.isclose(scale, clamped_scale, abs_tol=1e-8)
                                  or not math.isclose(distance, predicted_distance, abs_tol=1e-8),
                    bracket_m=[a['distance_m'], b['distance_m']],
                    calibration_version=self.data['version'], estimator='gaussian_process',
                    model_id=self.learned['model_id'])
