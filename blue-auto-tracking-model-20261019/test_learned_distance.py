import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from distance_calibration import DistanceCalibration, DistanceMeasurementError
from learned_distance import LearnedDistanceCalibration, MODEL_PATH
from test_flag_cycle import CycleTests


class LearnedDistanceTests(unittest.TestCase):
    def setUp(self):
        self.model = LearnedDistanceCalibration()

    def test_default_workflow_uses_learned_estimator_and_never_table_estimator(self):
        fixture = CycleTests()
        fixture.setUp()
        try:
            tracker, arduino, event, events = fixture.make_tracker()
            with patch.object(DistanceCalibration, '_estimate_size', side_effect=AssertionError('table estimate called')):
                fixture.run_tracker(tracker, event)
            self.assertEqual(tracker.state['phase'], 'completed')
            self.assertEqual(tracker.state['workflow_version'], 8)
            self.assertEqual(tracker.state['distance_engine'], 'gaussian_process')
            self.assertEqual(tracker.state['distance_measurement']['estimator'], 'gaussian_process')
            self.assertEqual(tracker.state['angles'], [98, 37, 106])
            self.assertEqual(events.count(('photo',)), 4)
            arduino.move_flag.assert_not_called()
        finally:
            fixture.doCleanups()

    def test_trims_apply_once_and_flag_angle_is_preserved(self):
        value = self.model.estimate_box([46.4, 71.2])
        self.assertAlmostEqual(value['distance_m'], 1.75, delta=.01)
        self.assertEqual(value['yaw_offset_deg'], -2)
        self.assertEqual(value['pitch_offset_deg'], 11)
        self.assertEqual(self.model.corrected_angles([100, 28, 95], value), [98, 28, 106])
        self.model.data['yaw_trim_deg'] = 2
        self.assertEqual(self.model.estimate_box([46.4, 71.2])['yaw_offset_deg'], 0)

    def test_out_of_domain_inputs_and_servo_limits_remain_rejected(self):
        for size in ([80, 120], [70, 47], [0, 71], [float('nan'), 71]):
            with self.subTest(size=size), self.assertRaises(DistanceMeasurementError):
                self.model.estimate_box(size)
        for row in (self.model.rows[0], self.model.rows[-1]):
            for factor in (.99, 1.01):
                value = self.model.estimate_box([row['width_px'] * factor, row['height_px'] * factor])
                self.assertGreaterEqual(value['distance_m'], 1.5)
                self.assertLessEqual(value['distance_m'], 2)
        with self.assertRaises(DistanceMeasurementError):
            self.model.corrected_angles([90, 28, 175], self.model.estimate_box([46.4, 71.2]))

    def test_missing_model_does_not_fall_back_to_table(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                LearnedDistanceCalibration(model_path=Path(directory) / 'absent.json')
            data = json.loads(MODEL_PATH.read_text())
            data['original_measurements_sha256'] = 'different'
            path = Path(directory) / 'mismatched.json'
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                LearnedDistanceCalibration(model_path=path)


if __name__ == '__main__':
    unittest.main()
