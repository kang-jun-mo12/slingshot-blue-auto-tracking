import json
import tempfile
import unittest
from pathlib import Path

from replay_distance_model import archived_images, difference_metrics


class ReplayTests(unittest.TestCase):
    def test_reused_photo_is_counted_once_and_conflicting_box_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'session-01'
            folder.mkdir()
            path = folder / 'events.jsonl'
            target = dict(box=[900, 500, 950, 570], confidence=.9,
                          class_id=1, class_name='ally')
            first = dict(kind='observation', observation=4, image='04.png', target=target)
            reuse = dict(first, kind='distance_observation')
            path.write_text('\n'.join(json.dumps(x) for x in (first, reuse)))
            entries, audit = archived_images(directory)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['event_kind'], 'observation')
            self.assertEqual(audit['reused_image_records'], 1)
            reuse['target'] = dict(target, box=[901, 500, 950, 570])
            path.write_text('\n'.join(json.dumps(x) for x in (first, reuse)))
            with self.assertRaisesRegex(ValueError, 'Conflicting saved boxes'):
                archived_images(directory)

    def test_comparison_measures_actual_rounded_commands(self):
        rows = [dict(session_id='one', baseline=dict(distance_m=1.75,
                       pitch_offset_deg=11, yaw_offset_deg=-2), baseline_raw_pitch_deg=12,
                     evaluation_model=dict(distance_m=1.751, pitch_offset_deg=11.1,
                       raw_pitch_offset_deg=12.06, yaw_offset_deg=-2))]
        metrics = difference_metrics(rows, 'evaluation_model')
        self.assertAlmostEqual(metrics['distance_mean_difference_cm'], .1)
        self.assertEqual(metrics['different_pitch_commands'], 1)
        self.assertEqual(metrics['within_0_1_deg'], 1)
        self.assertAlmostEqual(metrics['pitch_raw_mean_difference_deg'], .06)
        self.assertAlmostEqual(metrics['pitch_command_mean_difference_deg'], .1)


if __name__ == '__main__':
    unittest.main()
