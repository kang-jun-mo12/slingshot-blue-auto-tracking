import json
import tempfile
import unittest
from pathlib import Path

import joblib

from distance_learning import DistanceOffsetModel, collect_records


class LearningDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = json.loads(Path('calibration/flag-distance.json').read_text())
        self.current = self.root / 'current.json'
        self.current.write_text(json.dumps(self.data))
        self.history = self.root / 'history'
        session = self.history / 'session-01'
        session.mkdir(parents=True)
        self.events = session / 'events.jsonl'
        self.backups = self.root / 'backups'
        old = dict(self.data, version=1, pitch_trim_deg=0, yaw_trim_deg=0)
        path = self.backups / 'original/calibration/flag-distance.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(old))

    def collect(self, confirmed=True):
        return collect_records(self.current, self.history, self.backups, confirmed)

    def test_history_requires_user_confirmation_and_normalizes_old_pitch_trim(self):
        old = dict(kind='distance_estimate', box_size_px=[38.9, 60.9], distance_m=2,
                   pitch_offset_deg=14, calibration_version=1, yaw_offset_deg=-2)
        new = dict(old, pitch_offset_deg=13, calibration_version=self.data['version'], yaw_offset_deg=0)
        self.events.write_text('\n'.join(json.dumps(event) for event in (old, new)))
        records, audit = self.collect(False)
        self.assertEqual(len(records), 11)
        self.assertEqual(audit['history_events']['distance_estimate'], 2)
        records, audit = self.collect(True)
        self.assertEqual(len(records), 13)
        self.assertEqual([r['raw_pitch_offset_deg'] for r in records[-2:]], [14, 14])
        self.assertTrue(all(r['source'] == 'teacher_output_user_confirmed' for r in records[-2:]))
        self.assertEqual(audit['accepted_sources']['manual_measurement'], 11)
        self.assertTrue(all('yaw_offset_deg' not in r for r in records))

    def test_unknown_version_or_invalid_value_is_excluded_instead_of_guessed(self):
        event = dict(kind='distance_estimate', box_size_px=[46.4, 71.2], distance_m=1.75,
                     pitch_offset_deg=11, calibration_version=999)
        invalid = dict(event, calibration_version=1, box_size_px=[0, 71.2])
        self.events.write_text('\n'.join(json.dumps(r) for r in (event, invalid)))
        records, audit = self.collect()
        self.assertEqual(len(records), 11)
        self.assertEqual(len(audit['excluded']), 2)

    def test_real_model_roundtrip_preserves_prediction_and_external_trims(self):
        self.events.write_text('')
        records, _ = self.collect()
        model = DistanceOffsetModel().fit(records)
        path = self.root / 'model.joblib'
        joblib.dump(model, path)
        loaded = joblib.load(path)
        prediction = loaded.predict([[46.4, 71.2]], yaw_offset_deg=1.5, pitch_trim_deg=-1)[0]
        self.assertAlmostEqual(prediction['distance_m'], 1.75, delta=.02)
        self.assertAlmostEqual(prediction['raw_pitch_offset_deg'], 12, delta=.2)
        self.assertEqual(prediction['yaw_offset_deg'], 1.5)
        self.assertTrue(prediction['within_training_box_scale'])
        with self.assertRaises(ValueError):
            loaded.predict([[0, 71]])


if __name__ == '__main__':
    unittest.main()
