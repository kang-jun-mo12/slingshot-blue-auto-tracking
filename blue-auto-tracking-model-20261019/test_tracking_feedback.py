import json
import math
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image
from calibration import PixelCalibration
from test_calibration import model_data, detection
from tracking_feedback import FeedbackPlanner
from step_tracker import StepTracker
from server import ArduinoConnection


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        path=self.root/'model.json';path.write_text(json.dumps(model_data()))
        self.model=PixelCalibration(path)

    def observation(self,number,angles,center):
        return dict(session_id='test',observation=number,time=time.time()-1+number*.001,
                    angles=angles,target=detection(*center))

    def test_observed_response_reduces_next_correction_error_and_persists(self):
        path=self.root/'feedback.jsonl';planner=FeedbackPlanner(self.model,path)
        before=self.observation(1,[90,37,100],[1080,540])
        # Actual response is 35 px/degree, instead of the original 20.
        after=self.observation(2,[86,37,100],[940,540])
        self.assertTrue(planner.observe(before,after))
        planner.geometry='detect'
        learned=planner.plan([940,540],[86,37,100])
        old_angle=86+(960-940)/20
        actual_old_error=abs(940+35*(old_angle-86)-960)
        actual_new_error=abs(940+35*(learned['angles'][0]-86)-960)
        self.assertLess(actual_new_error,actual_old_error/2)
        reloaded=FeedbackPlanner(self.model,path);reloaded.geometry='detect'
        self.assertEqual(len(reloaded.samples),1)
        self.assertEqual(reloaded.samples[0]['delta_pixels'],[-140,0])

    def test_unmeasured_pose_uses_small_move_and_full_servo_bounds(self):
        planner=FeedbackPlanner(self.model)
        result=planner.plan([1200,600],[160,37,60])
        self.assertEqual(result['reason'],'moved')
        self.assertLessEqual(abs(result['angles'][0]-160),3)
        self.assertLessEqual(abs(result['angles'][2]-60),3)
        self.assertLess(result['angles'][2],90)
        self.assertEqual(result['angles'][1],37)
        result=planner.plan([1500,540],[0,37,40])
        self.assertEqual(result['angles'][0],0)

    def test_unrelated_or_noisy_observations_do_not_train(self):
        a=self.observation(1,[90,37,100],[1080,540])
        for b in [self.observation(3,[86,37,100],[940,540]),
                  self.observation(2,[90,37,100],[940,540]),
                  self.observation(2,[86,37,100],[1200,540]),
                  self.observation(2,[86,0,100],[940,540])]:
            planner=FeedbackPlanner(self.model)
            self.assertFalse(planner.observe(a,b))
            self.assertEqual(planner.samples,[])
        b=self.observation(2,[86,37,100],[940,540]);b['target']['class_name']='enemy'
        self.assertFalse(FeedbackPlanner(self.model).observe(a,b))

    def test_new_geometry_does_not_reuse_old_detector_feedback(self):
        planner=FeedbackPlanner(self.model)
        a=self.observation(1,[90,37,100],[1080,540])
        b=self.observation(2,[86,37,100],[940,540])
        self.assertTrue(planner.observe(a,b))
        planner.geometry='obb'
        _,count=planner.jacobian([960,540],[86,100])
        self.assertEqual(count,0)

    def test_stalled_motion_is_retained_as_evidence(self):
        planner=FeedbackPlanner(self.model)
        a=self.observation(1,[90,37,100],[980,560])
        b=self.observation(2,[89.8,37,99.7],[980.2,560.1])
        self.assertTrue(planner.observe(a,b))
        self.assertEqual(len(planner.samples),1)
        # No response must not be interpreted as a near-zero Jacobian that
        # would cause the next command to explode.
        matrix,_=planner.jacobian(np.array([980,560]),np.array([89.8,99.7]))
        np.testing.assert_allclose(matrix,np.eye(2)*20)

    def test_large_moves_do_not_drown_out_fine_servo_response(self):
        planner=FeedbackPlanner(self.model)
        planner.geometry='detect';planner.session_id='test'
        for delta,motion,repeats in [(8,160,100),(.5,30,12)]:
            for _ in range(repeats):
                planner.samples.append(dict(time=time.time()-1,session_id='test',geometry='detect',pose=[90,100],
                    pixel=[960,540],delta_angles=[delta,0],delta_pixels=[motion,0]))
        coarse,_=planner.jacobian(np.array([960,540]),np.array([90,100]),step=np.array([8,0]))
        fine,_=planner.jacobian(np.array([960,540]),np.array([90,100]),step=np.array([.5,0]))
        self.assertLess(coarse[0,0],22)
        self.assertGreater(fine[0,0],coarse[0,0]*1.4)
        self.assertEqual(fine[1,1],20)

    def test_crossing_bracket_prevents_full_reverse_overshoot(self):
        planner=FeedbackPlanner(self.model)
        planner.observe(None,self.observation(1,[90,37,100],[945,540]))
        planner.observe(None,self.observation(2,[90.5,37,100],[980,540]))
        result=planner.plan([980,540],[90.5,37,100])
        self.assertGreater(result['angles'][0],90)
        self.assertLess(result['angles'][0],90.5)
        self.assertEqual(result['bracket_axes'],[0])

    def test_one_tenth_bracket_can_select_better_observed_endpoint(self):
        planner=FeedbackPlanner(self.model)
        planner.observe(None,self.observation(1,[90.2,37,100],[931,540]))
        planner.observe(None,self.observation(2,[90.3,37,100],[969,540]))
        destination,axes=planner.bracket(np.array([931,540]),np.array([90.2,100]),
                                      np.eye(2)*20,np.array([91,100]))
        self.assertAlmostEqual(destination[0],90.3)
        self.assertEqual(axes,[0])

    def test_stick_jump_statistics_do_not_treat_visible_error_as_centered(self):
        planner=FeedbackPlanner(self.model);planner.geometry='detect';planner.session_id='test'
        for motion in [0,0,0,30,32,29]:
            planner.samples.append(dict(time=time.time()-1,session_id='test',geometry='detect',pose=[90,100],
                pixel=[960,540],delta_angles=[.5,0],delta_pixels=[motion,0]))
        result=planner.plan([970,540],[90,37,100])
        self.assertEqual(result['reason'],'moved')
        self.assertLess(result['angles'][0],90)
        self.assertGreater(result['measured_resolution_px'][0],10)
        self.assertEqual(result['held_axes'],[])
        self.assertLessEqual(abs(result['angles'][0]-90),.3)
        self.assertEqual(planner.plan([1020,540],[90,37,100])['reason'],'moved')

    def test_another_run_cannot_supply_gain_or_dead_zone_at_identical_pose(self):
        path=self.root/'feedback.jsonl'
        rows=[dict(time=time.time()-1,session_id='old-location',geometry='detect',pose=[90,100],
                   pixel=[960,540],delta_angles=[.5,0],delta_pixels=[motion,0],
                   class_id=1,class_name='ally') for motion in [0,0,0,30,32,29]]
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        planner=FeedbackPlanner(self.model,path)
        current=self.observation(1,[90,37,100],[970,540])
        planner.observe(None,current)
        result=planner.plan(current['target']['center'],current['angles'])
        self.assertEqual(len(planner.samples),6)  # Audit history is preserved.
        self.assertEqual(result['feedback_samples'],0)
        self.assertEqual(result['axis_gains'],[1,1])
        self.assertEqual(result['measured_resolution_px'],[0,0])
        self.assertEqual(result['reason'],'moved')

    def test_first_fine_correction_probes_response_before_using_large_step(self):
        planner=FeedbackPlanner(self.model)
        current=self.observation(1,[98,37,96.7],[991,546])
        planner.observe(None,current)
        result=planner.plan(current['target']['center'],current['angles'])
        self.assertEqual(result['reason'],'moved')
        self.assertEqual(result['angles'],[97.7,37,96.4])
        self.assertEqual(result['unmeasured_fine_axes'],[0,1])

    def test_fine_probe_does_not_gain_extra_tenth_from_float_rounding(self):
        planner=FeedbackPlanner(self.model)
        for yaw in [87.6,89.7,97.9,98,98.1,99.6]:
            result=planner.plan([991,540],[yaw,37,100])
            self.assertAlmostEqual(yaw-result['angles'][0],.3)

    def test_real_same_angle_drift_does_not_reuse_old_positive_endpoint(self):
        # 20261009-213320: the same 98-degree command produced centers 43px
        # apart. Version 2 froze at 31px by using the first photo as an endpoint.
        planner=FeedbackPlanner(self.model)
        previous=None
        for number,(yaw,center) in enumerate([
                (98,[972.1,543.3]),(97.7,[929.1,546]),
                (97.9,[929.1,545.8]),(98,[929.3,546])],1):
            current=self.observation(number,[yaw,0,96.7],center)
            planner.observe(previous,current);previous=current
        result=planner.plan(center,current['angles'])
        self.assertEqual(result['reason'],'moved')
        self.assertGreater(result['angles'][0],98)
        self.assertLessEqual(result['angles'][0],98.3)
        self.assertEqual(result['angles'][1],0)
        self.assertNotIn(0,result['bracket_axes'])

    def test_real_stalls_do_not_collapse_bracket_onto_current_bad_pose(self):
        # 20261009-213202: reversals from 98.7 down to 98 did not move the
        # camera. The old narrow bracket selected 98 forever despite 21px error.
        planner=FeedbackPlanner(self.model)
        previous=None
        for number,(yaw,x) in enumerate([
                (97.9,933.2),(98.7,981.6),(98.3,981.3),(98.1,981.1),(98,980.9)],1):
            current=self.observation(number,[yaw,0,96.7],[x,540])
            planner.observe(previous,current);previous=current
        result=planner.plan([x,540],current['angles'])
        self.assertEqual(result['reason'],'moved')
        self.assertLess(result['angles'][0],98)
        self.assertNotIn(0,result['bracket_axes'])

    def test_newest_opposite_endpoint_replaces_stale_same_angle(self):
        planner=FeedbackPlanner(self.model)
        for number,(yaw,x) in enumerate([(90,940),(90,980),(90.5,985)],1):
            planner.observe(None,self.observation(number,[yaw,37,100],[x,540]))
        result=planner.plan([985,540],[90.5,37,100])
        self.assertEqual(result['bracket_axes'],[])
        self.assertEqual(result['reason'],'moved')

    def test_fresh_fine_jump_immediately_limits_reversal(self):
        planner=FeedbackPlanner(self.model)
        before=self.observation(1,[98,37,100],[972.1,540])
        after=self.observation(2,[97.7,37,100],[929.1,540])
        planner.observe(None,before);planner.observe(before,after)
        result=planner.plan([929.1,540],after['angles'])
        self.assertEqual(result['reason'],'moved')
        self.assertGreater(result['angles'][0],97.7)
        self.assertLessEqual(result['angles'][0],97.9)
        self.assertGreater(result['fine_response_gains'][0],7)

    def test_reused_planner_resets_brackets_and_training_between_runs(self):
        planner=FeedbackPlanner(self.model)
        a=self.observation(1,[90,37,100],[945,540])
        b=self.observation(2,[90.5,37,100],[980,540])
        planner.observe(None,a);planner.observe(a,b)
        c=self.observation(1,[90.5,37,100],[980,540]);c['session_id']='new-location'
        planner.observe(None,c)
        result=planner.plan([980,540],c['angles'])
        self.assertEqual(result['feedback_samples'],0)
        self.assertEqual(result['bracket_axes'],[])
        self.assertEqual(planner.current_samples,0)

    def test_full_run_learns_real_move_response_and_keeps_all_four_frames(self):
        self.simulated_run([90,37,100],[1080,540],35)

    def test_full_run_can_start_outside_old_measured_angles(self):
        self.simulated_run([160,37,60],[1080,540],20)

    def simulated_run(self,initial,initial_center,response):
        angles=list(initial);center=list(initial_center)
        arduino=Mock();arduino.ensure_ready.side_effect=lambda:list(angles)
        def move(yaw,pitch,expected_angles):
            self.assertEqual(expected_angles,angles)
            center[0]+=response*(yaw-angles[0]);center[1]+=20*(pitch-angles[2])
            angles[:]=[yaw,angles[1],pitch]
            return list(angles)
        arduino.move_camera_to.side_effect=move
        vision=SimpleNamespace(operation_lock=threading.Lock())
        tracker=StepTracker(vision,arduino,inference_url='http://desktop:8090',calibration=self.model,
                            capture=lambda:Image.new('RGB',(1920,1080)),history_root=self.root/'sessions')
        tracker.detector=SimpleNamespace(run_png=lambda _: [detection(*center)])
        event=Mock();event.is_set.return_value=False;event.wait.return_value=False
        tracker.lease_deadline=time.monotonic()+60;vision.operation_lock.acquire()
        tracker._run(event,None,False,False)
        self.assertEqual(tracker.state['phase'],'completed')
        self.assertEqual(tracker.state['observations'],4)
        self.assertLess(tracker.state['best_distance_px'],math.dist(initial_center,[960,540])*.1)
        self.assertGreater(tracker.state['feedback_updates'],0)
        folder=self.root/'sessions'/tracker.state['history_session']
        self.assertEqual(len(list(folder.glob('*.png'))),4)
        events=[json.loads(x) for x in (folder/'events.jsonl').read_text().splitlines()]
        self.assertEqual(sum(x['kind']=='observation' for x in events),4)
        self.assertEqual(events[-1]['kind'],'finished')
        self.assertEqual(tracker.state['angles'][1],37)

    def test_serial_controller_accepts_camera_poses_outside_old_range(self):
        arduino=ArduinoConnection();arduino.connection=Mock(is_open=True)
        arduino.last_angles=[90,37,100]
        arduino.connection.readline.return_value=b'OK 160.1,37,60.2\n'
        self.assertEqual(arduino.move_camera_to(160.1,60.2,[90,37,100]),[160.1,37,60.2])
        with self.assertRaises(ValueError):arduino.move_camera_to(-.1,60.2,[160.1,37,60.2])
