import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image

from calibration import PixelCalibration, coefficient_digest, qualify
from server import ArduinoConnection
from step_tracker import StepTracker, TOTAL_OBSERVATIONS


def model_data():
    coefficients = np.zeros((2, 15, 2))
    coefficients[0, 0, 0] = 20
    coefficients[1, 0, 1] = 20
    model = dict(version=1, image_size=[1920,1080], coefficients=coefficients.tolist(),
                 yaw_range=[30,150], pitch_range=[90,110], deadband=[12,12], center_radius_px=15,
                 metrics=[dict(pairs=40, validation_p95_px_per_degree=1,
                               validation_median_px_per_degree=.5)]*2, valid=True)
    checks=[]
    for axis in (0,2):
        for sign in (-1,1):
            end=[90,37,100];end[axis]+=sign*3
            checks.append(dict(matches=40,median_error_px=1,p95_error_px=2,
                               **{'from':[90,37,100], 'to':end}))
    model['current_validation']={'coefficient_digest':coefficient_digest(model),'measurements':checks}
    return model


def detection(x, y, kind='ally'):
    return {'center':[x,y],'box':[x-10,y-10,x+10,y+10],'confidence':.9,
            'class_name':kind,'class_id':0 if kind=='enemy' else 1}


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'model.json';path.write_text(json.dumps(model_data()))
        self.model=PixelCalibration(path)

    def test_pixel_to_full_angle_preserves_release(self):
        result=self.model.plan([1200,340],[90,37,100])
        self.assertEqual(result['angles'],[78,37,110])
        np.testing.assert_allclose(result['predicted_center'],[960,540],atol=.1)

    def test_limits_deadband_and_unmeasured_pose(self):
        result=self.model.plan([1700,540],[90,37,100])
        self.assertEqual(result['angles'],[53,37,100])
        self.assertEqual(self.model.plan([969,550],[90,37,100])['reason'],'centered')
        self.assertEqual(self.model.plan([980,550],[90,37,100])['reason'],'moved')
        with self.assertRaises(RuntimeError):self.model.plan([960,540],[90,37,80])
        self.assertEqual(self.model.plan([960,100],[90,37,110])['reason'],'limit')

    def test_tight_diagonal_boundary_and_fractional_resolution(self):
        self.assertTrue(self.model.is_centered([972,549]))
        self.assertFalse(self.model.is_centered([972,552]))
        self.assertFalse(self.model.is_centered([973,540]))
        # Fractional corrections reach positions skipped by integer commands.
        self.model.coefficients[0,0,0]=25
        result=self.model.plan([973,540],[90,37,100])
        self.assertEqual(result['angles'],[89.5,37,100])
        self.assertEqual(result['reason'],'moved')
        self.assertAlmostEqual(result['predicted_center'][0],960.5)
        # Integer neighbors would be worse; 0.4 degrees now improves the error.
        self.model.coefficients[0,0,0]=30
        result=self.model.plan([973,540],[90,37,100])
        self.assertEqual(result['reason'],'moved')
        self.assertEqual(result['angles'],[89.6,37,100])

    def test_fractional_pose_is_preserved_during_correction(self):
        result=self.model.plan([974,540],[90.1,37.2,100.3])
        self.assertEqual(result['angles'],[89.4,37.2,100.3])
        np.testing.assert_allclose(result['predicted_center'],[960,540],atol=.1)

    def test_changed_coefficients_or_incomplete_validation_are_rejected(self):
        model=model_data();model['coefficients'][0][0][0]=99
        self.assertFalse(qualify(model,model['current_validation']))
        model=model_data();model['current_validation']['measurements'].pop()
        self.assertFalse(qualify(model,model['current_validation']))

    def make_tracker(self, detections):
        vision=SimpleNamespace(operation_lock=threading.Lock())
        arduino=Mock();arduino.ensure_ready.return_value=[90,37,100]
        arduino.move_camera_to.side_effect=lambda yaw,pitch,expected_angles:[yaw,expected_angles[1],pitch]
        events=[]
        def capture():
            events.append('capture')
            return Image.new('RGB',(1920,1080))
        tracker=StepTracker(vision,arduino,inference_url='http://desktop:8090',calibration=self.model,capture=capture,
                            history_root=Path(self.temp.name)/'tracking',
                            planner_factory=lambda base,path: SimpleNamespace(
                                plan=base.plan,observe=lambda *args:False,samples=[]))
        def infer(data):
            with Image.open(io.BytesIO(data)) as image:self.assertEqual(image.size,(1920,1080))
            events.append('infer')
            return next(detections)
        tracker.detector=SimpleNamespace(run_png=infer)
        event=Mock();event.is_set.return_value=False;event.wait.side_effect=lambda seconds:events.append('settle') or False
        tracker.lease_deadline=time.monotonic()+60
        vision.operation_lock.acquire()
        return tracker,arduino,event,events

    def test_fresh_capture_after_absolute_move_and_settle_then_stop(self):
        tracker,arduino,event,events=self.make_tracker(iter([[detection(1560,440)]]+[[detection(960,540)]]*7))
        original=arduino.move_camera_to.side_effect
        arduino.move_camera_to.side_effect=lambda *a,**kw:events.append('move') or original(*a,**kw)
        tracker._run(event,None,True,True)
        self.assertEqual(events[:7],['settle','capture','infer','move','settle','capture','infer'])
        self.assertEqual(events.count('capture'),4)
        arduino.move_camera_to.assert_called_once_with(60,105,expected_angles=[90,37,100])
        self.assertEqual(tracker.status()['phase'],'completed')
        self.assertEqual(tracker.status()['moves'],1)
        self.assertFalse(tracker.vision.operation_lock.locked())
        arduino.jog.assert_not_called()

    def test_missing_or_changed_target_finishes_four_observations(self):
        for batches,phase in [([[]],'no_target'),
                              ([[detection(1200,440)],[detection(960,540,'enemy')]],'target_lost')]:
            tracker,arduino,event,_=self.make_tracker(iter(batches+[batches[-1]]*(4-len(batches))))
            tracker._run(event,None,False,False)
            self.assertEqual(tracker.status()['phase'],'no_target' if phase=='no_target' else 'completed')
            self.assertEqual(tracker.status()['observations'],4)
            self.assertLessEqual(arduino.move_camera_to.call_count,2)
            if phase == 'target_lost':
                self.assertEqual(tracker.status()['angles'],[90,37,100])
                self.assertTrue(tracker.status()['returned_to_best'])

    def test_pink_only_never_centers_and_high_confidence_pink_is_ignored(self):
        pink=detection(1200,440,'enemy');pink['confidence']=.99
        blue=detection(1300,440);blue['confidence']=.6
        self.assertIs(StepTracker.select([pink,blue],None,None),blue)
        self.assertIsNone(StepTracker.select([pink],None,None))
        self.assertIsNone(StepTracker.select([pink],blue,[1200,440]))
        tracker,arduino,event,_=self.make_tracker(iter([[pink]]*4))
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.status()['phase'],'no_target')
        self.assertEqual(tracker.status()['eligible_detections'],0)
        self.assertEqual(tracker.status()['target_class_id'],1)
        self.assertEqual(tracker.status()['target_class_name'],'ally')
        arduino.move_camera_to.assert_not_called()

    def test_missing_or_conflicting_class_metadata_is_not_eligible(self):
        for update in [{'class_id':0},{'class_name':'enemy'},{'class_id':None},{'class_id':False}]:
            target=detection(1300,440);target.update(update)
            self.assertIsNone(StepTracker.select([target],None,None))

    def test_legacy_centering_paths_also_exclude_pink(self):
        from aim import _select_target
        from live_tracker import select_and_plan
        pink=detection(960,540,'enemy');pink['confidence']=.99
        blue=detection(1400,440);blue['confidence']=.6
        self.assertIsNone(_select_target([pink],(1920,1080),None))
        self.assertEqual(select_and_plan([pink],image_size=(1920,1080)),(None,(0,0),None))
        self.assertIs(_select_target([pink,blue],(1920,1080),None),blue)
        self.assertIs(select_and_plan([pink,blue],image_size=(1920,1080))[0],blue)

    def test_stop_during_inference_prevents_move(self):
        tracker,arduino,event,_=self.make_tracker(iter([]))
        def infer(data):
            event.is_set.return_value=True
            return [detection(1200,440)]
        tracker.detector.run_png=infer
        tracker._run(event,None,False,False)
        arduino.move_camera_to.assert_not_called()
        self.assertEqual(tracker.status()['phase'],'stopped')

    def test_lost_browser_during_inference_prevents_move(self):
        tracker,arduino,event,_=self.make_tracker(iter([]))
        def infer(data):
            tracker.lease_deadline=time.monotonic()-1
            return [detection(1200,440)]
        tracker.detector.run_png=infer
        tracker._run(event,None,False,False)
        arduino.move_camera_to.assert_not_called()
        self.assertEqual(tracker.status()['phase'],'timeout')

    def test_repeated_corrections_keep_observing_after_centered(self):
        tracker,arduino,event,events=self.make_tracker(iter([
            [detection(1500,540)], [detection(1050,540)], [detection(1020,590)],
            [detection(960,540)]]+[[detection(960,540)]]*4))
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.status()['phase'],'completed')
        self.assertEqual(arduino.move_camera_to.call_count,3)
        self.assertEqual(tracker.status()['corrections'],2)
        self.assertEqual(events.count('capture'),4)
        self.assertTrue(all(call.kwargs['expected_angles'][1]==37
                            for call in arduino.move_camera_to.call_args_list))

    def test_already_centered_still_takes_four_fresh_observations(self):
        tracker,arduino,event,events=self.make_tracker(iter([[detection(960,540)]]*4))
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.status()['phase'],'completed')
        self.assertEqual(tracker.status()['observations'],4)
        self.assertEqual(events.count('capture'),4)
        arduino.move_camera_to.assert_not_called()
        self.assertEqual(tracker.status()['best_distance_px'],0)

    def test_limit_and_resolution_hold_pose_without_early_exit(self):
        for reason in ['limit','resolution_limit']:
            tracker,arduino,event,events=self.make_tracker(iter([[detection(980,540)]]*4))
            tracker.calibration=Mock()
            tracker.calibration.plan.return_value={
                'angles':[90,37,100], 'predicted_center':[980,540], 'reason':reason}
            tracker._run(event,None,False,False)
            self.assertEqual(tracker.status()['phase'],'completed')
            self.assertEqual(events.count('capture'),4)
            arduino.move_camera_to.assert_not_called()

    def test_large_error_increase_does_not_end_four_observations(self):
        tracker,arduino,event,events=self.make_tracker(iter(
            [[detection(1050,540)],[detection(1130,540)]]+[[detection(960,540)]]*6))
        tracker.calibration=Mock()
        tracker.calibration.plan.side_effect=lambda point,angles:{
            'angles':[angles[0]-.1,angles[1],angles[2]],
            'predicted_center':[960,540], 'reason':'moved'}
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.status()['phase'],'completed')
        self.assertEqual(events.count('capture'),4)
        self.assertEqual(tracker.status()['best_distance_px'],0)

    def test_temporary_target_loss_reacquires_without_early_exit(self):
        tracker,arduino,event,events=self.make_tracker(iter(
            [[detection(1050,540)],[],[detection(960,540)]]+[[detection(960,540)]]*5))
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.status()['phase'],'completed')
        self.assertEqual(events.count('capture'),4)
        self.assertEqual(tracker.status()['best_distance_px'],0)
        self.assertEqual(arduino.move_camera_to.call_count,1)

    def test_four_no_improvement_observations_return_to_best(self):
        tracker,arduino,event,events=self.make_tracker(iter(
            [[detection(1050,540)]]*TOTAL_OBSERVATIONS))
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.status()['phase'],'completed')
        self.assertEqual(tracker.status()['moves'],3)
        self.assertEqual(arduino.move_camera_to.call_count,4)
        self.assertEqual(events.count('capture'),4)
        self.assertEqual(tracker.status()['angles'],[90,37,100])
        self.assertEqual(tracker.status()['best_distance_px'],90)
        self.assertTrue(tracker.status()['returned_to_best'])
        last_correction=arduino.move_camera_to.call_args_list[-2]
        arduino.move_camera_to.assert_called_with(
            90,100,expected_angles=[last_correction.args[0],37,last_correction.args[1]])

    def test_exact_best_pose_includes_sub_two_pixel_improvement(self):
        batches=[[detection(x,540)] for x in [1050,1049]+[1051]*6]
        tracker,arduino,event,events=self.make_tracker(iter(batches))
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.status()['phase'],'completed')
        self.assertEqual(tracker.status()['best_distance_px'],89)
        first_move=arduino.move_camera_to.call_args_list[0]
        self.assertEqual(tracker.status()['angles'],[first_move.args[0],37,first_move.args[1]])
        self.assertEqual(tracker.status()['target']['center'],[1049,540])
        self.assertEqual(tracker.status()['processed_seq'],tracker.status()['frame_seq'])
        self.assertEqual(tracker.status()['best_frame_seq'],2)
        self.assertEqual(events.count('capture'),4)

    def test_stop_after_first_move_cancels_return_to_best(self):
        tracker,arduino,event,_=self.make_tracker(iter([[detection(1050,540)]]))
        event.wait.side_effect=[False,True]
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.status()['phase'],'stopped')
        self.assertFalse(tracker.status()['returned_to_best'])
        self.assertEqual(arduino.move_camera_to.call_count,1)

    def test_last_observed_position_is_not_replaced_by_an_older_best_photo(self):
        tracker,arduino,event,_=self.make_tracker(iter(
            [[detection(962,540)]]+[[detection(963,540)]]*7))
        tracker._run(event,None,False,False)
        arduino.move_camera_to.assert_not_called()
        self.assertEqual(tracker.state['best_distance_px'],2)
        self.assertEqual(tracker.state['final_distance_px'],3)
        self.assertTrue(tracker.state['return_verified'])
        self.assertEqual(tracker.state['target']['center'],[963,540])

    def test_correction_budget_returns_to_best_observed_pose(self):
        offsets=[90,89,88,94]
        tracker,arduino,event,events=self.make_tracker(iter(
            [[detection(960+x,540)] for x in offsets]))
        tracker.calibration=Mock()
        tracker.calibration.plan.side_effect=lambda point,angles: {
            'angles':[angles[0]-1,angles[1],angles[2]],
            'predicted_center':[960,540], 'reason':'moved'}
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.status()['phase'],'completed')
        self.assertEqual(tracker.status()['moves'],3)
        self.assertEqual(arduino.move_camera_to.call_count,4)
        self.assertEqual(tracker.status()['angles'],[88,37,100])
        self.assertEqual(tracker.status()['best_distance_px'],88)
        self.assertEqual(events.count('capture'),4)

    def test_fourth_photo_measures_last_correction_without_an_extra_move(self):
        tracker,arduino,event,events=self.make_tracker(iter([
            [detection(1050-3*i,540)] for i in range(TOTAL_OBSERVATIONS)]))
        # Isolate the repetition budget from angle limits and numerical planning.
        tracker.calibration=Mock()
        tracker.calibration.plan.side_effect=lambda point,angles: {
            'angles':[angles[0]-1,angles[1],angles[2]],
            'predicted_center':[960,540], 'reason':'moved'}
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.status()['phase'],'completed')
        self.assertEqual(arduino.move_camera_to.call_count,TOTAL_OBSERVATIONS-1)
        self.assertEqual(events.count('capture'),TOTAL_OBSERVATIONS)

    def test_later_correction_cannot_switch_to_pink(self):
        tracker,arduino,event,_=self.make_tracker(iter([
            [detection(1500,540)], [detection(1050,540)]]+[[detection(960,540,'enemy')]]*6))
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.status()['phase'],'completed')
        self.assertEqual(arduino.move_camera_to.call_count,3)

    def test_serial_one_command_preserves_release_checks_expected_and_bounds(self):
        connection=ArduinoConnection();connection.connection=Mock(is_open=True)
        connection.last_angles=[90,37,100]
        connection.connection.readline.return_value=b'OK 78,37,105\r\n'
        self.assertEqual(connection.move_camera_to(78,105,[90,37,100]),[78,37,105])
        connection.connection.write.assert_called_once_with(b'78,37,105\n')
        with self.assertRaises(RuntimeError):connection.move_camera_to(79,105,[90,37,100])
        with self.assertRaises(ValueError):connection.move_camera_to(79,180.1,[78,37,105])
        connection.connection.readline.return_value=b'OK 120,37,100\r\n'
        self.assertEqual(connection.move_camera_to(120,100,[78,37,105]),[120,37,100])
        self.assertEqual(connection.connection.write.call_count,2)


if __name__=='__main__':unittest.main()
