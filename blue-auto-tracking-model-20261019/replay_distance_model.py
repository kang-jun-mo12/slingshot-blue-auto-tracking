"""Compare distance estimators on archived photos with their saved YOLO boxes."""
import argparse
import csv
import hashlib
import html
import json
import math
import os
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from PIL import Image

from distance_calibration import DistanceCalibration, DistanceMeasurementError
from distance_learning import DistanceOffsetModel, digest
from target_policy import is_centering_target


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def archived_images(history_root):
    """Join a selected target to its actual image; collapse reused image events."""
    entries = {}
    counts = Counter()
    for events in sorted(Path(history_root).glob('*/events.jsonl')):
        for number, line in enumerate(events.read_text().splitlines(), 1):
            try:
                event = json.loads(line)
            except ValueError:
                counts['invalid_json_lines'] += 1
                continue
            if event.get('kind') not in ('observation', 'distance_observation', 'return_verification'):
                continue
            counts['image_observation_records'] += 1
            if event.get('target') is None:
                counts['no_selected_target'] += 1
                continue
            name = event.get('image')
            if not name or Path(name).name != name:
                counts['invalid_image_name'] += 1
                continue
            image = events.parent / name
            key = str(image)
            if key in entries:
                # Version 7 logs reuse of a tracking image as another event.
                if entries[key]['target']['box'] != event['target']['box']:
                    raise ValueError(f'Conflicting saved boxes for image {key}')
                counts['reused_image_records'] += 1
                continue
            entries[key] = dict(image=key, session_id=events.parent.name,
                                event_kind=event['kind'], observation=event.get('observation'),
                                event_path=str(events), event_line=number,
                                source_angles=event.get('angles'), target=event['target'])
    return list(entries.values()), counts


def difference_metrics(rows, field):
    if not rows:
        return dict(images=0)
    values = [r[field] for r in rows]
    distance = np.asarray([abs(v['distance_m'] - r['baseline']['distance_m']) for r, v in zip(rows, values)])
    pitch = np.asarray([abs(v['pitch_offset_deg'] - r['baseline']['pitch_offset_deg']) for r, v in zip(rows, values)])
    raw_pitch = np.asarray([abs(v['raw_pitch_offset_deg'] - r['baseline_raw_pitch_deg']) for r, v in zip(rows, values)])
    different = np.count_nonzero(pitch > 1e-8)
    return dict(images=len(rows), sessions=len({r['session_id'] for r in rows}),
                distance_mean_difference_cm=float(distance.mean() * 100),
                distance_p95_difference_cm=float(np.percentile(distance, 95) * 100),
                distance_max_difference_cm=float(distance.max() * 100),
                pitch_command_mean_difference_deg=float(pitch.mean()),
                pitch_command_max_difference_deg=float(pitch.max()),
                pitch_raw_mean_difference_deg=float(raw_pitch.mean()),
                identical_pitch_commands=int(len(rows) - different),
                different_pitch_commands=int(different),
                within_0_1_deg=int(np.count_nonzero(pitch <= .10000001)),
                identical_yaw_commands=all(v['yaw_offset_deg'] == r['baseline']['yaw_offset_deg']
                                            for r, v in zip(rows, values)))


