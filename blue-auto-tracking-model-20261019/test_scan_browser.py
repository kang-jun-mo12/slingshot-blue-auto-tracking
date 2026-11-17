import subprocess
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent

MOCK = r"""
window.mock = {
  state: {active: false, phase: 'idle', error: null, session_id: null,
    captured: 0, total: 328, angles: null, last_photo: null, download_url: null},
  starts: 0, stops: 0,
};
window.fetch = async (url, options = {}) => {
  const response = () => ({ok: true, json: async () => ({...mock.state})});
  if (url === '/api/scan/status' || url === '/api/scan/heartbeat') return response();
  if (url === '/api/scan/start') {
    mock.starts++;
    mock.startBody = JSON.parse(options.body);
    Object.assign(mock.state, {active: true, phase: 'moving',
      session_id: 'a'.repeat(32), angles: [90, 74, 90]});
    return response();
  }
  if (url === '/api/scan/stop') {
    mock.stops++;
    Object.assign(mock.state, {active: false, phase: 'stopped', captured: 2,
      angles: [33, 74, 90], download_url: '/api/scan/download/' + 'a'.repeat(32) + '.zip'});
    return response();
  }
  throw new Error('Unexpected request: ' + url);
};
"""

CHECKS = r"""
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const check = (condition, message) => { if (!condition) throw new Error(message); };
(async () => {
  const report = document.createElement('pre');
  report.id = 'scan-test-result';
  document.body.append(report);
  try {
    await sleep(100);
    document.querySelector('#yaw-min').value = '42';
    document.querySelector('#yaw-max').value = '57';
    document.querySelector('#start').click();
    await sleep(100);
    check(mock.starts === 1 && mock.state.active, 'start must launch scan once');
    check(mock.startBody.yaw_min === 42 && mock.startBody.yaw_max === 57,
      'start must send entered yaw range');
    check(document.querySelector('#start').disabled, 'start disabled while active');
    check(document.querySelector('#yaw-min').disabled, 'range inputs disabled while active');
    document.querySelector('#stop').click();
    await sleep(100);
    check(mock.stops === 1 && !mock.state.active, 'stop must stop scan');
    const link = document.querySelector('#download');
    check(!link.hidden && link.href.includes('/api/scan/download/'), 'ZIP link appears');
    check(document.querySelector('#count').textContent.includes('2 / 328'), 'count updated');
    report.dataset.status = 'passed';
    report.textContent = 'PASSED: scan start, stop, progress, download';
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
        if self.path == "/scan.html":
            html = (ROOT / "web/scan.html").read_text()
            html = html.replace('<script src="/scan.js?v=2"></script>',
                                '<script src="/mock.js"></script>'
                                '<script src="/scan.js?v=2"></script>'
                                '<script src="/checks.js"></script>')
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
            with tempfile.TemporaryDirectory(prefix="servo-scan-browser-") as profile:
                result = subprocess.run([
                    "/usr/lib/chromium/chromium", "--headless", "--disable-gpu",
                    "--no-first-run", "--no-default-browser-check", "--password-store=basic",
                    "--disable-background-networking", "--disable-dev-shm-usage",
                    "--user-data-dir=" + profile, "--virtual-time-budget=3000", "--dump-dom",
                    f"http://127.0.0.1:{server.server_port}/scan.html",
                ], capture_output=True, timeout=30)
            dom = result.stdout.decode(errors="replace")
            if result.returncode or 'id="scan-test-result" data-status="passed"' not in dom:
                raise RuntimeError(dom[-4000:] + result.stderr.decode(errors="replace")[-1500:])
            print("Photo scan browser checks passed; servo and camera APIs were mocked.")
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
