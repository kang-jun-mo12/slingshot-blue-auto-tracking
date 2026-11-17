import re
import subprocess
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent

MOCK = r"""
window.mock = {angles: [90, 37, 90], requests: [], aimRequests: [], active: 0, maxActive: 0, failNext: false};
window.fetch = async (url, options = {}) => {
  const response = (data, ok = true) => ({ok, json: async () => data});
  if (url === '/api/tracker/status') return response({active: false, phase: 'idle', angles: null});
  if (url === '/api/status') return response({connected: true, available: true, port: '/dev/mock', angles: mock.angles});
  if (url === '/api/vision') return response({busy: false, result: null, model_available: true});
  if (url === '/api/angles') {
    const data = JSON.parse(options.body);
    mock.applyRequests = (mock.applyRequests || []).concat([data]);
    mock.angles = data.angles;
    return response({angles: [...mock.angles]});
  }
  if (url === '/api/flag') {
    const data = JSON.parse(options.body);
    mock.flagRequests = (mock.flagRequests || []).concat([data]);
    await new Promise(resolve => setTimeout(resolve, 20));
    mock.angles[1] = data.return_to_zero ? 0 : 180;
    return response({angles: [...mock.angles]});
  }
  if (url === '/api/jog') {
    const data = JSON.parse(options.body);
    mock.requests.push(data);
    mock.active++;
    mock.maxActive = Math.max(mock.maxActive, mock.active);
    await new Promise(resolve => setTimeout(resolve, 20));
    mock.active--;
    if (mock.failNext) { mock.failNext = false; return response({error: 'test failure'}, false); }
    mock.angles[0] = Math.max(0, Math.min(180, mock.angles[0] + data.yaw));
    mock.angles[2] = Math.max(0, Math.min(180, mock.angles[2] + data.pitch));
    return response({angles: [...mock.angles]});
  }
  if (url === '/api/aim/step') {
    const request = JSON.parse(options.body);
    mock.aimRequests.push(request);
    const first = mock.aimRequests.length === 1;
    const center = first ? [500, 240] : [320, 240];
    if (first) mock.angles = [87, 37, 90];
    return response({
      moved: first, centered: !first, reason: first ? 'moved' : 'centered',
      angles: [...mock.angles], target: {center},
      error_px: first ? [180, 0] : [0, 0], distance_px: first ? 180 : 0,
      vision: {image_url: 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=',
        width: 640, height: 480, detections: [{center, confidence: .9}],
        timing_ms: {capture: 10, inference: 10, total: 20}},
    });
  }
  throw new Error('Unexpected request: ' + url);
};
"""

