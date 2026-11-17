"""Reproduce the measured approach to a pose, including stalled reversals."""
import numpy as np

from servo_angles import checked_angles
from target_policy import is_centering_target
from tracking_feedback import FeedbackPlanner


def return_path(calibration, best, preload_degrees=2., current_center=None):
    goal = checked_angles(best['angles'])
    observations = best.get('return_observations', [])
    prior = FeedbackPlanner(calibration)
    loaded = [None, None]
    for index, (before, after) in enumerate(zip(observations, observations[1:])):
        a, b = before.get('target'), after.get('target')
        if (not a or not b or not is_centering_target(a) or not is_centering_target(b)
                or min(a['confidence'],b['confidence']) < .6
                or before['angles'][1] != after['angles'][1]):
            continue
        delta = (np.asarray(after['angles'])-before['angles'])[[0,2]]
        point = (np.asarray(a['center'])+b['center'])/2
        pose = (np.asarray(before['angles'])+after['angles'])[[0,2]]/2
        matrix = prior.prior(point,pose)
        try:
            actual = np.linalg.solve(matrix,np.asarray(b['center'])-a['center'])
        except np.linalg.LinAlgError:
            continue
        for axis in (0,1):
            if (abs(delta[axis]) >= .09 and delta[axis]*actual[axis] > 0
                    and abs(actual[axis])*np.linalg.norm(matrix[:,axis]) >= 3):
                loaded[axis] = (index,int(np.sign(delta[axis])))

    # A small reverse command may not move the camera at all. Remember the
    # direction of the last *visible response*, and replay the following small
    # commands too, rather than assuming the last command took up the backlash.
    anchors = [value[0] for value in loaded if value is not None]
    route = [list(o['angles']) for o in observations[min(anchors):]] if anchors else [goal]
    directions = [value[1] if value is not None else 0 for value in loaded]
    if current_center is not None and 0 in directions:
        matrix = prior.prior(best['observation']['target']['center'],[goal[0],goal[2]])
        try:
            correction = np.linalg.solve(matrix,
                np.asarray(best['observation']['target']['center'])-current_center)
            for axis in (0,1):
                if directions[axis] == 0 and abs(correction[axis])*np.linalg.norm(matrix[:,axis]) >= 3:
                    directions[axis] = int(np.sign(correction[axis]))
        except np.linalg.LinAlgError:
            pass
    if any(directions):
        lead = list(route[0])
        for axis, servo in enumerate((0,2)):
            lead[servo] = round(max(0,min(180,lead[servo]-directions[axis]*preload_degrees)),1)
        route.insert(0,lead)
    route.append(goal)
    unique = []
    for pose in route:
        # All return movement is camera-only; it never replays a D9 change.
        pose[1] = goal[1]
        pose = checked_angles(pose)
        if not unique or pose != unique[-1]:
            unique.append(pose)
    return unique, directions
