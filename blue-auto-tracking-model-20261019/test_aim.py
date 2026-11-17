import unittest
from unittest.mock import Mock

from aim import AimController
from server import ArduinoConnection


def detected(center, detections=None):
    return {
        "width": 640, "height": 480,
        "detections": detections if detections is not None else
        [{"class_id": 1, "class_name": "ally", "center": center, "confidence": 0.9, "box": [0, 0, 30, 30]}],
    }


def request(**updates):
    data = {"expected_angles": [90, 37, 90], "reverse_yaw": False,
            "reverse_pitch": False, "previous_center": None,
            "previous_distance": None}
    data.update(updates)
    return data


class AimTests(unittest.TestCase):
    def setUp(self):
        self.arduino = Mock()
        self.arduino.jog.return_value = [87, 37, 92]
        self.vision = Mock()
        self.controller = AimController(self.vision, self.arduino)

    def test_moves_camera_toward_target_and_keeps_release_angle(self):
        self.vision.capture_and_detect.return_value = detected([500, 150])
        result = self.controller.step(request())
        self.arduino.jog.assert_called_once_with(-3, 2, expected_angles=[90, 37, 90])
        self.assertTrue(result["moved"])
        self.assertEqual(result["angles"][1], 37)

    def test_reversal_flips_both_directions(self):
        self.vision.capture_and_detect.return_value = detected([500, 330])
        self.arduino.jog.return_value = [93, 37, 92]
        self.controller.step(request(reverse_yaw=True, reverse_pitch=True))
        self.arduino.jog.assert_called_once_with(3, 2, expected_angles=[90, 37, 90])

    def test_centered_or_missing_target_never_moves(self):
        for frame, reason in [(detected([327, 235]), "centered"),
                              (detected(None, []), "no_target")]:
            self.vision.capture_and_detect.return_value = frame
            result = self.controller.step(request())
            self.assertEqual(result["reason"], reason)
            self.assertFalse(result["moved"])
        self.arduino.jog.assert_not_called()

    def test_divergence_and_target_loss_stop_before_motion(self):
        self.vision.capture_and_detect.return_value = detected([500, 240])
        result = self.controller.step(request(previous_center=[470, 240],
                                              previous_distance=100))
        self.assertEqual(result["reason"], "diverging")
        self.vision.capture_and_detect.return_value = detected([600, 400])
        result = self.controller.step(request(previous_center=[300, 200]))
        self.assertEqual(result["reason"], "target_lost")
        self.arduino.jog.assert_not_called()

    def test_last_observation_does_not_move(self):
        self.vision.capture_and_detect.return_value = detected([500, 240])
        result = self.controller.step(request(observe_only=True))
        self.assertEqual(result["reason"], "max_steps")
        self.arduino.jog.assert_not_called()

    def test_uninitialized_or_changed_board_does_not_capture_or_move(self):
        connection = ArduinoConnection()
        controller = AimController(self.vision, connection)
        with self.assertRaises(RuntimeError):
            controller.step(request())
        connection.connection = Mock(is_open=True)
        connection.last_angles = [90, 38, 90]
        with self.assertRaises(RuntimeError):
            controller.step(request())
        self.vision.capture_and_detect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
