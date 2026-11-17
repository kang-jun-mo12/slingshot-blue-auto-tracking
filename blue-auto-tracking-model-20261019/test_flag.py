import io
import json
import threading
from email.message import Message
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from server import ArduinoConnection, RequestHandler


class FlagTests(unittest.TestCase):
    def setUp(self):
        self.arduino = ArduinoConnection()
        self.arduino.connection = Mock(is_open=True)
        self.arduino.last_angles = [96, 37, 101]

    def test_go_preserves_camera_axes(self):
        self.arduino.connection.readline.return_value = b'OK 96,180,101\n'
        with patch('server.time.sleep') as sleep:
            self.assertEqual(self.arduino.move_flag(), [96, 180, 101])
        self.arduino.connection.write.assert_called_once_with(b'96,180,101\n')
        sleep.assert_not_called()

    def test_return_waits_after_acknowledgement_then_returns_to_zero(self):
        events = []
        self.arduino.connection.write.side_effect = lambda command: events.append(command)
        self.arduino.connection.readline.side_effect = [b'OK 96,180,101\n', b'OK 96,0,101\n']
        with patch('server.time.sleep', side_effect=lambda seconds: events.append(('wait', seconds))):
            self.assertEqual(self.arduino.move_flag(True), [96, 0, 101])
        self.assertEqual(events, [b'96,180,101\n', ('wait', 1), b'96,0,101\n'])
        self.assertEqual(self.arduino.last_angles, [96, 0, 101])

    def test_uninitialized_or_invalid_request_never_moves(self):
        with self.assertRaises(ValueError):
            self.arduino.move_flag('true')
        self.arduino.last_angles = None
        with self.assertRaises(RuntimeError):
            self.arduino.move_flag(True)
        self.arduino.connection.write.assert_not_called()

    def test_failed_go_does_not_wait_or_report_return(self):
        self.arduino.connection.readline.return_value = b''
        with patch('server.time.sleep') as sleep:
            with self.assertRaises(RuntimeError):
                self.arduino.move_flag(True)
        sleep.assert_not_called()
        self.assertIsNone(self.arduino.last_angles)

    def test_cycle_pose_check_prevents_flag_at_changed_camera_angles(self):
        with self.assertRaises(RuntimeError):
            self.arduino.move_flag(True,expected_angles=[95,37,101])
        self.arduino.connection.write.assert_not_called()

    def test_cycle_stop_check_runs_under_serial_lock_before_go(self):
        def check():
            self.assertTrue(self.arduino.lock.locked())
            raise RuntimeError('stopped')
        with self.assertRaisesRegex(RuntimeError,'stopped'):
            self.arduino.move_flag(True,expected_angles=[96,37,101],before_go=check)
        self.arduino.connection.write.assert_not_called()

    def test_stop_after_go_still_completes_return_without_repeating_go(self):
        stopped = False
        def before_go():
            self.assertFalse(stopped)
        def wait(_):
            nonlocal stopped
            stopped = True
        self.arduino.connection.readline.side_effect = [b'OK 96,180,101\n',b'OK 96,0,101\n']
        with patch('server.time.sleep',side_effect=wait):
            result = self.arduino.move_flag(True,expected_angles=[96,37,101],before_go=before_go)
        self.assertTrue(stopped)
        self.assertEqual(result,[96,0,101])
        self.assertEqual([c.args[0] for c in self.arduino.connection.write.call_args_list],
                         [b'96,180,101\n',b'96,0,101\n'])


class StandaloneFlagEndpointTests(unittest.TestCase):
    def make_request(self, payload):
        handler = RequestHandler.__new__(RequestHandler)
        handler.path = '/api/flag'
        body = json.dumps(payload).encode()
        handler.headers = Message()
        handler.headers['Content-Type'] = 'application/json'
        handler.headers['Content-Length'] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler.send_json = Mock()
        handler.log_error = Mock()
        handler.tracker = Mock()
        handler.tracker.status.return_value = {'active': False}
        handler.scanner = Mock()
        handler.scanner.status.return_value = {'active': False}
        handler.arduino = Mock()
        handler.vision = SimpleNamespace(operation_lock=threading.Lock())
        return handler

    def test_manual_request_still_uses_standalone_endpoint(self):
        handler = self.make_request({'return_to_zero': True})
        def run(*args):
            self.assertTrue(handler.vision.operation_lock.locked())
            return [98,0,107]
        handler.arduino.move_flag.side_effect = run
        handler.do_POST()
        handler.arduino.move_flag.assert_called_once_with(True)
        handler.send_json.assert_called_once_with(200,{'ok':True,'angles':[98,0,107]})
        self.assertFalse(handler.vision.operation_lock.locked())

    def test_cached_automatic_handoff_requests_are_rejected_without_movement(self):
        for session in ('run-123', '', None):
            handler = self.make_request({'return_to_zero':True,'tracking_session':session})
            handler.do_POST()
            handler.arduino.move_flag.assert_not_called()
            handler.tracker.run_standalone_flag.assert_not_called()
            self.assertEqual(handler.send_json.call_args.args[0],400)
            self.assertFalse(handler.vision.operation_lock.locked())

    def test_active_tracking_blocks_standalone_call(self):
        handler = self.make_request({'return_to_zero':True})
        handler.tracker.status.return_value = {'active':True}
        handler.do_POST()
        handler.arduino.move_flag.assert_not_called()
        self.assertNotEqual(handler.send_json.call_args.args[0],200)

    def test_failure_releases_operation_lock_without_retry(self):
        handler = self.make_request({'return_to_zero':True})
        handler.arduino.move_flag.side_effect = RuntimeError('ack missing')
        handler.do_POST()
        handler.arduino.move_flag.assert_called_once()
        self.assertFalse(handler.vision.operation_lock.locked())
        self.assertNotEqual(handler.send_json.call_args.args[0],200)


if __name__ == '__main__':
    unittest.main()