def compare(calibration_path, history_root, training_root, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    training_root = Path(training_root)
    report = json.loads((training_root / 'validation-report.json').read_text())
    records = json.loads((training_root / 'training-records.json').read_text())
    if digest(records) != report['data']['dataset_sha256']:
        raise ValueError('Training dataset digest disagrees with saved validation report')
    table = DistanceCalibration(calibration_path)
    if digest(table.rows) != report['data']['original_measurements_sha256']:
        raise ValueError('The measured calibration table changed since training')
    final = joblib.load(training_root / 'model.joblib')
    held_out = set(report['held_out_session_ids'])
    training_sessions = {r['group_id'] for r in records if r['source'] != 'manual_measurement'}
    development = [r for r in records if r['group_id'] not in held_out]
    # Recreate and save the evaluation model that never saw the held-out sessions.
    evaluation = DistanceOffsetModel(report['kernel_nu']).fit(development)
    joblib.dump(evaluation, output / 'evaluation-model.joblib')
    entries, audit = archived_images(history_root)
    results, excluded = [], []
    for entry in entries:
        path = Path(entry['image'])
        try:
            with Image.open(path) as image:
                if image.size != (1920, 1080) or image.format != 'PNG':
                    raise ValueError('unexpected_image_size_or_format')
                image.verify()
            target = entry['target']
            if not is_centering_target(target) or target['confidence'] < .6:
                raise ValueError('target_class_or_confidence')
            size = table.box_size(target)
            # Apply the existing module's domain checks equally before comparing.
            baseline = table.estimate_box(size)
        except (OSError, ValueError, KeyError) as exc:
            reason = str(exc)
            audit['excluded_unique_images'] += 1
            excluded.append(dict(image=str(path), reason=reason))
            continue
        entry.update(box_size_px=size, baseline=baseline,
                     baseline_raw_pitch_deg=(baseline['pitch_offset_deg'] - table.data.get('pitch_trim_deg', 0))
                     / table.data['pitch_direction'],
                     image_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                     session_in_final_training=entry['session_id'] in training_sessions,
                     held_out_session=entry['session_id'] in held_out)
        results.append(entry)
    sizes = [r['box_size_px'] for r in results]
    if not sizes:
        raise ValueError('No archived photos passed the common input checks')
    trims = dict(yaw_offset_deg=table.data['yaw_offset_deg'] + table.data.get('yaw_trim_deg', 0),
                 pitch_trim_deg=table.data.get('pitch_trim_deg', 0), pitch_direction=table.data['pitch_direction'])
    predictions = final.predict(sizes, **trims)
    evaluation_predictions = evaluation.predict(sizes, **trims)
    for entry, value, val_value in zip(results, predictions, evaluation_predictions):
        entry['final_model'] = value
        entry['evaluation_model'] = val_value
        angles = entry.get('source_angles')
        if angles is not None:
            try:
                entry['baseline_angles_from_image_pose'] = table.corrected_angles(angles, entry['baseline'])
                entry['model_angles_from_image_pose'] = table.corrected_angles(angles, value)
            except ValueError:
                # Distance agreement can still be checked if the hypothetical
                # offset at that photo's pose would exceed physical servo limits.
                entry['angle_application_out_of_range'] = True
    unseen = [r for r in results if r['held_out_session']]
    other_unseen = [r for r in results if not r['session_in_final_training']]
    summary = dict(method='offline_same_saved_yolo_box_for_both_estimators',
                   images_reinferred_with_yolo=False, motors_moved=False,
                   current_calibration_version=table.data['version'], current_trims=trims,
                   unique_linked_images=len(entries), archive_audit=dict(audit),
                   excluded_reasons=dict(Counter(e['reason'] for e in excluded)),
                   final_model_all_eligible_photos=difference_metrics(results, 'final_model'),
                   evaluation_model_held_out_session_photos=difference_metrics(unseen, 'evaluation_model'),
                   final_model_photos_from_other_unseen_sessions=difference_metrics(other_unseen, 'final_model'),
                   held_out_session_ids=sorted(held_out), evaluation_training_rows=len(development),
                   caveats=['All eligible photos are a mixture of training-session and other archived inputs.',
                            'The held-out session result uses a separately fitted model that never saw those sessions.',
                            'A photo may have multiple saved observations; each physical image file is counted once.',
                            'Images are verified and the saved selected YOLO box is replayed; YOLO is not rerun.',
                            'Differences compare estimators, not independent distance ground truth or physical servo behavior.',
                            'Angles from an image pose are hypothetical comparisons, not recorded post-return physical angles.'])
    write_json(output / 'summary.json', summary)
    write_json(output / 'per-image-results.json', results)
    write_json(output / 'excluded-images.json', excluded)
    columns = ['image', 'session_id', 'held_out', 'width_px', 'height_px', 'baseline_distance_m',
               'final_model_distance_m', 'evaluation_model_distance_m', 'baseline_pitch_deg',
               'final_model_pitch_deg', 'evaluation_model_pitch_deg']
    with (output / 'per-image-results.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for r in results:
            writer.writerow(dict(image=r['image'], session_id=r['session_id'], held_out=r['held_out_session'],
                                 width_px=r['box_size_px'][0], height_px=r['box_size_px'][1],
                                 baseline_distance_m=r['baseline']['distance_m'],
                                 final_model_distance_m=r['final_model']['distance_m'],
                                 evaluation_model_distance_m=r['evaluation_model']['distance_m'],
                                 baseline_pitch_deg=r['baseline']['pitch_offset_deg'],
                                 final_model_pitch_deg=r['final_model']['pitch_offset_deg'],
                                 evaluation_model_pitch_deg=r['evaluation_model']['pitch_offset_deg']))
    write_html(output, summary, results)
    print(json.dumps(summary, ensure_ascii=False))


def write_html(output, summary, rows):
    unseen = [r for r in rows if not r['session_in_final_training']]
    worst_distance = sorted(unseen, key=lambda r: abs(r['final_model']['distance_m'] - r['baseline']['distance_m']), reverse=True)[:3]
    worst_pitch = sorted(unseen, key=lambda r: abs(r['final_model']['pitch_offset_deg'] - r['baseline']['pitch_offset_deg']), reverse=True)[:3]
    selected = list({r['image']: r for r in worst_distance + worst_pitch}.values())
    cards = []
    for row in selected:
        baseline, prediction = row['baseline'], row['final_model']
        src = html.escape(os.path.relpath(Path(row['image']).resolve(), output.resolve()), quote=True)
        x1, y1, x2, y2 = row['target']['box']
        cards.append(f'''<article><h2>{html.escape(row['session_id'])} / {Path(row['image']).name}</h2>
<div class="photo"><img src="{src}" alt="저장된 추적 사진">
<svg viewBox="0 0 1920 1080"><rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" fill="none" stroke="#30d4ff" stroke-width="4"/></svg></div>
<p>박스 {row['box_size_px'][0]:.2f} × {row['box_size_px'][1]:.2f} px</p>
<table><tr><th></th><th>기존 계산</th><th>최종 학습 모델</th></tr>
<tr><th>거리</th><td>{baseline['distance_m']:.5f} m</td><td>{prediction['distance_m']:.5f} m</td></tr>
<tr><th>상하 보정</th><td>{baseline['pitch_offset_deg']:+.1f}°</td><td>{prediction['pitch_offset_deg']:+.1f}°</td></tr></table>
<p>거리 차이 {abs(prediction['distance_m']-baseline['distance_m'])*100:.3f} cm · 상하 명령 차이 {abs(prediction['pitch_offset_deg']-baseline['pitch_offset_deg']):.1f}°</p></article>''')
    metric = summary['final_model_photos_from_other_unseen_sessions']
    separate = summary['evaluation_model_held_out_session_photos']
    histogram = Counter(round(abs(r['final_model']['pitch_offset_deg'] - r['baseline']['pitch_offset_deg']), 1) for r in unseen)
    body = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>저장 사진으로 거리 모델 비교</title>
<style>body{{font:16px sans-serif;background:#101519;color:#e8eff5;max-width:1100px;margin:32px auto;padding:16px}}article{{padding:20px;border:1px solid #456;margin:24px 0}}h2{{font-size:18px}}.photo{{position:relative}}img{{display:block;width:100%}}svg{{position:absolute;inset:0;width:100%;height:100%}}table{{border-collapse:collapse}}td,th{{padding:10px;border:1px solid #456}}</style>
<h1>저장된 사진의 같은 박스로 비교</h1>
<p>각 사진에 저장된 YOLO 박스를 두 방식에 동일하게 입력했습니다. YOLO 재추론이나 서보 이동은 수행하지 않았습니다.</p>
<p>최종 모델 학습에 사용하지 않은 {metric['sessions']}개 세션의 사진 {metric['images']}장: 평균 거리 차이 {metric['distance_mean_difference_cm']:.3f} cm,
최대 {metric['distance_max_difference_cm']:.3f} cm. 상하 명령 평균 차이 {metric['pitch_command_mean_difference_deg']:.3f}°, 최대 {metric['pitch_command_max_difference_deg']:.1f}°.</p>
<p>상하 명령 동일 {histogram[0.0]}장 · 0.1° 차이 {histogram[0.1]}장 · 0.2° 차이 {histogram[0.2]}장. 좌우 보정은 두 방식 모두 −2°입니다.</p>
<p>추가로, 이전에 평가용으로 분리한 {separate['sessions']}개 세션의 {separate['images']}장은 해당 세션을 전혀 학습하지 않은 별도 평가 모델로 검증했습니다.
평균 거리 차이 {separate['distance_mean_difference_cm']:.3f} cm, 최대 상하 명령 차이 {separate['pitch_command_max_difference_deg']:.1f}°였습니다.</p>
<p>아래는 최종 모델 평가에서 거리 또는 상하 명령 차이가 큰 사진입니다. 이는 기존 계산과의 차이이며 실제 거리 오차는 별도로 측정해야 합니다.</p>
{''.join(cards)}</html>'''
    (output / 'comparison.html').write_text(body)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calibration', default='calibration/flag-distance.json')
    parser.add_argument('--history', default='tracking_sessions')
    parser.add_argument('--training', default='calibration/distance-learning-20261014')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    compare(args.calibration, args.history, args.training, args.output)
