"""Capture -> detect once -> calibrated absolute move -> settle -> recapture."""
import io
import math
import time
from pathlib import Path

from calibration import PixelCalibration
from live_tracker import LiveTracker
from remote_inference import RemoteDetector, RetryableInferenceError
from vision import Detector, capture_photo
from target_policy import TARGET_CLASS_ID, TARGET_CLASS_NAME, centering_targets, is_centering_target
from servo_angles import checked_angles
from tracking_feedback import FeedbackPlanner, TrackingSession, HISTORY_ROOT

TOTAL_OBSERVATIONS = 4
# Live photos showed 18px of additional motion between the first exposure
# (~2s after a command) and the next unchanged-pose exposure. Wait for the
# loaded mount to settle before fitting a motion response.
SETTLE_SECONDS = 3.0


class StepStopped(Exception):
    pass


class StepTracker(LiveTracker):
    def __init__(self, vision, arduino, inference_url=None, calibration=None, capture=capture_photo,
                 history_root=HISTORY_ROOT, planner_factory=FeedbackPlanner):
        super().__init__(vision, arduino, inference_url=inference_url)
        self.image_size = (1920, 1080)
        self.calibration = calibration or PixelCalibration()
        self.capture = capture
        self.history_root = Path(history_root)
        self.planner_factory = planner_factory
        self.state.update(mode='calibrated_steps', moves=0, predicted_center=None,
                          target_class_id=TARGET_CLASS_ID, target_class_name=TARGET_CLASS_NAME, eligible_detections=0,
                          corrections=0, max_corrections=TOTAL_OBSERVATIONS-2,
                          observations=0, total_observations=TOTAL_OBSERVATIONS,
                          last_observation_reason=None,
                          best_angles=None, best_distance_px=None, best_frame_seq=None,
                          returned_to_best=False, history_session=None,
                          feedback_samples=0, feedback_updates=0,
                          feedback_version=3, feedback_scope='current_session', measured_resolution_px=None,
                          final_distance_px=None, return_verified=False)

    def start(self, reverse_yaw=False, reverse_pitch=False):
        # Mounting direction is measured by calibration. Manual WASD reversal
        # must not reverse these measured servo coordinates a second time.
        self.calibration.require_valid()
        return super().start(reverse_yaw, reverse_pitch)

    def frame_after(self, after):
        # Step captures are always PNG, even with the legacy local detector.
        with self.condition:
            if self.frame is None or self.frame_seq <= after:
                return None
            sequence, frame = self.frame_seq, self.frame
        from PIL import Image
        with self.preview_lock:
            if self.preview_seq != sequence:
                with Image.open(io.BytesIO(frame)) as source:
                    preview = source.convert('RGB')
                    preview.thumbnail((960, 540))
                buffer = io.BytesIO()
                preview.save(buffer, format='JPEG', quality=75)
                self.preview_seq, self.preview = sequence, buffer.getvalue()
            return sequence, self.preview

    def _check(self, event):
        if event.is_set():
            raise StepStopped('stopped')
        with self.condition:
            if time.monotonic() > self.lease_deadline:
                raise StepStopped('timeout')

    def _update(self, **values):
        with self.condition:
            self.state.update(values)

    def _restore_best(self, event, angles, best, session=None):
        self._check(event)
        moved = angles != best['angles']
        latest_target = self.state.get('target')
        if not moved and latest_target is not None:
            # The last actual exposure is the final position. Keep it visible;
            # the best historical distance is reported separately.
            self._update(returned_to_best=True, return_verified=True,
                         final_distance_px=round(math.dist(latest_target['center'],[960,540]),2),
                         predicted_center=None)
            return
        if angles != best['angles']:
            self._update(phase='returning_to_best')
            updated = self.arduino.move_camera_to(
                best['angles'][0], best['angles'][2], expected_angles=angles)
            if updated != best['angles']:
                raise RuntimeError('최소 오차 위치의 서보 각도와 적용 결과가 다릅니다')
            self._update(angles=list(updated))
            travel = max(abs(updated[0]-angles[0]), abs(updated[2]-angles[2]))
            if event.wait(max(SETTLE_SECONDS, travel * .08)):
                raise StepStopped('stopped')
        self._check(event)
        # Display the actual observation recorded at the restored pose.
        # Publish it as a new frame so browser previews and boxes stay in sync.
        with self.condition:
            self.frame = best['png']
            self.frame_seq += 1
            self.state.update(best['observation'], angles=list(best['angles']),
                              processed_seq=self.frame_seq, predicted_center=None,
                              returned_to_best=True, return_verified=False,
                              final_distance_px=None)

    @staticmethod
    def select(detections, previous, predicted):
        detections = centering_targets(detections)
        if previous is None:
            return max(detections, key=lambda d: d['confidence']) if detections else None
        if not is_centering_target(previous):
            return None
        candidates = [d for d in detections if d.get('class_name') == previous.get('class_name')]
        candidates.sort(key=lambda d: math.dist(d['center'], predicted))
        if not candidates or math.dist(candidates[0]['center'], predicted) > 180:
            return None
        # An ambiguous pair must not silently switch to a different object.
        if len(candidates) > 1 and abs(math.dist(candidates[0]['center'], predicted) -
                                     math.dist(candidates[1]['center'], predicted)) < 35:
            return None
        return candidates[0]

    def _after_tracking(self, event, best, detector, session):
        """Optional continuation after the fixed tracking observation budget."""
        return 'completed'

    def _run(self, stop_event, angles, reverse_yaw, reverse_pitch):
        phase, error = 'stopped', None
        session = None
        try:
            self._check(stop_event)
            angles = self.arduino.ensure_ready()
            self._update(angles=list(angles), moves=0, corrections=0, observations=0,
                         last_observation_reason=None,
                         predicted_center=None, eligible_detections=0,
                         best_angles=None, best_distance_px=None, best_frame_seq=None,
                         returned_to_best=False, history_error=None, history_session=None,
                         feedback_samples=0, feedback_updates=0, feedback_pairs_used=0,
                         measured_resolution_px=None, final_distance_px=None, return_verified=False)
            # Every physical servo angle can begin observation. The planner
            # handles measured and unmeasured poses using fresh feedback.
            angles = checked_angles(angles)
            session = TrackingSession(self.history_root)
            planner = self.planner_factory(self.calibration, self.history_root / 'feedback-v2.jsonl')
            self._update(history_session=session.id, feedback_samples=len(planner.samples), feedback_updates=0)
            detector = self.detector
            if detector is None:
                detector = RemoteDetector(self.inference_url) if self.inference_url is not None else Detector(self.model_path)
                self.detector = detector
            if stop_event.wait(SETTLE_SECONDS):
                raise StepStopped('stopped')
            previous = predicted = None
            moves, retries, observations = 0, 0, 0
            best = None
            previous_observation = None
            run_observations = []
            while observations < TOTAL_OBSERVATIONS:
                self._check(stop_event)
                self.arduino.require_angles(angles)
                self._update(phase='capturing', target=None, error=None)
                # No background camera reader. This exposure happens only after
                # the preceding motion and mechanical settling have completed.
                image = self.capture()
                if image.size != (1920, 1080):
                    raise RuntimeError('1920×1080 사진이 필요합니다')
                self._check(stop_event)
                buffer = io.BytesIO()
                image.save(buffer, format='PNG', compress_level=1)
                png = buffer.getvalue()
                with self.condition:
                    self.frame, self.frame_time = png, time.monotonic()
                    self.frame_seq += 1
                    sequence = self.frame_seq
                    self.state['phase'] = 'detecting'
                started = time.monotonic()
                try:
                    detections = detector.run_png(png) if self.inference_url is not None else detector.run(image)
                except RetryableInferenceError as exc:
                    retries += 1
                    self._update(phase='reconnecting', retry_count=retries, error=str(exc))
                    if retries >= 3:
                        raise
                    stop_event.wait(1)
                    continue
                self._check(stop_event)
                retries = 0
                self.arduino.require_angles(angles)
                target = self.select(detections, previous, predicted)
                observations += 1
                self._update(target=target, detections=len(detections), retry_count=0,
                             observations=observations,
                             eligible_detections=len(centering_targets(detections)),
                             inference_ms=round((time.monotonic()-started)*1000),
                             processed_seq=sequence, error=None)
                observation = session.observation(observations, png, angles, target, detections)
                run_observations.append(observation)
                learned = planner.observe(previous_observation, observation)
                previous_observation = observation
                self._update(feedback_samples=len(planner.samples),
                             feedback_updates=self.state['feedback_updates'] + int(learned))
                if target is None:
                    self._update(error_px=None, predicted_center=predicted,
                                 last_observation_reason='target_lost' if previous else 'no_target',
                                 phase='observing')
                    if observations < TOTAL_OBSERVATIONS and stop_event.wait(.5):
                        raise StepStopped('stopped')
                    continue
                error_px = [round(target['center'][0]-960, 1), round(target['center'][1]-540, 1)]
                distance = math.hypot(*error_px)
                self._update(error_px=error_px)
                # Keep the exact minimum across all measured poses.
                if best is None or distance < best['distance']:
                    best = dict(distance=distance, angles=list(angles), png=png,
                                return_observations=list(run_observations),
                                observation=dict(target=target, error_px=error_px,
                                                 detections=len(detections),
                                                 eligible_detections=len(centering_targets(detections)),
                                                 inference_ms=self.state['inference_ms']))
                    self._update(best_angles=list(angles), best_distance_px=round(distance, 2),
                                 best_frame_seq=sequence)
                if observations == TOTAL_OBSERVATIONS:
                    self._update(last_observation_reason='final_observation')
                    break
                plan = planner.plan(target['center'], angles)
                session.write(dict(kind='plan', observation=observations, **plan))
                self._update(last_observation_reason=plan['reason'],
                             feedback_pairs_used=plan.get('feedback_samples', 0),
                             measured_resolution_px=plan.get('measured_resolution_px'))
                if plan['reason'] != 'moved':
                    # A centered or immovable pose still gets the remaining
                    # fresh observations; never manufacture an extra movement.
                    previous, predicted = target, list(target['center'])
                    self._update(phase='observing', predicted_center=predicted)
                    if stop_event.wait(.5):
                        raise StepStopped('stopped')
                    continue
                self._check(stop_event)
                self._update(phase='moving', predicted_center=plan['predicted_center'])
                updated = self.arduino.move_camera_to(plan['angles'][0], plan['angles'][2], expected_angles=angles)
                if updated != plan['angles']:
                    raise RuntimeError('계산된 서보 각도와 적용 결과가 다릅니다')
                travel = max(abs(updated[0]-angles[0]), abs(updated[2]-angles[2]))
                angles = updated
                moves += 1
                previous, predicted = target, plan['predicted_center']
                self._update(phase='settling', angles=list(angles), moves=moves,
                             corrections=max(0, moves-1))
                if stop_event.wait(max(SETTLE_SECONDS, travel * .08)):
                    raise StepStopped('stopped')
            if best is not None:
                best['tracking_observations'] = list(run_observations)
                self._restore_best(stop_event, angles, best, session=session)
                phase = self._after_tracking(stop_event, best, detector, session)
                error = self.state.get('error')
            else:
                phase = 'no_target'
        except StepStopped as exc:
            phase = str(exc)
        except Exception as exc:
            phase, error = 'error', str(exc)
        finally:
            stop_event.set()
            self._update(active=False, phase=phase, error=error)
            if session is not None:
                try:
                    session.write(dict(kind='finished', phase=phase, error=error,
                                       angles=self.state['angles'], observations=self.state['observations'],
                                       best_angles=self.state['best_angles'],
                                       best_distance_px=self.state['best_distance_px'],
                                       return_verified=self.state['return_verified'],
                                       final_distance_px=self.state['final_distance_px'],
                                       workflow=self.state.get('workflow'),
                                       return_attempt=self.state.get('return_attempt'),
                                       return_angles_applied=self.state.get('return_angles_applied'),
                                       return_confirmed=self.state.get('return_confirmed'),
                                       return_distance_px=self.state.get('return_distance_px'),
                                       distance_measurement=self.state.get('distance_measurement'),
                                       offset_angles=self.state.get('offset_angles'),
                                       flag_started=self.state.get('flag_started',False),
                                       flag_completed=self.state.get('flag_completed',False)))
                except OSError as exc:
                    self._update(history_error=str(exc))
            self.vision.operation_lock.release()
