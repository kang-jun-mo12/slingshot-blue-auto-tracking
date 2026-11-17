const startButton = document.querySelector('#start');
const stopButton = document.querySelector('#stop');
const phase = document.querySelector('#phase');
const progress = document.querySelector('#progress');
const count = document.querySelector('#count');
const angles = document.querySelector('#angles');
const message = document.querySelector('#message');
const download = document.querySelector('#download');
const photo = document.querySelector('#latest-photo');
const placeholder = document.querySelector('#placeholder');
const yawMin = document.querySelector('#yaw-min');
const yawMax = document.querySelector('#yaw-max');
let active = false;
let busy = false;
let lastPhoto = null;

async function api(path, postData = null) {
  const response = await fetch(path, postData ? {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(postData), cache: 'no-store',
  } : { cache: 'no-store' });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '요청 실패');
  return data;
}

function render(state) {
  active = state.active;
  startButton.disabled = active || busy;
  stopButton.disabled = !active || busy;
  yawMin.disabled = active || busy;
  yawMax.disabled = active || busy;
  phase.textContent = ({ idle: '대기', starting: '시작 중', moving: '서보 이동 중',
    capturing: '촬영 중', stopping: '중지 중', packaging: 'ZIP 만드는 중',
    stopped: '중지됨', timeout: '연결 끊김 · 중지됨', done: '완료', error: '오류' })[state.phase] || state.phase;
  progress.value = state.captured;
  progress.max = state.total;
  count.textContent = `${state.captured} / ${state.total}장`;
  angles.textContent = state.angles
    ? `현재 각도: 좌우 ${state.angles[0]}° · 상하 ${state.angles[2]}°`
    : '현재 각도: -';
  if (state.error) {
    message.className = 'error';
    message.textContent = state.error;
  } else if (active) {
    message.className = '';
    message.textContent = '촬영 중입니다. 이 탭을 열어 두세요.';
  } else if (state.phase === 'done' || state.phase === 'stopped') {
    message.className = '';
    message.textContent = '사진을 내려받을 수 있습니다. 라즈베리파이에도 원본이 보관됩니다.';
  }
  download.hidden = !state.download_url;
  if (state.download_url) download.href = state.download_url;
  const key = `${state.session_id}/${state.last_photo}`;
  if (state.last_photo && key !== lastPhoto) {
    lastPhoto = key;
    photo.src = `/api/scan/photo/${state.session_id}/${state.last_photo}`;
    photo.hidden = false;
    placeholder.hidden = true;
  }
  if (!state.last_photo && lastPhoto) {
    lastPhoto = null;
    photo.hidden = true;
    placeholder.hidden = false;
  }
}

async function command(path, data = {}) {
  if (busy) return;
  busy = true;
  startButton.disabled = true;
  stopButton.disabled = true;
  try {
    render(await api(path, data));
  } catch (error) {
    message.className = 'error';
    message.textContent = error.message;
  } finally {
    busy = false;
    startButton.disabled = active;
    stopButton.disabled = !active;
    yawMin.disabled = active;
    yawMax.disabled = active;
  }
}

async function poll() {
  try {
    const state = await api(active && !document.hidden
      ? '/api/scan/heartbeat' : '/api/scan/status', active && !document.hidden ? {} : null);
    render(state);
  } catch (error) {
    message.className = 'error';
    message.textContent = `서버 연결 확인 중: ${error.message}`;
  } finally {
    setTimeout(poll, 1000);
  }
}

function stopOnLeave() {
  if (!active) return;
  const body = new Blob(['{}'], { type: 'application/json' });
  navigator.sendBeacon('/api/scan/stop', body);
}

startButton.addEventListener('click', () => {
  const min = yawMin.valueAsNumber;
  const max = yawMax.valueAsNumber;
  if (!Number.isInteger(min) || !Number.isInteger(max)
      || min < 0 || max > 180 || min >= max) {
    message.className = 'error';
    message.textContent = '좌우 시작·끝 각도를 0°~180° 범위로 입력하고, 끝 각도를 더 크게 설정하세요.';
    return;
  }
  command('/api/scan/start', { yaw_min: min, yaw_max: max });
});
stopButton.addEventListener('click', () => command('/api/scan/stop'));
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stopOnLeave();
});
window.addEventListener('pagehide', stopOnLeave);
poll();