CHECKS = r"""
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const key = (type, code, target = document, extra = {}) => target.dispatchEvent(new KeyboardEvent(type, {code, bubbles: true, ...extra}));
const tap = async code => { key('keydown', code); key('keyup', code); await sleep(80); };
const check = (condition, message) => { if (!condition) throw new Error(message); };
(async () => {
  const report = document.createElement('pre');
  report.id = 'keyboard-test-result';
  document.body.append(report);
  try {
    await sleep(250);
    const release = document.querySelector('[data-servo="1"] .angle');
    release.value = 120;
    release.dispatchEvent(new Event('input', {bubbles: true}));
    await tap('KeyW');
    check(mock.angles[2] === 91, 'W must increase pitch');
    await tap('KeyS');
    await tap('KeyA');
    check(mock.angles[0] === 91 && mock.angles[2] === 90, 'S/A mapping');
    await tap('KeyD');
    check(mock.angles[0] === 90, 'D must decrease yaw');
    check(mock.angles[1] === 37 && release.value === '120', 'release draft must not apply');
    check(mock.requests.every(r => Object.keys(r).sort().join(',') === 'pitch,yaw'), 'jog must contain no release value');
    let before = mock.requests.length;
    key('keydown', 'KeyA');
    key('keydown', 'KeyA', document, {repeat: true});
    await sleep(430);
    key('keyup', 'KeyA');
    check(mock.requests.length >= before + 2, 'held key must repeat');
    before = mock.requests.length;
    await sleep(250);
    check(mock.requests.length === before && mock.maxActive === 1, 'release must stop, with single flight');
    key('keydown', 'KeyW');
    await sleep(50);
    window.dispatchEvent(new Event('blur'));
    before = mock.requests.length;
    await sleep(250);
    check(mock.requests.length === before, 'blur must stop');
    release.focus();
    key('keydown', 'KeyS', release);
    key('keyup', 'KeyS', release);
    await sleep(80);
    check(mock.requests.length === before, 'editing must not jog');
    release.blur();
    const enabled = document.querySelector('#keyboard-enabled');
    enabled.checked = false;
    enabled.dispatchEvent(new Event('change'));
    await tap('KeyW');
    check(mock.requests.length === before, 'disabled keyboard must not jog');
    enabled.checked = true;
    enabled.dispatchEvent(new Event('change'));
    const yawInput = document.querySelector('[data-servo="0"] .angle');
    const pitchInput = document.querySelector('[data-servo="2"] .angle');
    yawInput.value = '90.1';
    yawInput.dispatchEvent(new Event('input', {bubbles: true}));
    pitchInput.value = '100.3';
    pitchInput.dispatchEvent(new Event('input', {bubbles: true}));
    document.querySelector('#apply').click();
    await sleep(80);
    check(mock.applyRequests.at(-1).angles.join(',') === '90.1,120,100.3', 'Apply preserves decimals');
    document.querySelector('#keyboard-step').value = '0.1';
    await tap('KeyA');
    check(mock.requests.at(-1).yaw === 0.1, 'fractional WASD delta');
    check(yawInput.value === '90.2', 'fractional acknowledged angle stays visible');
    mock.angles = [90,37,90];
    document.querySelector('#reverse-yaw').checked = true;
    document.querySelector('#keyboard-step').value = 3;
    await tap('KeyA');
    check(mock.requests.at(-1).yaw === -3, 'reversal and step setting');
    before = mock.requests.length;
    key('keydown', 'KeyW', document, {ctrlKey: true});
    key('keyup', 'KeyW');
    await sleep(80);
    check(mock.requests.length === before, 'modifier shortcut must not jog');
    mock.failNext = true;
    key('keydown', 'KeyW');
    await sleep(400);
    check(mock.requests.length === before + 1, 'failure must stop repeat');
    key('keyup', 'KeyW');
    mock.angles = null;
    await refreshStatus();
    before = mock.requests.length;
    await tap('KeyD');
    check(mock.requests.length === before, 'uninitialized position must not jog');
    mock.angles = [90, 37, 90];
    await refreshStatus();
    document.querySelector('#flag-go').click();
    check(document.querySelector('#flag-go-return').disabled, 'flag requests must block duplicate clicks');
    await sleep(80);
    check(mock.angles.join(',') === '90,180,90' && release.value === '180', 'Go must move only D9 to 180');
    document.querySelector('#flag-go-return').click();
    await sleep(80);
    check(mock.angles.join(',') === '90,0,90' && release.value === '0', 'Go & Return must show final zero');
    check(mock.flagRequests.map(r => r.return_to_zero).join(',') === 'false,true', 'flag button mapping');
    window.servoController.setTracking(true, mock.angles);
    check(document.querySelector('#flag-go').disabled && document.querySelector('#flag-go-return').disabled, 'tracking must disable flag buttons');
    window.servoController.setTracking(false, mock.angles);
    report.dataset.status = 'passed';
    report.textContent = 'PASSED: keyboard mapping, stop, D9 isolation';
  } catch (error) {
    report.dataset.status = 'failed';
    report.textContent = error.stack;
  }
})();
"""


class TestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def do_GET(self):
        if self.path == "/":
            html = (ROOT / "web/index.html").read_text()
            html, count = re.subn(r'(<script src="/app\.js(?:\?[^"<>]*)?"></script>)',
                r'<script src="/mock.js"></script>\1<script src="/checks.js"></script>', html)
            if count != 1:
                raise RuntimeError('Expected exactly one app script; refuse unmocked browser test')
            body, content_type = html.encode(), "text/html; charset=utf-8"
        elif self.path in ("/mock.js", "/checks.js"):
            body = (MOCK if self.path == "/mock.js" else CHECKS).encode()
            content_type = "application/javascript"
        elif self.path.startswith("/api/"):
            self.send_error(500, "Tests must never call a real API")
            return
        else:
            return super().do_GET()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    with ThreadingHTTPServer(("127.0.0.1", 0), TestHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="servo-keyboard-browser-") as profile:
                result = subprocess.run([
                    "/usr/lib/chromium/chromium", "--headless", "--disable-gpu",
                    "--no-first-run", "--no-default-browser-check", "--password-store=basic",
                    "--disable-background-networking", "--disable-dev-shm-usage",
                    "--user-data-dir=" + profile, "--virtual-time-budget=6000", "--dump-dom",
                    f"http://127.0.0.1:{server.server_port}",
                ], capture_output=True, timeout=30)
            dom = result.stdout.decode(errors="replace")
            if result.returncode or 'id="keyboard-test-result" data-status="passed"' not in dom:
                raise RuntimeError(dom[-4000:] + result.stderr.decode(errors="replace")[-1500:])
            print("Browser keyboard checks passed; all servo requests were mocked.")
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
