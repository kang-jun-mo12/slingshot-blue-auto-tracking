"""Class policy for every automatic camera-centering path."""

TARGET_CLASS_ID = 1
TARGET_CLASS_NAME = 'ally'


def is_centering_target(detection):
    # Both fields must agree with the verified two-label model. Missing labels,
    # a legacy single-class output, or contradictory metadata cannot move axes.
    return (type(detection.get('class_id')) is int
            and detection['class_id'] == TARGET_CLASS_ID
            and detection.get('class_name') == TARGET_CLASS_NAME)


def centering_targets(detections):
    return [d for d in detections if is_centering_target(d)]
