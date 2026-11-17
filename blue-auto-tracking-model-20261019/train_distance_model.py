"""Train and assess an offline candidate without touching camera/servo services."""
import argparse
import csv
import json
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.model_selection import GroupKFold

from distance_calibration import DistanceCalibration
from distance_learning import DistanceOffsetModel, collect_records, error_metrics


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def train(calibration_path, history_root, backups_root, output, use_confirmed_history):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    records, audit = collect_records(calibration_path, history_root, backups_root, use_confirmed_history)
    write_json(output / 'data-audit.json', audit)
    write_json(output / 'training-records.json', records)
    columns = ['sample_id', 'group_id', 'source', 'width_px', 'height_px',
               'distance_m', 'raw_pitch_offset_deg']
    with (output / 'training-records.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)
    manual = [r for r in records if r['source'] == 'manual_measurement']
    history = [r for r in records if r['source'] != 'manual_measurement']
    # Keep the most recent sessions completely out of model/parameter selection.
    groups = sorted({r['group_id'] for r in history})
    test_groups = set(groups[-max(1, len(groups) // 5):]) if groups else set()
    development = manual + [r for r in history if r['group_id'] not in test_groups]
    test = [r for r in history if r['group_id'] in test_groups]
    development_groups = [r['group_id'] for r in development]
    folds = min(3, len(set(development_groups)))
    candidates = []
    for nu in (1.5, 2.5):
        errors = []
        if folds >= 2:
            for train_ids, val_ids in GroupKFold(folds).split(development, groups=development_groups):
                subset = [development[i] for i in train_ids]
                validation = [development[i] for i in val_ids]
                model = DistanceOffsetModel(nu).fit(subset)
                predictions = model.predict([[r['width_px'], r['height_px']] for r in validation])
                errors.append(error_metrics(validation, predictions))
        else:
            # With only the manual table, omit one actual row per fold.
            for i in range(len(development)):
                model = DistanceOffsetModel(nu).fit(development[:i] + development[i + 1:])
                row = development[i]
                errors.append(error_metrics([row], model.predict([[row['width_px'], row['height_px']]])))
        score = float(np.mean([e['distance_mae_m'] / .01 + e['pitch_mae_deg'] / .1 for e in errors]))
        candidates.append(dict(nu=nu, validation_folds=errors, selection_score=score))
    nu = min(candidates, key=lambda item: item['selection_score'])['nu']
    validation_model = DistanceOffsetModel(nu).fit(development)
    test_predictions = validation_model.predict([[r['width_px'], r['height_px']] for r in test]) if test else []
    held_out = error_metrics(test, test_predictions) if test else None
    table = DistanceCalibration(calibration_path)
    baseline_predictions = []
    baseline_rows = []
    for row in test:
        try:
            value = table.estimate_box([row['width_px'], row['height_px']])
        except ValueError:
            continue
        baseline_rows.append(row)
        baseline_predictions.append(dict(distance_m=value['distance_m'],
                                         raw_pitch_offset_deg=(value['pitch_offset_deg'] - table.data.get('pitch_trim_deg', 0))
                                         / table.data['pitch_direction']))
    final = DistanceOffsetModel(nu).fit(records)
    joblib.dump(final, output / 'model.joblib')
    fitted_predictions = final.predict([[r['width_px'], r['height_px']] for r in records])
    report = dict(status='offline_candidate_not_deployed', model_type='two_gaussian_process_regressors',
                  kernel_nu=nu, sklearn_version=sklearn.__version__, data=audit,
                  hyperparameter_selection=candidates,
                  evaluation_model_trained_rows=len(development),
                  held_out_session_ids=sorted(test_groups), held_out_history=held_out,
                  baseline_table_on_held_out=error_metrics(baseline_rows, baseline_predictions) if baseline_rows else None,
                  final_model_trained_rows=len(records), training_fit=error_metrics(records, fitted_predictions),
                  learned_targets=['distance_m', 'raw_pitch_offset_deg'],
                  yaw_is_configuration=True, current_yaw_offset_deg=table.data['yaw_offset_deg'] + table.data.get('yaw_trim_deg', 0),
                  current_pitch_trim_deg=table.data.get('pitch_trim_deg', 0),
                  learned_kernels=dict(distance=str(final.distance_gp.kernel_), pitch=str(final.pitch_gp.kernel_)),
                  fit_warnings=final.fit_warnings,
                  caveats=['Held-out teacher-label error measures reproduction of the current estimator, not independent physical accuracy.',
                           'All manual rows came from one supplied table; repeated sessions are not new independent range measurements.',
                           'Posterior standard deviations are model estimates, not validated physical error bounds.',
                           'Do not extrapolate outside 1.50-2.00 m; live integration and real-world validation remain pending.'])
    write_json(output / 'validation-report.json', report)
    write_json(output / 'held-out-predictions.json', [dict(record=r, prediction=p) for r, p in zip(test, test_predictions)])
    print(json.dumps(dict(output=str(output), sources=audit['accepted_sources'],
                          total_records=len(records), held_out=held_out,
                          status=report['status']), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calibration', default='calibration/flag-distance.json')
    parser.add_argument('--history', default='tracking_sessions')
    parser.add_argument('--backups', default='backups')
    parser.add_argument('--output', required=True)
    parser.add_argument('--user-confirmed-history', action='store_true',
                        help='Include teacher outputs only after explicit user confirmation')
    args = parser.parse_args()
    train(args.calibration, args.history, args.backups, args.output, args.user_confirmed_history)
