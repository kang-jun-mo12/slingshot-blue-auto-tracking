"""Calibrate camera motion from acknowledged moves and matched blue observations."""
import json
import math
import time
from collections import deque
from pathlib import Path
from uuid import uuid4

import numpy as np

from servo_angles import checked_angles
from target_policy import is_centering_target
from calibration import coefficient_digest, features

HISTORY_ROOT = Path(__file__).resolve().parent / 'tracking_sessions'


class TrackingSession:
    def __init__(self, root=HISTORY_ROOT):
        self.id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8]
        self.path = Path(root) / self.id
        self.path.mkdir(parents=True, exist_ok=False)

    def write(self, event):
        with (self.path / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')

    def observation(self, number, png, angles, target, detections):
        filename = f'{number:02d}.png'
        (self.path / filename).write_bytes(png)
        event = dict(kind='observation', session_id=self.id, observation=number,
                     time=time.time(), image=filename, angles=list(angles),
                     target=target, detections=detections)
        self.write(event)
        return event


class FeedbackPlanner:
    def __init__(self, calibration, path=None):
        self.calibration = calibration
        self.path = Path(path) if path is not None else None
        self.samples = []
        if self.path and self.path.is_file():
            with self.path.open() as stream:
                for line in deque(stream, maxlen=2000):
                    try:
                        sample = json.loads(line)
                        if self.valid_sample(sample):
                            self.samples.append(sample)
                    except (ValueError, TypeError):
                        continue
        self.current_samples = 0
        self.session_id = None
        self.geometry = None
        self.observations = []
        self.last_fit = {}
        self.grid_profile = None
        profile_path = self.calibration.path.parent / 'tracking-prior.json'
        if profile_path.is_file():
            try:
                profile = json.loads(profile_path.read_text())
                coefficients = np.asarray(profile['coefficients'], dtype=float)
                if (profile['base_digest'] == coefficient_digest(self.calibration.model)
                        and coefficients.shape == (2, 15, 2) and np.isfinite(coefficients).all()
                        and all(axis in (0, 1) and profile['metrics'][axis]['validation_median_px_per_degree'] < 4
                                and profile['metrics'][axis]['validation_p95_px_per_degree'] < 12
                                for axis in profile['accepted_axes'])):
                    self.grid_profile = profile
            except (OSError, ValueError, KeyError, TypeError):
                pass

    @staticmethod
    def valid_sample(sample):
        try:
            delta = np.asarray(sample['delta_angles'], dtype=float)
            motion = np.asarray(sample['delta_pixels'], dtype=float)
            pose = np.asarray(sample['pose'], dtype=float)
            pixel = np.asarray(sample['pixel'], dtype=float)
            return (delta.shape == motion.shape == pose.shape == pixel.shape == (2,)
                    and np.isfinite([delta, motion, pose, pixel]).all()
                    and np.isfinite(sample['time'])
                    and .09 <= np.linalg.norm(delta) <= 30
                    and 0 <= np.linalg.norm(motion) <= 1200
                    and sample['geometry'] in ('obb', 'detect')
                    and sample['class_id'] == 1 and sample['class_name'] == 'ally')
        except (KeyError, TypeError, ValueError):
            return False

    def prior(self, point, pose):
        # Outside measured angles use the nearest measured Jacobian, avoiding
        # uncontrolled polynomial extrapolation. Fresh motion updates it below.
        model = self.calibration.model
        limits = np.asarray([model['yaw_range'], model['pitch_range']])
        measured_pose = np.clip(pose, limits[:, 0], limits[:, 1])
        matrix = self.calibration.jacobian(point, measured_pose)
        if self.grid_profile:
            basis = features(*point, *measured_pose)
            for axis in self.grid_profile['accepted_axes']:
                column = basis @ np.asarray(self.grid_profile['coefficients'][axis])
                ratio = np.linalg.norm(column)/max(np.linalg.norm(matrix[:, axis]), .01)
                if .5 <= ratio <= 2 and np.dot(column, matrix[:, axis]) > 0:
                    matrix[:, axis] = column
        return matrix

    def observe(self, previous, current):
        if current['session_id'] != self.session_id:
            self.session_id = current['session_id']
            self.observations = []
            self.current_samples = 0
        target = current.get('target')
        if target and is_centering_target(target):
            self.observations.append(current)
            self.observations = self.observations[-8:]
        self.geometry = 'obb' if target and 'obb_corners' in target else 'detect'
        if previous is None or target is None:
            return False
        before = previous.get('target')
        if not before or not is_centering_target(before) or not is_centering_target(target):
            return False
        if previous['session_id'] != current['session_id'] or current['observation'] != previous['observation'] + 1:
            return False
        if ('obb_corners' in before) != ('obb_corners' in target):
            return False
        if min(before['confidence'], target['confidence']) < .6:
            return False
        if not 0 < current['time'] - previous['time'] < 45:
            return False
        a, b = np.asarray(previous['angles']), np.asarray(current['angles'])
        if a[1] != b[1]:
            return False
        delta = (b-a)[[0,2]]
        p, q = np.asarray(before['center']), np.asarray(target['center'])
        def area(t):
            x1,y1,x2,y2 = t['box']
            return (x2-x1)*(y2-y1)
        if not .5 <= area(target)/max(area(before),1) <= 2:
            return False
        sample = dict(time=current['time'], session_id=current['session_id'],
                      from_observation=previous['observation'], to_observation=current['observation'],
                      pose=((a+b)[[0,2]]/2).tolist(), pixel=((p+q)/2).tolist(),
                      delta_angles=delta.tolist(), delta_pixels=(q-p).tolist(),
                      geometry=self.geometry, class_id=1, class_name='ally')
        if not self.valid_sample(sample):
            return False
        # Keep stalls and overshoots too: excluding them hid the servo's
        # dead zone and made the old learner systematically overconfident.
        expected = self.prior(sample['pixel'], sample['pose']) @ delta
        if np.linalg.norm(delta) > 3 and np.dot(q-p, expected) < -10:
            return False
        sample['version'] = 3
        self.samples.append(sample)
        self.samples = self.samples[-2000:]
        self.current_samples += 1
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a') as stream:
                stream.write(json.dumps(sample, allow_nan=False) + '\n')
        return True

    def session_samples(self):
        # A relocated mount can have the same commanded angles and a different
        # response. Keep past runs for analysis, but do not transfer their servo
        # gain or dead zone without evidence from this run. The validated grid
        # calibration still supplies the initial camera geometry.
        return (sample for sample in self.samples
                if self.session_id is not None
                and sample.get('session_id') == self.session_id
                and sample['geometry'] == self.geometry)

    def jacobian(self, point, pose, step=None):
        prior = self.prior(point, pose)
        if step is None:
            step = np.linalg.solve(prior, np.asarray([960, 540])-point)
        step = np.abs(step)
        now = time.time()
        nearby = []
        for sample in self.session_samples():
            age = now-sample['time']
            if sample['geometry'] != self.geometry or not 0 <= age <= 30*86400:
                continue
            distance = np.linalg.norm(np.asarray(sample['pose'])-pose)
            if distance > 35:
                continue
            weight = math.exp(-distance**2/800 - np.linalg.norm(np.asarray(sample['pixel'])-point)**2/640000)
            weight *= math.exp(-age/(7*86400))
            try:
                actual = np.linalg.solve(self.prior(sample['pixel'], sample['pose']), sample['delta_pixels'])
            except np.linalg.LinAlgError:
                continue
            nearby.append((sample, actual, weight))
        gains, counts, spread, fine_response_gains = [], [], [], []
        for axis in range(2):
            ratios, weights = [], []
            fine_response_gain = 1.
            for sample, actual, weight in nearby:
                delta = sample['delta_angles'][axis]
                # A stalled axis is a censored response, not zero camera gain.
                # It is retained in the log and handled by bracketing below.
                if abs(delta) < .09 or abs(actual[axis]) < .12 or delta*actual[axis] <= 0:
                    continue
                ratio = actual[axis]/delta
                if not .2 <= ratio <= 8:
                    continue
                if (abs(delta) <= 1.5 and ratio >= 1.5
                        and abs(actual[axis])*np.linalg.norm(prior[:, axis]) >= 8):
                    fine_response_gain = max(fine_response_gain, ratio)
                # Compare like-sized axis commands. Large moves must not drown
                # out the fine-motion response through their squared magnitude.
                scale_weight = math.exp(-math.log((abs(delta)+.5)/(step[axis]+.5))**2/.8)
                weights.append(weight * scale_weight * delta**2/(delta**2+.25))
                ratios.append(ratio)
            ratios, weights = np.asarray(ratios), np.asarray(weights)
            gain = 1.
            for _ in range(3):
                robust = weights*np.minimum(1., 1.5/np.maximum(np.abs(ratios-gain), .001))
                gain = (2. + np.sum(robust*ratios))/(2. + np.sum(robust))
            gain = float(np.clip(gain, .5, 4))
            # A fresh small command that caused a large jump is immediately
            # relevant near the center. A robust average alone treats it as an
            # outlier and commands another oversized reversal. Use its response
            # as a conservative gain envelope, only for fine corrections.
            if np.linalg.norm(np.asarray(point)-[960,540]) < 100 and step[axis] <= 3:
                gain = max(gain, fine_response_gain)
            gains.append(gain)
            fine_response_gains.append(fine_response_gain)
            counts.append(int(np.sum(weights>.02)))
            spread.append(float(np.sqrt(np.sum(weights*(ratios-gain)**2)/max(np.sum(weights),1))))
        matrix = prior @ np.diag(gains)
        self.last_fit = dict(axis_gains=gains, axis_samples=counts, response_spread=spread,
                             fine_response_gains=fine_response_gains, feedback_scope='current_session')
        return matrix, len(nearby)

    def bracket(self, point, pose, matrix, destination):
        # The current measured error must be one endpoint. In particular, an
        # older observation at the current command angle cannot overrule a new
        # photo: backlash means equal commands need not mean equal positions.
        result = np.asarray(destination, dtype=float).copy()
        current_residual = np.linalg.solve(matrix, np.asarray(point)-[960,540])
        used = []
        for axis in range(2):
            values = {}
            for observation in reversed(self.observations):
                angles = np.asarray(observation['angles'])[[0,2]]
                if (abs(angles[1-axis]-pose[1-axis]) > .11
                        or abs(angles[axis]-pose[axis]) > 3
                        or observation['target']['confidence'] < .6):
                    continue
                residual = np.linalg.solve(matrix, np.asarray(observation['target']['center'])-[960,540])
                # Only the latest evidence at each angle is eligible.
                values.setdefault(round(float(angles[axis]),1), float(residual[axis]))
            current = (pose[axis], current_residual[axis])
            pairs = []
            for angle, residual in values.items():
                lo, hi = sorted((current, (angle, residual)))
                if lo[1]<-.08 and hi[1]>.08 and .09<hi[0]-lo[0]<=3:
                    pairs.append((hi[0]-lo[0],lo,hi))
            if not pairs:
                continue
            width,lo,hi = min(pairs,key=lambda p:p[0])
            root = lo[0]-lo[1]*width/(hi[1]-lo[1])
            if width < .2-1e-6:
                candidate = min((lo,hi),key=lambda v:abs(v[1]))[0]
            else:
                candidate = np.clip(root,lo[0]+.1,hi[0]-.1)
            if abs(candidate-pose[axis]) < .05:
                # The closest old endpoint is already commanded but the fresh
                # photo is still off-center. Continue from the fresh response;
                # this is not proof that further correction is impossible.
                continue
            result[axis] = candidate
            used.append(axis)
        return result, used

    def measured_resolution(self, point, pose):
        """Estimate observed stick/jump limits, without claiming shaft precision.

        Require both several stalled commands and several actual fine moves.
        A smooth servo has no such limit and continues down to 0.1 degrees.
        """
        stalls = [0, 0]
        jumps = [[], []]
        now = time.time()
        for sample in self.session_samples():
            if (sample['geometry'] != self.geometry or not 0 <= now-sample['time'] < 2*86400
                    or np.linalg.norm(np.asarray(sample['pose'])-pose)>15
                    or np.linalg.norm(np.asarray(sample['pixel'])-point)>300):
                continue
            basis = self.prior(sample['pixel'],sample['pose'])
            actual = np.linalg.solve(basis,sample['delta_pixels'])*np.linalg.norm(basis,axis=0)
            for axis in range(2):
                delta = sample['delta_angles'][axis]
                if .09 <= abs(delta) <= 1.5:
                    if abs(actual[axis]) < 3:
                        stalls[axis] += 1
                    elif delta*actual[axis]>0 and abs(actual[axis])>=8:
                        jumps[axis].append(abs(actual[axis]))
        return np.asarray([min(20., float(np.percentile(jumps[i],20))*.5)
                           if stalls[i]>=3 and len(jumps[i])>=3 else 0.
                           for i in range(2)])

    def plan(self, pixel, angles):
        angles = checked_angles(angles)
        point = np.asarray(pixel,dtype=float)
        if point.shape != (2,) or not np.isfinite(point).all() or np.any(point<0) or np.any(point>[1920,1080]):
            raise ValueError('표적 픽셀 좌표가 잘못되었습니다')
        pose = np.asarray([angles[0],angles[2]])
        error = point-[960,540]
        matrix,count = self.jacobian(point,pose)
        info = dict(feedback_samples=count, jacobian=matrix.tolist(), **self.last_fit)
        if np.max(np.abs(error))<=3 and np.linalg.norm(error)<=4:
            return dict(angles=angles,predicted_center=point.tolist(),reason='centered',**info)
        if np.linalg.cond(matrix)>30 or abs(np.linalg.det(matrix))<4:
            return dict(angles=angles,predicted_center=point.tolist(),reason='uncertain',**info)
        measured = self.calibration.model
        inside = (measured['yaw_range'][0]<=pose[0]<=measured['yaw_range'][1]
                  and measured['pitch_range'][0]<=pose[1]<=measured['pitch_range'][1])
        correction = np.linalg.solve(matrix,-error)
        # Damping is most valuable close to the target where stiction and
        # reversal jumps dominate. Do not waste the coarse capture budget.
        gain = .75 if np.linalg.norm(error)<100 else .95
        correction *= gain
        max_step = 8 if inside else 3
        scale = min(1,max_step/max(np.max(np.abs(correction)),.001))
        correction *= scale
        unmeasured_fine_axes = []
        if np.linalg.norm(error) < 50:
            # After a relocation, probe a small fine move before trusting the
            # prior's gain. Stalls do not qualify as a measured axis response.
            for axis, samples in enumerate(self.last_fit['axis_samples']):
                if samples == 0:
                    correction[axis] = np.clip(correction[axis],-.3,.3)
                    unmeasured_fine_axes.append(axis)
        destination = np.clip(pose+correction,0,180)
        destination, bracket_axes = self.bracket(point,pose,matrix,destination)
        resolution = self.measured_resolution(point,pose)
        # Stick/jump statistics are diagnostic, not a success tolerance. They
        # must not freeze an axis with a visible error for all remaining photos.
        destination = np.clip(destination, np.maximum(0,pose-max_step),np.minimum(180,pose+max_step))
        info.update(control_gain=gain, bracket_axes=bracket_axes,
                    measured_resolution_px=np.round(resolution,2).tolist(), held_axes=[],
                    unmeasured_fine_axes=unmeasured_fine_axes)
        # Round before floor/ceil so binary float noise at an exact tenth does
        # not manufacture an extra 0.1-degree candidate beyond the chosen step.
        ticks = np.round(destination*10,8)
        candidates=[]
        for yaw in {round(float(np.floor(ticks[0])/10),1),round(float(np.ceil(ticks[0])/10),1)}:
            for pitch in {round(float(np.floor(ticks[1])/10),1),round(float(np.ceil(ticks[1])/10),1)}:
                predicted=point+matrix @ (np.asarray([yaw,pitch])-pose)
                candidates.append((np.linalg.norm(predicted-[960,540]),yaw,pitch,predicted))
        distance,yaw,pitch,predicted=min(candidates,key=lambda c:c[0])
        if distance>=np.linalg.norm(error)-.1 or [yaw,pitch]==pose.tolist():
            return dict(angles=angles,predicted_center=point.tolist(),reason='resolution_limit',**info)
        return dict(angles=[yaw,angles[1],pitch],predicted_center=np.round(predicted,1).tolist(),reason='moved',**info)
