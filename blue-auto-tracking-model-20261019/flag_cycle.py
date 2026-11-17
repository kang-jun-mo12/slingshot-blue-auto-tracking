"""Four tracking observations, one return, offsets from the last detected box."""
import time

from distance_calibration import DistanceMeasurementError
from learned_distance import LearnedDistanceCalibration
from step_tracker import SETTLE_SECONDS, StepStopped, StepTracker

OFFSET_AXIS_WAIT_SECONDS = 1.0


class FlagCycleTracker(StepTracker):
    def __init__(self, *args, distance_calibration=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.distance_calibration = distance_calibration or LearnedDistanceCalibration()
        self.state.update(self._cycle_state())

    @staticmethod
    def _cycle_state():
        return dict(workflow='track_distance_offset', workflow_version=8, distance_engine='gaussian_process',
                    distance_observations=0, total_distance_observations=1, distance_source_observation=None,
                    distance_measurement=None, distance_error=None,
                    alignment_angles=None, alignment_distance_px=None,
                    offset_angles=None, offset_applied=False, yaw_offset_applied=False,
                    flag_started=False, flag_completed=False, flag_ready=False,
                    preview_stage='tracking',
                    return_attempt=0, return_moves=0, return_angles_applied=False, return_confirmed=False,
                    return_distance_px=None, return_error=None)

    def _run(self, event, angles, reverse_yaw, reverse_pitch):
        self._update(**self._cycle_state())
        super()._run(event, angles, reverse_yaw, reverse_pitch)

    def _restore_best(self, event, angles, best, session=None):
        self._check(event)
        destination = list(best['angles'])
        self._update(phase='returning_to_best',return_attempt=1,
                     returned_to_best=False,return_verified=False,return_confirmed=False,
                     return_distance_px=None,return_error=None,preview_stage='returning',
                     predicted_center=None)
        if session:
            session.write(dict(kind='return_path',attempt=1,time=time.time(),
                               before=list(angles),best_angles=best['angles'],
                               best_distance_px=best['distance'],waypoints=[destination]))
        self.arduino.require_angles(angles)
        if angles != destination:
            updated = self.arduino.move_camera_to(destination[0],destination[2],expected_angles=angles)
            if updated != destination:
                raise RuntimeError('최소 오차 위치 복귀 명령과 서보 응답이 다릅니다')
            travel = max(abs(updated[i]-angles[i]) for i in (0,2))
            self._update(angles=list(updated),target=None,return_moves=self.state['return_moves']+1)
            if session:
                session.write(dict(kind='return_move',attempt=1,time=time.time(),
                                   before=list(angles),angles=list(updated)))
            angles = list(updated)
            if event.wait(max(SETTLE_SECONDS,travel*.08)):
                raise StepStopped('stopped')
        self._check(event)
        self._update(angles=destination,returned_to_best=True,return_angles_applied=True,
                     final_distance_px=None)
        # The preview remains a tracking photo, never evidence of the restored pose.

    def _after_tracking(self, event, best, detector, session):
        self._check(event)
        angles = list(self.state['angles'])
        self.arduino.require_angles(angles)
        self._update(alignment_angles=angles,phase='measuring_distance')
        # Keep the last valid selected target from this run, independent of which
        # observation had the smallest center error and of state cleared on return.
        source = next((item for item in reversed(best['tracking_observations'])
                       if item['target'] is not None), None)
        try:
            if source is None or source['target']['confidence'] < .6:
                raise DistanceMeasurementError('마지막 추적 박스의 신뢰도가 부족합니다.')
            size = self.distance_calibration.box_size(source['target'])
            self._update(distance_observations=1,
                         distance_source_observation=source['observation'])
            session.write(dict(kind='distance_observation',time=time.time(),observation=1,
                               source_observation=source['observation'],image=source['image'],
                               angles=source['angles'],target=source['target'],box_size_px=size,
                               reused_tracking_box=True))
            estimate = self.distance_calibration.estimate_box(size)
            estimate['source_observation'] = source['observation']
            estimate['source_image'] = source['image']
            corrected = self.distance_calibration.corrected_angles(angles,estimate)
        except DistanceMeasurementError as exc:
            self._update(distance_error=str(exc),error=str(exc))
            session.write(dict(kind='distance_rejected',time=time.time(),reason=str(exc)))
            return 'distance_unreliable'
        self._update(distance_measurement=estimate,offset_angles=corrected,phase='applying_yaw_offset')
        session.write(dict(kind='distance_estimate',time=time.time(),**estimate))
        self._check(event)
        yaw_only = [corrected[0],angles[1],angles[2]]
        updated = self.arduino.move_camera_to(yaw_only[0],yaw_only[2],expected_angles=angles)
        if updated != yaw_only:
            raise RuntimeError('좌우 거리 보정 각도와 서보 적용 결과가 다릅니다')
        self._update(angles=list(updated),yaw_offset_applied=True,preview_stage='before_offset',
                     phase='offset_yaw_wait',return_verified=False,final_distance_px=None)
        session.write(dict(kind='distance_offset_axis',axis='yaw',time=time.time(),
                           before=angles,angles=list(updated)))
        # Start the one-second gap after the yaw command is acknowledged.
        # A stop or expired browser lease during this gap prevents pitch.
        if event.wait(OFFSET_AXIS_WAIT_SECONDS):
            raise StepStopped('stopped')
        self._check(event)
        self._update(phase='applying_pitch_offset')
        updated = self.arduino.move_camera_to(corrected[0],corrected[2],expected_angles=yaw_only)
        if updated != corrected:
            raise RuntimeError('상하 거리 보정 각도와 서보 적용 결과가 다릅니다')
        self._update(angles=list(updated),offset_applied=True,preview_stage='before_offset',
                     phase='offset_settling',return_verified=False,final_distance_px=None)
        session.write(dict(kind='distance_offset_axis',axis='pitch',time=time.time(),
                           before=yaw_only,angles=list(updated)))
        session.write(dict(kind='distance_offset',time=time.time(),before=angles,angles=updated))
        if event.wait(max(SETTLE_SECONDS,abs(updated[2]-yaw_only[2])*.08)):
            raise StepStopped('stopped')
        self._check(event)
        self.arduino.require_angles(updated)
        self._check(event)
        session.write(dict(kind='offset_complete',time=time.time(),angles=list(updated),
                           next_action=None))
        return 'completed'
