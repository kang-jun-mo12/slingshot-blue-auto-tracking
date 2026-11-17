"""Empirical distance and flag offsets from original-image bounding box sizes."""
import itertools
import json
import math
import statistics
from pathlib import Path

from servo_angles import checked_angles

DEFAULT_PATH = Path(__file__).resolve().parent / 'calibration' / 'flag-distance.json'


class DistanceMeasurementError(ValueError):
    pass


class DistanceCalibration:
    def __init__(self, path=DEFAULT_PATH):
        self.data = json.loads(Path(path).read_text())
        self.rows = self.data['measurements']
        if (self.data['image_size'] != [1920, 1080]
                or self.data['box_geometry'] != 'axis_aligned_width_height'
                or self.data['pitch_direction'] not in (-1, 1)
                or len(self.rows) < 2):
            raise ValueError('거리 측정표 형식이 올바르지 않습니다')
        numbers = [self.data['yaw_offset_deg'], self.data.get('yaw_trim_deg', 0),
                   self.data.get('pitch_trim_deg', 0)]
        for row in self.rows:
            numbers.extend(row[k] for k in ('distance_m','width_px','height_px','pitch_offset_deg'))
            if min(row[k] for k in ('distance_m','width_px','height_px')) <= 0:
                raise ValueError('거리와 박스 크기는 양수여야 합니다')
        if not all(type(x) in (int,float) and math.isfinite(x) for x in numbers):
            raise ValueError('거리 측정표에는 유한한 숫자가 필요합니다')
        self.scales = [math.sqrt(r['width_px']*r['height_px']) for r in self.rows]
        if any(a['distance_m'] >= b['distance_m'] or sa <= sb
               for a,b,sa,sb in zip(self.rows,self.rows[1:],self.scales,self.scales[1:])):
            raise ValueError('거리가 증가할수록 측정된 박스 크기가 감소해야 합니다')

    @staticmethod
    def box_size(target):
        box = target.get('box', [])
        if (len(box) != 4 or not all(type(x) in (int,float) and math.isfinite(x) for x in box)
                or not 0 <= box[0] < box[2] <= 1920
                or not 0 <= box[1] < box[3] <= 1080):
            raise DistanceMeasurementError('유효한 원본 사진의 박스 크기가 필요합니다')
        return [box[2]-box[0], box[3]-box[1]]

    def estimate(self, sizes):
        """Use at least two consistent frames; never extrapolate the table.

        Geometric mean size gives width and height equal relative weight. It
        interpolates every recorded row exactly, without assuming an ideal
        pinhole model for this detector. Two percent endpoint noise is clamped
        and explicitly reported, rather than extrapolated.
        """
        valid = [(i, list(size)) for i,size in enumerate(sizes)
                 if size is not None and len(size) == 2
                 and all(type(x) in (int,float) and math.isfinite(x) and x>0 for x in size)]
        candidates = []
        for count in range(len(valid),1,-1):
            for group in itertools.combinations(valid,count):
                median = [statistics.median(s[axis] for _,s in group) for axis in (0,1)]
                spread = [max(s[axis] for _,s in group)-min(s[axis] for _,s in group) for axis in (0,1)]
                if all(spread[axis]/median[axis] <= .06 for axis in (0,1)):
                    candidates.append((sum(spread[a]/median[a] for a in (0,1)),group,median))
            if candidates:
                break
        if not candidates:
            raise DistanceMeasurementError('박스 크기를 2회 이상 일관되게 확인하지 못했습니다. 표적을 고정하고 다시 시작하세요.')
        _, group, median = min(candidates,key=lambda c:c[0])
        return self._estimate_size(median, [i+1 for i,_ in group])

    def estimate_box(self, size):
        """Estimate directly from one real tracking box, without extra captures."""
        if (size is None or len(size) != 2
                or not all(type(x) in (int,float) and math.isfinite(x) and x > 0 for x in size)):
            raise DistanceMeasurementError('유효한 마지막 추적 박스 크기가 필요합니다.')
        return self._estimate_size(list(size), [1])

    def _estimate_size(self, median, used_samples):
        scale = math.sqrt(median[0]*median[1])
        if scale > self.scales[0]*1.02 or scale < self.scales[-1]*.98:
            raise DistanceMeasurementError('박스 크기가 측정표의 1.50~2.00m 범위를 벗어났습니다.')
        clamped_scale = min(self.scales[0],max(self.scales[-1],scale))
        for index in range(len(self.rows)-1):
            if self.scales[index] >= clamped_scale >= self.scales[index+1]:
                break
        fraction = (self.scales[index]-clamped_scale)/(self.scales[index]-self.scales[index+1])
        a,b = self.rows[index:index+2]
        def interpolate(key):
            return a[key]+fraction*(b[key]-a[key])
        expected_ratio = interpolate('width_px')/interpolate('height_px')
        if abs(median[0]/median[1]/expected_ratio-1) > .12:
            raise DistanceMeasurementError('박스의 가로·세로 비율이 측정표와 다릅니다. 같은 표적과 자세인지 확인하세요.')
        # Apply the user's fixed trims once to the final interpolated offsets.
        # Keep original measurements intact for distance estimation and audit.
        return dict(distance_m=round(interpolate('distance_m'),4),
                    box_size_px=[round(x,2) for x in median],
                    yaw_offset_deg=round(self.data['yaw_offset_deg']+self.data.get('yaw_trim_deg',0),1),
                    pitch_offset_deg=round(interpolate('pitch_offset_deg')*self.data['pitch_direction']
                                          +self.data.get('pitch_trim_deg',0),1),
                    used_samples=used_samples, sample_count=len(used_samples),
                    range_clamped=not math.isclose(scale,clamped_scale,abs_tol=1e-8),
                    bracket_m=[a['distance_m'],b['distance_m']],
                    calibration_version=self.data['version'])

    @staticmethod
    def corrected_angles(angles, estimate):
        angles = checked_angles(angles)
        requested = [round(angles[0]+estimate['yaw_offset_deg'],1),angles[1],
                     round(angles[2]+estimate['pitch_offset_deg'],1)]
        try:
            return checked_angles(requested)
        except ValueError as exc:
            raise DistanceMeasurementError('거리 보정 후 각도가 0~180°를 벗어납니다. 시작 위치를 조절해 주세요.') from exc
