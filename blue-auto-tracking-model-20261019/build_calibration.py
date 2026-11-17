"""Measure pixel motion from existing servo-grid PNGs; never accesses hardware."""
import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

from calibration import fit_axis, MODEL_PATH, qualify


def measure(root):
    cv2.setNumThreads(2)
    orb = cv2.ORB_create(nfeatures=2400)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    rows, report = [], []
    for manifest in sorted(Path(root).glob('*/metadata.csv')):
        records = list(csv.DictReader(manifest.open()))
        # Partial scans still contain useful adjacent image pairs.
        if len(records) < 2:
            continue
        frames = {}
        for record in records:
            path = manifest.parent / record['filename']
            raw = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if raw is None or raw.shape != (1080, 1920):
                continue
            image = cv2.resize(raw, (960, 540))
            points, descriptors = orb.detectAndCompute(image, None)
            frames[(float(record['yaw_deg']), float(record['pitch_deg']))] = (points, descriptors)
        print(f'{manifest.parent.name}: {len(frames)} images', flush=True)
        for pose, (points, desc) in sorted(frames.items()):
            for axis in range(2):
                # Last pitch interval is 108 -> 110 rather than 3 degrees.
                neighbor = list(pose)
                neighbor[axis] += 2 if axis == 1 and pose[1] == 108 else 3
                neighbor = tuple(neighbor)
                if neighbor not in frames or desc is None:
                    continue
                other, other_desc = frames[neighbor]
                if other_desc is None:
                    continue
                matches = [m[0] for m in matcher.knnMatch(desc, other_desc, k=2)
                           if len(m) == 2 and m[0].distance < .7 * m[1].distance]
                if len(matches) < 25:
                    continue
                src = np.float32([points[m.queryIdx].pt for m in matches]) * 2
                dst = np.float32([other[m.trainIdx].pt for m in matches]) * 2
                matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4)
                if matrix is None:
                    continue
                keep = mask.ravel().astype(bool)
                src, dst = src[keep], dst[keep]
                if len(src) < 25 or keep.mean() < .6:
                    continue
                span = np.ptp(src, axis=0)
                if span[0] < 500 or span[1] < 250:
                    continue
                degrees = neighbor[axis] - pose[axis]
                midpoint = (src + dst) / 2
                velocity = (dst - src) / degrees
                # Uniformly sample spatial cells so textured objects do not
                # overwhelm measurements elsewhere in the image.
                cells = (midpoint // [160, 135]).astype(int)
                selected = []
                for cell in np.unique(cells, axis=0):
                    ids = np.flatnonzero((cells == cell).all(axis=1))
                    selected.extend(ids[:8])
                pair_id = len(report)
                report.append({'session': manifest.parent.name, 'from': pose,
                               'to': neighbor, 'axis': axis, 'inliers': len(src)})
                mid_pose = (np.array(pose) + neighbor) / 2
                for idx in selected:
                    rows.append([axis, pair_id, *midpoint[idx], *mid_pose, *velocity[idx]])
        print(f'  cumulative accepted pairs: {len(report)}', flush=True)
    return np.asarray(rows, dtype=float), report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sessions', default='scan_sessions')
    parser.add_argument('--output', type=Path, default=MODEL_PATH)
    args = parser.parse_args()
    samples, pairs = measure(args.sessions)
    if not len(samples):
        raise RuntimeError('No usable image pairs')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output.with_suffix('.npz'), samples=samples)
    args.output.with_name('pairs.json').write_text(json.dumps(pairs, indent=2))
    axes, metrics = [], []
    for axis in range(2):
        subset = samples[samples[:, 0] == axis]
        coeff, metric = fit_axis(subset)
        axes.append(coeff.tolist())
        metrics.append(metric)
    model = {'version': 1, 'image_size': [1920, 1080], 'coefficients': axes,
             'yaw_range': [30, 150], 'pitch_range': [90, 110],
             'movement_policy': 'direct_then_repeat_correction', 'deadband': [12, 12],
             'center_radius_px': 15, 'metrics': metrics,
             'sample_count': len(samples), 'pair_count': len(pairs),
             'sessions': sorted({p['session'] for p in pairs}),
             'note': 'Empirical interpolation of static-scene feature motion, not per-pixel physical measurement.'}
    # Historical photos alone cannot establish that today's mounting matches.
    validation_path = args.output.parent / 'current-validation' / 'validation.json'
    model['current_validation'] = json.loads(validation_path.read_text()) if validation_path.is_file() else {}
    model['valid'] = qualify(model, model['current_validation'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output.with_suffix('.npz'), samples=samples)
    args.output.with_name('pairs.json').write_text(json.dumps(pairs, indent=2))
    temp = args.output.with_suffix('.tmp')
    temp.write_text(json.dumps(model, indent=2))
    temp.replace(args.output)
    print(json.dumps({k: v for k, v in model.items() if k != 'coefficients'}, indent=2))


if __name__ == '__main__':
    main()
