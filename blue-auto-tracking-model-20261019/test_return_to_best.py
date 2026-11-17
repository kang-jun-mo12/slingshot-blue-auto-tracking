import json
import tempfile
import unittest
from pathlib import Path

from calibration import PixelCalibration
from return_to_best import return_path
from test_calibration import detection, model_data


class ReturnPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        model = Path(self.tmp.name)/'model.json';model.write_text(json.dumps(model_data()))
        self.model = PixelCalibration(model)

    def best(self, records):
        observations = [dict(angles=[yaw,37,pitch],target=detection(x,y))
                        for yaw,pitch,x,y in records]
        last = observations[-1]
        return dict(angles=last['angles'],observation=dict(target=last['target']),
                    return_observations=observations)

    def test_real_stalled_reverse_replays_last_visible_approach(self):
        # 22:08: the best yaw 101.2 was reached after +1.2 then -0.3.
        # The small negative command did not reverse the physical camera.
        best = self.best([(97.2,111.4,878.6,860.9),(97.9,108.4,885,769.7),
                          (100.3,100.4,924.4,603.6),(101.5,98.2,969.3,561.1),
                          (101.2,97.5,967.8,539.8)])
        path,directions = return_path(self.model,best)
        self.assertEqual(directions,[1,-1])
        self.assertEqual(path,[[98.3,37,102.4],[100.3,37,100.4],
                               [101.5,37,98.2],[101.2,37,97.5]])

    def test_real_two_axis_stalls_preserve_original_final_small_commands(self):
        # 22:12: both camera axes stopped responding before the best command.
        best = self.best([(93.1,109.4,836.7,804.5),(96.6,101.4,918.7,630.8),
                          (98,98.1,917.4,576.1),(99.5,96.8,978.5,527.6),
                          (99.1,97.1,977.8,527.6)])
        path,directions = return_path(self.model,best)
        self.assertEqual(directions,[1,-1])
        self.assertEqual(path[-2:],[[99.5,37,96.8],[99.1,37,97.1]])
        self.assertEqual(path[0],[96,37,100.1])

    def test_unknown_initial_approach_uses_fresh_observed_displacement(self):
        best = self.best([(100,95,960,540)])
        path,directions = return_path(self.model,best,current_center=[980,520])
        self.assertEqual(directions,[-1,1])
        self.assertEqual(path,[[102,37,93],[100,37,95]])

    def test_waypoints_preserve_d9_and_physical_bounds(self):
        best = self.best([(0,180,960,540)])
        path,_ = return_path(self.model,best,current_center=[900,600],preload_degrees=4)
        self.assertTrue(all(p[1]==37 and all(0<=a<=180 for a in p) for p in path))
        self.assertEqual(path[-1],best['angles'])


if __name__ == '__main__':
    unittest.main()
