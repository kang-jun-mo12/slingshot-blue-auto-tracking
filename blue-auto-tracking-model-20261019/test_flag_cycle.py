import json
import math
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

from calibration import PixelCalibration
from distance_calibration import DistanceCalibration, DistanceMeasurementError
from flag_cycle import FlagCycleTracker
from remote_inference import RetryableInferenceError
from test_calibration import detection, model_data


def sized_target(width=46.4,height=71.2,x=960,y=540):
    target = detection(x,y)
    target['box'] = [x-width/2,y-height/2,x+width/2,y+height/2]
    return target


class DistanceTests(unittest.TestCase):
    def setUp(self):
        self.calibration = DistanceCalibration()

    def test_all_eleven_distances_keep_measurements_and_apply_additional_trims(self):
        for row in self.calibration.rows:
            with self.subTest(distance=row['distance_m']):
                result = self.calibration.estimate([[row['width_px'],row['height_px']]]*3)
                self.assertEqual(result['distance_m'],row['distance_m'])
                self.assertEqual(result['yaw_offset_deg'],-2)
                self.assertEqual(result['pitch_offset_deg'],row['pitch_offset_deg']-1)
                self.assertEqual(result['sample_count'],3)

    def test_single_box_reproduces_all_rows_without_fabricated_samples(self):
        for row in self.calibration.rows:
            estimate=self.calibration.estimate_box([row['width_px'],row['height_px']])
            self.assertEqual(estimate['distance_m'],row['distance_m'])
            self.assertEqual(estimate['yaw_offset_deg'],-2)
            self.assertEqual(estimate['pitch_offset_deg'],row['pitch_offset_deg']-1)
            self.assertEqual(estimate['sample_count'],1)
        for size in [None,[0,71],[math.nan,71],[80,120],[70,47]]:
            with self.assertRaises(DistanceMeasurementError):
                self.calibration.estimate_box(size)

    def test_between_measurements_interpolates_instead_of_rounding_to_row(self):
        a,b = self.calibration.rows[8:10]
        size = self.calibration.scales[8]*.6+self.calibration.scales[9]*.4
        width = a['width_px']*.6+b['width_px']*.4
        result = self.calibration.estimate([[width,size*size/width]]*3)
        self.assertEqual(result['distance_m'],1.92)
        self.assertEqual(result['pitch_offset_deg'],11.7)
        self.assertEqual(self.calibration.corrected_angles([100.1,37,95.2],result),[98.1,37,106.9])

    def test_two_consistent_samples_reject_outlier_and_use_median(self):
        result = self.calibration.estimate([[46.4,71.2],[80,100],[46.6,71.4]])
        self.assertEqual(result['used_samples'],[1,3])
        self.assertEqual(result['box_size_px'],[46.5,71.3])
        self.assertAlmostEqual(result['distance_m'],1.745,delta=.01)

    def test_missing_inconsistent_or_out_of_range_sizes_are_rejected(self):
        for sizes in [[None,None,[46.4,71.2]],[[55,83],[46,71],[39,61]],
                      [[70,106]]*3,[[30,47]]*3,[[70,47]]*3,
                      [[math.nan,70]]*3]:
            with self.subTest(sizes=sizes):
                with self.assertRaises(DistanceMeasurementError):
                    self.calibration.estimate(sizes)

    def test_small_endpoint_noise_is_reported_without_extrapolation(self):
        result = self.calibration.estimate([[38.9*.99,60.9*.99]]*3)
        self.assertEqual(result['distance_m'],2)
        self.assertEqual(result['pitch_offset_deg'],13)
        self.assertTrue(result['range_clamped'])

    def test_corrected_pose_must_fit_without_silent_clamping(self):
        estimate = self.calibration.estimate([[38.9,60.9]]*3)
        for angles in [[90,37,175],[180,37,180]]:
            with self.assertRaises(DistanceMeasurementError):
                self.calibration.corrected_angles(angles,estimate)

    def test_box_size_matches_ui_aabb_not_obb_edge_lengths(self):
        target = sized_target(40.5,64.7)
        target['obb_corners'] = [[0,0],[30,0],[30,60],[0,60]]
        width,height = self.calibration.box_size(target)
        self.assertAlmostEqual(width,40.5)
        self.assertAlmostEqual(height,64.7)


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        model = self.root/'model.json'; model.write_text(json.dumps(model_data()))
        self.model = PixelCalibration(model)

    def make_tracker(self,tracking=None,initial=None):
        batches = iter(tracking if tracking is not None else [[sized_target()]]*4)
        events = []; angles = list(initial or [100,37,95])
        arduino = Mock()
        arduino.ensure_ready.side_effect = lambda:list(angles)
        def require(expected):
            self.assertEqual(expected,angles)
            return list(angles)
        arduino.require_angles.side_effect = require
        def move(yaw,pitch,expected_angles):
            require(expected_angles)
            events.append(('camera',yaw,pitch))
            angles[:] = [yaw,angles[1],pitch]
            return list(angles)
        arduino.move_camera_to.side_effect = move
        def flag(return_to_zero,expected_angles,before_go):
            self.assertTrue(return_to_zero);require(expected_angles);before_go()
            events.append(('flag_go',));angles[1] = 180
            events.append(('flag_return',));angles[1] = 0
            return list(angles)
        arduino.move_flag.side_effect = flag
        def capture():
            events.append(('photo',))
            return Image.new('RGB',(1920,1080))
        tracker = FlagCycleTracker(SimpleNamespace(operation_lock=threading.Lock()),arduino,
            inference_url='http://desktop:8090',calibration=self.model,capture=capture,
            history_root=self.root/'tracking',planner_factory=lambda base,path:SimpleNamespace(
                plan=base.plan,observe=lambda *args:False,samples=[]))
        def infer(_):
            batch = next(batches)
            if isinstance(batch,Exception): raise batch
            return batch
        tracker.detector = SimpleNamespace(run_png=infer)
        event = Mock();event.is_set.return_value = False
        event.wait.side_effect = lambda seconds:events.append(('wait',seconds)) or False
        tracker.lease_deadline = time.monotonic()+60
        tracker.vision.operation_lock.acquire()
        return tracker,arduino,event,events

    def run_tracker(self,tracker,event):
        tracker._run(event,None,False,False)
        self.assertFalse(tracker.vision.operation_lock.locked())

    def test_tracking_stops_after_offsets_without_moving_flag(self):
        tracker,arduino,event,events = self.make_tracker()
        self.run_tracker(tracker,event)
        self.assertEqual(tracker.state['phase'],'completed')
        self.assertEqual(events.count(('photo',)),4)
        self.assertEqual(tracker.state['observations'],4)
        self.assertEqual(tracker.state['distance_observations'],1)
        self.assertEqual(tracker.state['distance_measurement']['distance_m'],1.75)
        self.assertEqual(tracker.state['alignment_angles'],[100,37,95])
        self.assertEqual(tracker.state['angles'],[98,37,106])
        self.assertFalse(tracker.state['flag_ready'])
        self.assertFalse(tracker.state['flag_completed'])
        self.assertEqual(tracker.state['preview_stage'],'before_offset')
        self.assertFalse(tracker.state['return_verified'])
        self.assertIsNone(tracker.state['final_distance_px'])
        self.assertEqual(events[-4:],[('camera',98,95),('wait',1.0),
                                    ('camera',98,106),('wait',3.0)])
        arduino.move_flag.assert_not_called()
        folder=self.root/'tracking'/tracker.state['history_session']
        self.assertEqual(len(list(folder.glob('*.png'))),4)
        records=[json.loads(x) for x in (folder/'events.jsonl').read_text().splitlines()]
        self.assertEqual(sum(r['kind']=='observation' for r in records),4)
        self.assertEqual(sum(r['kind']=='distance_observation' for r in records),1)
        self.assertEqual(records[-2]['kind'],'offset_complete')
        self.assertIsNone(records[-2]['next_action'])
        self.assertFalse(any(r['kind'].startswith('flag_') for r in records))
        self.assertEqual(records[-1]['kind'],'finished')
        self.assertFalse(records[-1]['flag_completed'])
        self.assertEqual(records[-1]['distance_measurement']['distance_m'],1.75)

    def test_return_to_best_before_offset_uses_last_box_instead_of_best_box(self):
        tracking=[[sized_target(54.7,83,x=980)], [sized_target(54,82)],
                  [sized_target(48.3,73.6,x=972)], [sized_target(38.9,60.9,x=975)]]
        tracker,arduino,event,events=self.make_tracker(tracking=tracking)
        tracker.calibration.plan=lambda point,angles:dict(angles=[round(angles[0]-.1,1),angles[1],angles[2]],
                                                        predicted_center=point,reason='moved')
        self.run_tracker(tracker,event)
        self.assertEqual(tracker.state['phase'],'completed')
        self.assertEqual(tracker.state['alignment_angles'],[99.9,37,95])
        self.assertEqual(tracker.state['angles'],[97.9,37,108])
        self.assertEqual(tracker.state['distance_source_observation'],4)
        self.assertEqual(tracker.state['distance_measurement']['distance_m'],2)
        self.assertEqual(tracker.state['distance_measurement']['sample_count'],1)
        self.assertEqual(events.count(('photo',)),4)
        last_photo=max(i for i,e in enumerate(events) if e==('photo',))
        self.assertEqual(events[last_photo+1:], [('camera',99.9,95),('wait',3.0),
                         ('camera',97.9,95),('wait',1.0),('camera',97.9,108),('wait',3.0)])
        self.assertIsNone(tracker.state['return_distance_px'])
        folder=self.root/'tracking'/tracker.state['history_session']
        records=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines()]
        measurement=next(r for r in records if r['kind']=='distance_observation')
        self.assertEqual(measurement['source_observation'],4)
        self.assertEqual(measurement['image'],'04.png')
        self.assertEqual(measurement['angles'],[99.7,37,95])
        self.assertTrue(measurement['reused_tracking_box'])
        arduino.move_flag.assert_not_called()

    def test_no_tracking_target_never_checks_distance_or_moves_flag(self):
        tracker,arduino,event,events=self.make_tracker(tracking=[[]]*4)
        self.run_tracker(tracker,event)
        self.assertEqual(tracker.state['phase'],'no_target')
        self.assertEqual(events.count(('photo',)),4)
        arduino.move_camera_to.assert_not_called();arduino.move_flag.assert_not_called()

    def test_invalid_last_box_prevents_offset_even_when_earlier_boxes_valid(self):
        for target in [sized_target(80,120),sized_target(70,47)]:
            tracker,arduino,event,events=self.make_tracker(tracking=[[sized_target()]]*3+[[target]])
            self.run_tracker(tracker,event)
            self.assertEqual(tracker.state['phase'],'distance_unreliable')
            self.assertTrue(tracker.state['error'])
            self.assertEqual(events.count(('photo',)),4)
            arduino.move_camera_to.assert_not_called();arduino.move_flag.assert_not_called()

    def test_last_detected_box_used_when_fourth_observation_has_no_target(self):
        tracker,arduino,event,events=self.make_tracker(tracking=[[sized_target()]]*3+[[]])
        self.run_tracker(tracker,event)
        self.assertEqual(tracker.state['phase'],'completed')
        self.assertEqual(tracker.state['distance_source_observation'],3)
        self.assertEqual(tracker.state['distance_measurement']['sample_count'],1)
        self.assertEqual(events.count(('photo',)),4)

    def test_stop_or_timeout_during_last_tracking_inference_prevents_offset_and_flag(self):
        for timeout in [False,True]:
            tracker,arduino,event,_=self.make_tracker()
            original=tracker.detector.run_png;calls=0
            def infer(png):
                nonlocal calls
                calls+=1
                if calls==4:
                    if timeout: tracker.lease_deadline=time.monotonic()-1
                    else: event.is_set.return_value=True
                return original(png)
            tracker.detector.run_png=infer
            self.run_tracker(tracker,event)
            self.assertEqual(tracker.state['phase'],'timeout' if timeout else 'stopped')
            arduino.move_camera_to.assert_not_called();arduino.move_flag.assert_not_called()

    def test_stop_during_offset_settling_prevents_flag(self):
        tracker,arduino,event,_=self.make_tracker()
        event.wait.side_effect=lambda seconds:tracker.state['offset_applied']
        self.run_tracker(tracker,event)
        self.assertEqual(tracker.state['phase'],'stopped')
        self.assertTrue(tracker.state['offset_applied'])
        arduino.move_flag.assert_not_called()

    def test_offset_outside_servo_range_is_rejected(self):
        tracker,arduino,event,_=self.make_tracker(initial=[100,37,175])
        tracker.calibration.plan=lambda point,angles:dict(angles=angles,predicted_center=point,reason='centered')
        self.run_tracker(tracker,event)
        self.assertEqual(tracker.state['phase'],'distance_unreliable')
        arduino.move_camera_to.assert_not_called();arduino.move_flag.assert_not_called()

    def test_retryable_tracking_failure_does_not_add_distance_photos(self):
        tracker,arduino,event,events=self.make_tracker(tracking=[[sized_target()]]*3+
            [RetryableInferenceError('temporary'),[sized_target()]])
        self.run_tracker(tracker,event)
        self.assertEqual(tracker.state['phase'],'completed')
        self.assertEqual(events.count(('photo',)),5)
        self.assertEqual(tracker.state['observations'],4)
        self.assertEqual(tracker.state['distance_observations'],1)

    def test_stop_or_timeout_between_offset_axes_prevents_pitch_and_flag(self):
        for timeout in (False,True):
            tracker,arduino,event,_=self.make_tracker()
            def wait(seconds):
                if tracker.state['phase']=='offset_yaw_wait':
                    self.assertEqual(seconds,1.0)
                    if timeout:
                        tracker.lease_deadline=time.monotonic()-1
                        return False
                    return True
                return False
            event.wait.side_effect=wait
            self.run_tracker(tracker,event)
            self.assertEqual(tracker.state['phase'],'timeout' if timeout else 'stopped')
            self.assertEqual(tracker.state['angles'],[98,37,95])
            self.assertTrue(tracker.state['yaw_offset_applied'])
            self.assertFalse(tracker.state['offset_applied'])
            arduino.move_camera_to.assert_called_once_with(98,95,expected_angles=[100,37,95])
            arduino.move_flag.assert_not_called()

    def test_failed_yaw_acknowledgement_prevents_pitch_and_flag(self):
        tracker,arduino,event,_=self.make_tracker()
        arduino.move_camera_to.side_effect=RuntimeError('yaw acknowledgement missing')
        self.run_tracker(tracker,event)
        self.assertEqual(tracker.state['phase'],'error')
        self.assertFalse(tracker.state['yaw_offset_applied'])
        self.assertFalse(tracker.state['offset_applied'])
        arduino.move_camera_to.assert_called_once_with(98,95,expected_angles=[100,37,95])
        arduino.move_flag.assert_not_called()

    def test_stop_during_single_return_prevents_distance_photos_and_flag(self):
        tracking=[[sized_target(x=x)] for x in [980,960,962,964]]
        tracker,arduino,event,events=self.make_tracker(tracking=tracking)
        tracker.calibration.plan=lambda point,angles:dict(angles=[angles[0]-.1,angles[1],angles[2]],
                                                        predicted_center=point,reason='moved')
        event.wait.side_effect=lambda _:tracker.state['phase']=='returning_to_best'
        self.run_tracker(tracker,event)
        self.assertEqual(tracker.state['phase'],'stopped')
        self.assertEqual(tracker.state['observations'],4)
        self.assertEqual(tracker.state['return_moves'],1)
        self.assertFalse(tracker.state['returned_to_best'])
        self.assertFalse(tracker.state['return_confirmed'])
        arduino.move_flag.assert_not_called()


if __name__ == '__main__':
    unittest.main()
