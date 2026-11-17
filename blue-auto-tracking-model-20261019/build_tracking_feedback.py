"""Rebuild servo feedback from all raw, matched tracking observations (no hardware)."""
import argparse
import json
from pathlib import Path
from calibration import PixelCalibration
from tracking_feedback import FeedbackPlanner


def rebuild(roots, destination):
    planner = FeedbackPlanner(PixelCalibration())
    sessions, observations = [], 0
    for root in roots:
        for path in sorted(Path(root).glob('*/events.jsonl')):
            previous = None
            count = 0
            for line in path.read_text().splitlines():
                event = json.loads(line)
                if event.get('kind') != 'observation':
                    continue
                planner.observe(previous, event)
                previous = event
                count += 1
            observations += count
            sessions.append({'session':path.parent.name, 'observations':count})
    # The same session can be copied into two roots; do not duplicate evidence.
    unique = {(s['session_id'],s['from_observation'],s['to_observation']):s for s in planner.samples}
    destination = Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(''.join(json.dumps(s)+'\n' for s in unique.values()))
    temporary.replace(destination)
    report = dict(sessions=sessions, observations=observations, pairs=len(unique),
                  retained_stalls=sum(sum(v*v for v in s['delta_pixels'])<4 for s in unique.values()))
    destination.with_suffix('.report.json').write_text(json.dumps(report,indent=2))
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--roots',nargs='+',default=['tracking_sessions','diagnostics/candidate-tracking'])
    parser.add_argument('--output',default='tracking_sessions/feedback-v2.jsonl')
    args=parser.parse_args()
    print(json.dumps(rebuild(args.roots,args.output),indent=2))
