import subprocess
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent

MOCK = r"""
window.mock = {
  state: {active: false, phase: 'idle', error: null, target: null,
    detections: 0, error_px: null, angles: null, inference_ms: null,
    processed_seq: 0, frame_seq: 0, width: 1920, height: 1080},
  angles: null, starts: 0, stops: 0, applies: 0, jogs: 0, ready: 0,
  delayStart: false, failStart: false, flags: [], failFlag: false,
};
window.fetch = async (url, options = {}) => {
  const response = (data = {...mock.state}, ok = true) => ({ok, json: async () => data});
  if (url === '/api/status') return response({connected: !!mock.angles, available: true, angles: mock.angles});
  if (url === '/api/vision') return response({busy: false, result: null, model_available: true});
  if (url === '/api/tracker/status') return response();
  if (url === '/api/calibration') return response({valid: true, pair_count: 821, sample_count: 96853});
  if (url.startsWith('/api/tracker/frame?')) {
    const after = Number(url.split('after=')[1]);
    if (!mock.state.frame_seq || after >= mock.state.frame_seq) return {status: 204, ok: true};
    const bytes = Uint8Array.from(atob('R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs='), c => c.charCodeAt(0));
    return {status: 200, ok: true, blob: async () => new Blob([bytes], {type: 'image/gif'}),
      headers: {get: () => String(mock.state.frame_seq)}};
  }
  if (url === '/api/tracker/start') {
    const args = JSON.parse(options.body);
    if (Object.keys(args).sort().join(',') !== 'reverse_pitch,reverse_yaw')
      throw new Error('start must not apply angle drafts');
    mock.starts++;
    if (mock.delayStart) await new Promise(resolve => setTimeout(resolve, 100));
    if (mock.failStart) return response({error: 'test init failed'}, false);
    mock.angles = mock.angles || [90, 90, 90];
    mock.state.angles = [...mock.angles];
    mock.state.active = true;
    mock.state.phase = 'searching';
    Object.assign(mock.state, {history_session: 'session-' + mock.starts,
      flag_ready: false, flag_claimed: false, flag_started: false, flag_completed: false});
    return response();
  }
  if (url === '/api/tracker/stop') {
    mock.stops++;
    mock.state.active = false;
    mock.state.phase = 'stopped';
    return response();
  }
  if (url === '/api/tracker/heartbeat') return response();
  if (url === '/api/flag') {
    const args = JSON.parse(options.body);
    if (mock.state.active) throw new Error('flag must run after tracking ends');
    mock.flags.push(args);
    if (mock.failFlag) return response({error: 'test flag failed'}, false);
    mock.angles[1] = args.return_to_zero ? 0 : 180;
    if (args.tracking_session) Object.assign(mock.state, {flag_claimed: true,
      flag_ready: false, flag_started: true, flag_completed: true, angles: [...mock.angles]});
    return response({angles: [...mock.angles]});
  }
  if (url === '/api/angles') {
    mock.applies++;
    mock.angles = JSON.parse(options.body).angles;
    return response({angles: [...mock.angles]});
  }
  if (url === '/api/jog') {
    mock.jogs++;
    const delta = JSON.parse(options.body);
    mock.angles[0] += delta.yaw;
    mock.angles[2] += delta.pitch;
    return response({angles: [...mock.angles]});
  }
  if (url === '/api/servo/ready') {
    mock.ready++;
    mock.angles = [90, 90, 90];
    return response({angles: [...mock.angles]});
  }
  if (url === '/api/aim/step') {
    return response({moved: false, centered: true, reason: 'centered', angles: mock.angles,
      error_px: [0, 0], distance_px: 0, target: {center: [960, 540]},
      vision: {image_url: 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=',
        width: 1920, height: 1080, detections: [], timing_ms: {capture: 0, inference: 0, total: 0}}});
  }
  throw new Error('Unexpected request: ' + url);
};
navigator.sendBeacon = () => { mock.stops++; mock.state.active = false; return true; };
"""

