import unittest
from unittest.mock import Mock, patch
from server import ArduinoConnection
from servo_angles import checked_angles


class FractionalServoTests(unittest.TestCase):
    def make_connection(self):
        arduino = ArduinoConnection()
        arduino.connection = Mock(is_open=True)
        arduino.last_angles = [90.1, 37.2, 100.3]
        return arduino

    def test_decimal_move_preserves_release_and_acknowledges_exact_command(self):
        arduino = self.make_connection()
        arduino.connection.readline.return_value = b'OK 90.2,37.2,100.4\n'
        self.assertEqual(arduino.move_camera_to(90.2,100.4,[90.1,37.2,100.3]),
                         [90.2,37.2,100.4])
        arduino.connection.write.assert_called_once_with(b'90.2,37.2,100.4\n')

    def test_fractional_jog_does_not_accumulate_float_noise(self):
        arduino = self.make_connection()
        arduino.connection.readline.return_value = b'OK 90.2,37.2,100.2\n'
        self.assertEqual(arduino.jog(.1,-.1),[90.2,37.2,100.2])
        arduino.connection.write.assert_called_once_with(b'90.2,37.2,100.2\n')

    def test_invalid_angles_never_open_serial(self):
        arduino = ArduinoConnection()
        for value in [True, '90', float('nan'), float('inf'), -0.1, 180.1, 90.15]:
            with self.subTest(value=value), patch('server.serial.Serial') as open_port:
                with self.assertRaises(ValueError):
                    arduino.send_angles([value,90,90])
                open_port.assert_not_called()
        self.assertEqual(checked_angles([-0.0,0,180]),[0,0,180])

    def test_ack_that_rounds_away_decimal_invalidates_position(self):
        arduino = self.make_connection()
        arduino.connection.readline.return_value = b'OK 90,37,100\n'
        with self.assertRaises(RuntimeError):
            arduino.move_camera_to(90.2,100.4,[90.1,37.2,100.3])
        self.assertIsNone(arduino.last_angles)
        self.assertIsNone(arduino.connection)

    def test_old_firmware_is_rejected_before_angle_command(self):
        arduino = ArduinoConnection('/dev/mock')
        port = Mock()
        port.readline.return_value = b'ERR expected: angle1,angle2,angle3\n'
        with patch('server.serial.Serial',return_value=port), patch('server.time.sleep'):
            self.assertFalse(arduino.connect())
        port.write.assert_called_once_with(b'CAPS\n')
        port.close.assert_called_once()
        self.assertIsNone(arduino.last_angles)

    def test_new_firmware_handshake(self):
        arduino = ArduinoConnection('/dev/mock')
        port = Mock()
        port.readline.return_value = b'CAPS fractional-0.1 pulse-us\n'
        with patch('server.serial.Serial',return_value=port), patch('server.time.sleep'):
            self.assertTrue(arduino.connect())
        self.assertIs(arduino.connection,port)
