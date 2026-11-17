"""Offline distance/offset learning. Not imported by the running servo server."""
import hashlib
import json
import math
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

from distance_calibration import DistanceCalibration


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def collect_records(calibration_path, history_root, backups_root, use_confirmed_history=False):
    """Keep measured rows and explicitly authorized teacher labels distinct."""
    calibration = DistanceCalibration(calibration_path)
    data = calibration.data
    versions = {}
    paths = [Path(calibration_path), *sorted(Path(backups_root).glob('*/calibration/flag-distance.json'))]
    for path in paths:
        snapshot = json.loads(path.read_text())
        # Only normalize historical trims when the underlying measurements agree.
        if digest(snapshot['measurements']) != digest(data['measurements']):
            continue
        entry = dict(pitch_trim_deg=snapshot.get('pitch_trim_deg', 0),
                     pitch_direction=snapshot['pitch_direction'])
        version = snapshot['version']
        if version in versions and entry != versions[version]:
            raise ValueError(f'Conflicting pitch definitions for calibration version {version}')
        versions[version] = entry
    records = [dict(sample_id=f'measured-{i:02d}', group_id='original-manual-table',
                    source='manual_measurement', width_px=row['width_px'],
                    height_px=row['height_px'], distance_m=row['distance_m'],
                    raw_pitch_offset_deg=row['pitch_offset_deg'])
               for i, row in enumerate(calibration.rows, 1)]
    counts = Counter()
    excluded = []
    files = sorted(Path(history_root).glob('*/events.jsonl'))
    for path in files:
        for line_number, line in enumerate(path.read_text().splitlines(), 1):
            try:
                event = json.loads(line)
            except ValueError:
                excluded.append(dict(path=str(path), line=line_number, reason='invalid_json'))
                continue
            kind = event.get('kind', 'unknown')
            counts[kind] += 1
            if kind != 'distance_estimate' or not use_confirmed_history:
                continue
            try:
                width, height = event['box_size_px']
                distance, pitch = event['distance_m'], event['pitch_offset_deg']
                definition = versions[event['calibration_version']]
                if (not all(type(x) in (int, float) and math.isfinite(x)
                            for x in (width, height, distance, pitch))
                        or not 0 < width <= 1920 or not 0 < height <= 1080
                        or not calibration.rows[0]['distance_m'] <= distance <= calibration.rows[-1]['distance_m']):
                    raise ValueError('invalid_size_or_label')
                raw_pitch = (pitch - definition['pitch_trim_deg']) / definition['pitch_direction']
                if not 0 <= raw_pitch <= 180:
                    raise ValueError('invalid_pitch_label')
            except (KeyError, TypeError, ValueError) as exc:
                excluded.append(dict(path=str(path), line=line_number, reason=str(exc)))
                continue
            records.append(dict(sample_id=f'{path.parent.name}:{line_number}',
                                group_id=path.parent.name,
                                source='teacher_output_user_confirmed',
                                width_px=width, height_px=height, distance_m=distance,
                                raw_pitch_offset_deg=raw_pitch,
                                calibration_version=event['calibration_version'],
                                range_clamped=event.get('range_clamped', False),
                                box_source='last_tracking_box' if 'source_observation' in event
                                else 'stationary_photo_aggregate',
                                source_path=str(path), source_line=line_number))
    audit = dict(manual_rows=len(calibration.rows), history_sessions=len(files),
                 history_events=dict(counts), accepted_rows=len(records),
                 accepted_sources=dict(Counter(r['source'] for r in records)),
                 unique_boxes=len({(r['width_px'], r['height_px']) for r in records}),
                 user_confirmation_scope='Existing calculated results were compared and matched, per user on 2026-10-14'
                 if use_confirmed_history else None,
                 excluded=excluded, dataset_sha256=digest(records),
                 original_measurements_sha256=digest(calibration.rows),
                 label_note='Teacher labels are user-confirmed outputs of the existing estimator; '
                            'they are not additional independent tape-measure observations.')
    return records, audit


class DistanceOffsetModel:
    """Two learned regressors: inverse box scale -> distance -> raw pitch offset."""
    def __init__(self, nu=1.5):
        self.nu = nu

    def _regressor(self, x, y):
        kernel = ConstantKernel(1, (0.01, 100)) * Matern(.3, (.02, 10), nu=self.nu)
        kernel += WhiteKernel(1e-4, (1e-7, .05))
        gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True,
                                      n_restarts_optimizer=1, random_state=42)
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter('always', ConvergenceWarning)
            gp.fit(x, y)
        self.fit_warnings.extend(str(w.message) for w in notices)
        return gp

    def fit(self, records):
        if len(records) < 3:
            raise ValueError('At least three labeled records are required')
        self.fit_warnings = []
        inverse_scale = np.asarray([1 / math.sqrt(r['width_px'] * r['height_px']) for r in records])
        distance = np.asarray([r['distance_m'] for r in records])
        pitch = np.asarray([r['raw_pitch_offset_deg'] for r in records])
        self.scale_min = float(inverse_scale.min())
        self.scale_span = float(np.ptp(inverse_scale))
        self.distance_min = float(distance.min())
        self.distance_span = float(np.ptp(distance))
        if min(self.scale_span, self.distance_span) <= 0:
            raise ValueError('Training records must cover different sizes and distances')
        x = ((inverse_scale - self.scale_min) / self.scale_span).reshape(-1, 1)
        self.distance_gp = self._regressor(x, distance)
        dx = ((distance - self.distance_min) / self.distance_span).reshape(-1, 1)
        self.pitch_gp = self._regressor(dx, pitch)
        return self

    def predict(self, sizes, yaw_offset_deg=-2, pitch_trim_deg=-1, pitch_direction=1):
        array = np.asarray(sizes, dtype=float)
        if array.ndim != 2 or array.shape[1] != 2 or not np.isfinite(array).all() or np.any(array <= 0):
            raise ValueError('Positive finite width/height pairs are required')
        inverse_scale = 1 / np.sqrt(array[:, 0] * array[:, 1])
        x = ((inverse_scale - self.scale_min) / self.scale_span).reshape(-1, 1)
        distance, distance_std = self.distance_gp.predict(x, return_std=True)
        dx = ((distance - self.distance_min) / self.distance_span).reshape(-1, 1)
        pitch, pitch_std = self.pitch_gp.predict(dx, return_std=True)
        return [dict(distance_m=float(d), raw_pitch_offset_deg=float(p),
                     yaw_offset_deg=float(yaw_offset_deg),
                     pitch_offset_deg=round(float(p * pitch_direction + pitch_trim_deg), 1),
                     distance_model_std_m=float(ds), pitch_conditional_model_std_deg=float(ps),
                     within_training_box_scale=bool(0 <= feature <= 1))
                for d, p, ds, ps, feature in zip(distance, pitch, distance_std, pitch_std, x[:, 0])]


def error_metrics(records, predictions):
    distance = np.abs([p['distance_m'] - r['distance_m'] for r, p in zip(records, predictions)])
    pitch = np.abs([p['raw_pitch_offset_deg'] - r['raw_pitch_offset_deg'] for r, p in zip(records, predictions)])
    return dict(rows=len(records), distance_mae_m=float(distance.mean()),
                distance_max_error_m=float(distance.max()),
                pitch_mae_deg=float(pitch.mean()), pitch_max_error_deg=float(pitch.max()))