CHECKS = r"""
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const key = (target = document, extra = {}) =>
  target.dispatchEvent(new KeyboardEvent('keydown', {code: 'Space', bubbles: true, ...extra}));
const check = (condition, message) => { if (!condition) throw new Error(message); };
(async () => {
  const report = document.createElement('pre');
  report.id = 'live-test-result';
  document.body.append(report);
  try {
    await sleep(150);
    const ids = [...document.querySelectorAll('[id]')].map(node => node.id);
    check(ids.length === new Set(ids).size, 'all unified page IDs must be unique');
    check(document.querySelectorAll('.servo').length === 3, 'manual angles must be on the same page');
    check(mock.applies === 0 && mock.ready === 0 && mock.starts === 0, 'page load must not initialize hardware');
    const release = document.querySelector('[data-servo="1"] .angle');
    release.value = 120;
    release.dispatchEvent(new Event('input'));
    document.querySelector('#tracking-toggle').click();
    await sleep(100);
    check(mock.starts === 1 && mock.state.active && mock.applies === 0, 'tracking starts without prior Apply');
    check(mock.angles[1] === 90, 'unsubmitted release draft must not apply');
    check(document.querySelector('#apply').disabled && release.disabled, 'manual controls disabled during tracking');
    key(document, {repeat: true});
    key(document, {code: 'KeyS'});
    await sleep(50);
    check(mock.starts === 1 && mock.stops === 0 && mock.jogs === 0, 'S and repeat must not conflict with tracking');
    key(release);
    key(document, {ctrlKey: true});
    await sleep(50);
    check(mock.stops === 0, 'editing/modifier Space must not stop');
    key();
    await sleep(100);
    check(mock.stops === 1 && !mock.state.active, 'Space stops tracking');
    check(!document.querySelector('#apply').disabled && !release.disabled, 'manual controls recover after stop');
    key(document, {code: 'KeyS'});
    document.dispatchEvent(new KeyboardEvent('keyup', {code: 'KeyS'}));
    await sleep(100);
    check(mock.jogs === 1 && mock.angles[2] === 89, 'S retains manual pitch movement');
    release.value = 37;
    release.dispatchEvent(new Event('input'));
    document.querySelector('#apply').click();
    await sleep(100);
    check(mock.applies === 1 && mock.angles[1] === 37, 'manual Apply works on unified page');
    key();
    await sleep(100);
    check(mock.state.angles[1] === 37, 'tracking preserves previously applied release angle');
    key();
    await sleep(100);
    mock.angles = null;
    await refreshStatus();
    document.querySelector('#aim').click();
    await sleep(100);
    check(mock.ready === 0 && mock.state.active, 'calibrated centering uses the same server-side sequence');
    document.querySelector('#aim-stop').click();
    await sleep(100);
    check(!mock.state.active, 'centering stop ends the sequence');
    mock.state.frame_seq = 1;
    mock.state.processed_seq = 1;
    mock.state.phase = 'completed';
    mock.state.observations = 4;
    mock.state.total_observations = 4;
    mock.state.returned_to_best = true;
    mock.state.best_distance_px = 4.2;
    await sleep(1800);
    check(!document.querySelector('#camera-photo').hidden, 'final verification image must load even after automatic stop');
    check(document.querySelector('#move-count').textContent.includes('4/4'), 'show all four completed observations');
    check(document.querySelector('#aim-status').textContent.includes('4.2 px'), 'show best measured distance after return');
    Object.assign(mock.state, {active: true, phase: 'measuring_distance', workflow: 'track_distance_offset',
      distance_observations: 1, total_distance_observations: 1, distance_source_observation: 4, preview_stage: 'tracking',
      return_verified: true, final_distance_px: 6.2});
    await sleep(1300);
    check(document.querySelector('#apply').disabled, 'distance measurement keeps manual controls disabled');
    check(document.querySelector('#distance-progress').textContent.includes('4번째 추적 박스'), 'show reused tracking box source');
    Object.assign(mock.state, {active: false, phase: 'completed', distance_observations: 1,
      offset_applied: true, flag_started: false, flag_completed: false, return_verified: false,
      final_distance_px: null, alignment_distance_px: 6.2, preview_stage: 'before_offset',
      best_angles: [100,37,95], return_attempt: 1, return_confirmed: false, return_angles_applied: true, return_distance_px: 6.2,
      angles: [98,37,106], distance_measurement: {distance_m: 1.75, box_size_px: [46.4,71.2],
        sample_count: 1, yaw_offset_deg: -2, pitch_offset_deg: 11, range_clamped: false}});
    await sleep(800);
    check(document.querySelector('#estimated-distance').textContent.includes('1.75 m'), 'show calibrated distance');
    check(document.querySelector('#distance-offset').textContent.includes('-2° / +11°'), 'show signed offsets');
    check(document.querySelector('#final-offset-status').textContent.includes('최종 각도 유지'), 'show final offset completion');
    check(document.querySelector('#camera-state').textContent.includes('보정 전'), 'photo must not claim to show the post-offset position');
    check(!document.querySelector('#tracking-status').textContent.includes('재촬영하지'), 'completed state identifies reused tracking box');
    check(!document.querySelector('#apply').disabled, 'controls recover after the complete cycle');
    check(document.querySelector('#best-servo-angles').textContent === '100° / 95°', 'show original best servo pose separately');
    check(document.querySelector('#return-check').textContent.includes('각도 복귀 완료'), 'show acknowledged return angle without claiming matching physical error');
    Object.assign(mock.state, {return_distance_px: 28});
    await sleep(1300);
    check(document.querySelector('#return-check').textContent.includes('촬영 오차 28'), 'show measured error without a retry or failure label');
    check(!document.querySelector('#return-check').textContent.includes('/3'), 'single return must not show repeated return attempts');
    // Even a legacy status claiming flag readiness must never trigger movement.
    Object.assign(mock.state, {flag_ready: true, flag_claimed: false,
      flag_started: false, flag_completed: false, history_session: 'old-session'});
    await sleep(1400);
    check(mock.flags.length === 0, 'viewing completed history must not auto-run flag');
    key();
    await sleep(100);
    mock.angles = [96,37,106];
    Object.assign(mock.state, {active: false, phase: 'completed', angles: [...mock.angles],
      flag_ready: false, flag_claimed: false, flag_started: false, flag_completed: false});
    await sleep(2800);
    check(mock.flags.length === 0, 'completed run and repeated polls must never auto-call flag');
    check(mock.angles[1] === 37, 'tracking completion preserves the flag angle');
    check(document.querySelector('#tracking-status').textContent.includes('최종 서보 보정을 완료'),
      'completion message must end at final servo offsets');
    check(!document.querySelector('#apply').disabled, 'manual controls recover after offsets');
    document.querySelector('#flag-go-return').click();
    await sleep(100);
    check(mock.flags.length === 1 && !mock.flags[0].tracking_session,
      'only an explicit manual Go & Return click may call the standalone action');
    mock.failStart = true;
    key();
    await sleep(100);
    check(!document.querySelector('#apply').disabled, 'startup error releases controls');
    mock.failStart = false;
    mock.delayStart = true;
    key();
    await sleep(20);
    window.dispatchEvent(new Event('pagehide'));
    await sleep(200);
    check(!mock.state.active, 'leaving during initialization must stop late start');
    report.dataset.status = 'passed';
    report.textContent = 'PASSED: unified controls, automatic initialization, Space/WASD isolation, stop and failure recovery';
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
        if self.path in ("/", "/tracker.html"):
            html = (ROOT / "web/index.html").read_text()
            html = html.replace('<script src="/app.js?v=final-offset6"></script>',
                                '<script src="/mock.js"></script>'
                                '<script src="/app.js?v=final-offset6"></script>')
            html = html.replace('</body>', '<script src="/checks.js"></script></body>')
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
            with tempfile.TemporaryDirectory(prefix="servo-live-browser-") as profile:
                result = subprocess.run([
                    "/usr/lib/chromium/chromium", "--headless", "--disable-gpu",
                    "--no-first-run", "--no-default-browser-check", "--password-store=basic",
                    "--disable-background-networking", "--disable-dev-shm-usage",
                    "--user-data-dir=" + profile, "--virtual-time-budget=24000", "--window-size=1440,1080",
                    "--screenshot=/tmp/unified-controller.png", "--dump-dom",
                    f"http://127.0.0.1:{server.server_port}/tracker.html",
                ], capture_output=True, timeout=30)
            dom = result.stdout.decode(errors="replace")
            if result.returncode or 'id="live-test-result" data-status="passed"' not in dom:
                raise RuntimeError(dom[-4000:] + result.stderr.decode(errors="replace")[-1500:])
            print("Live tracking browser checks passed; servo and camera APIs were mocked.")
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
