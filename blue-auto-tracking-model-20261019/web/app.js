const cards = [...document.querySelectorAll('.servo')];
const message = document.querySelector('#message');
const connection = document.querySelector('#connection');
const applyButton = document.querySelector('#apply');
const flagGoButton = document.querySelector('#flag-go');
const flagReturnButton = document.querySelector('#flag-go-return');
const keyboardEnabled = document.querySelector('#keyboard-enabled');
const keyboardState = document.querySelector('#keyboard-state');
const aimButton = document.querySelector('#aim');
const aimStopButton = document.querySelector('#aim-stop');
const aimStatus = document.querySelector('#aim-status');
const heldKeys = new Set();
let motionBusy = false;
let trackingBusy = false;
let keyboardReady = false;
let acknowledgedAngles = null;
let visionAvailable = false;
let aiming = false;
let stopAim = false;
let keyTimer = null;
let draftTouched = false;
let initialPositionSynced = false;

function clamp(value) {
  return Math.max(0, Math.min(180, Math.round((Number(value) || 0) * 10) / 10));
}

function setCardValue(card, value) {
  const angle = clamp(value);
  card.querySelector('input[type="range"]').value = angle;
  card.querySelector('.angle').value = angle;
  card.querySelector('output').value = `${angle}°`;
}

cards.forEach((card) => {
  const range = card.querySelector('input[type="range"]');
  const number = card.querySelector('.angle');
  range.addEventListener('input', () => { draftTouched = true; setCardValue(card, range.value); });
  number.addEventListener('input', () => { draftTouched = true; setCardValue(card, number.value); });
  card.querySelectorAll('.step').forEach((button) => {
    button.addEventListener('click', () => { draftTouched = true; setCardValue(card, Number(number.value) + Number(button.dataset.step)); });
  });
});

async function refreshStatus() {
  try {
    const response = await fetch('/api/status', { cache: 'no-store' });
    const status = await response.json();
    const ready = status.ready ?? (status.connected && Array.isArray(status.angles));
    connection.className = `connection ${ready ? 'online' : status.available ? '' : 'offline'}`;
    connection.querySelector('strong').textContent = ready ? status.port : status.connected ? 'Arduino 응답 대기' : status.available ? 'USB 감지됨' : '연결 안 됨';
    connection.title = status.message;
    keyboardReady = status.connected && Array.isArray(status.angles);
    acknowledgedAngles = keyboardReady ? status.angles : null;
    if (!initialPositionSynced && !draftTouched && !motionBusy && status.angles) {
      cards.forEach((card, index) => setCardValue(card, status.angles[index]));
      initialPositionSynced = true;
    }
    if (!keyboardReady) stopKeys();
    updateKeyboardState();
    updateAimControls();
  } catch {
    connection.className = 'connection offline';
    connection.querySelector('strong').textContent = '서버 오류';
    keyboardReady = false;
    acknowledgedAngles = null;
    stopKeys();
    updateKeyboardState();
    updateAimControls();
  }
}

async function applyAngles() {
  stopKeys();
  if (motionBusy || trackingBusy || aiming || capturing) return;
  motionBusy = true;
  const angles = cards.map((card) => Number(card.querySelector('.angle').value));
  applyButton.disabled = true;
  message.className = '';
  message.textContent = '아두이노로 전송 중...';
  try {
    const response = await fetch('/api/angles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ angles }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '전송 실패');
    keyboardReady = true;
    acknowledgedAngles = data.angles;
    message.textContent = `적용됨 · ${angles.join('° / ')}°`;
    await refreshStatus();
  } catch (error) {
    keyboardReady = false;
    message.className = 'error';
    message.textContent = error.message;
  } finally {
    motionBusy = false;
    applyButton.disabled = false;
    updateKeyboardState();
    updateAimControls();
  }
}

async function moveFlag(returnToZero) {
  if (motionBusy || trackingBusy || aiming || capturing || !keyboardReady)
    return {ok: false, error: '서보 연결 또는 다른 동작이 끝난 상태를 확인해 주세요'};
  stopKeys();
  motionBusy = true;
  updateAimControls();
  message.className = '';
  message.textContent = returnToZero ? '깃발 180° 이동 → 1초 대기 → 0° 복귀 중…' : '깃발을 180°로 이동 중…';
  try {
    const response = await fetch('/api/flag', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({return_to_zero: returnToZero}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '깃발 이동 실패');
    acknowledgedAngles = result.angles;
    cards.forEach((card, index) => setCardValue(card, result.angles[index]));
    message.textContent = returnToZero ? '깃발 0° 복귀 완료' : '깃발 180° 적용됨';
    return {...result, ok: true};
  } catch (error) {
    message.className = 'error';
    message.textContent = error.message;
    return {ok: false, error: error.message};
  } finally {
    motionBusy = false;
    await refreshStatus();
    updateAimControls();
  }
}

flagGoButton.addEventListener('click', () => moveFlag(false));
flagReturnButton.addEventListener('click', () => moveFlag(true));

document.querySelector('#center').addEventListener('click', () => { if (trackingBusy || motionBusy) return; stopKeys(); draftTouched = true; cards.forEach((card) => setCardValue(card, 90)); });
applyButton.addEventListener('click', applyAngles);
refreshStatus();
setInterval(refreshStatus, 3000);

function updateKeyboardState() {
  keyboardState.textContent = trackingBusy ? '추적 중 · 수동 조작 대기' : !keyboardEnabled.checked ? '꺼짐' : !keyboardReady ? '서보 연결 대기' : heldKeys.size ? '조작 중' : '준비';
}

function stopKeys() {
  heldKeys.clear();
  clearTimeout(keyTimer);
  keyTimer = null;
  updateKeyboardState();
}

function isEditing(target) {
  return target?.isContentEditable || target?.closest?.('input, textarea, select, [role="textbox"]');
}

async function jogKeys() {
  keyTimer = null;
  if (trackingBusy || motionBusy || !heldKeys.size || !keyboardReady || !keyboardEnabled.checked || document.hidden) return;
  const stepInput = document.querySelector('#keyboard-step');
  const step = Math.max(0.1, Math.min(5, Math.round((Number(stepInput.value) || 0.1) * 10) / 10));
  const yawSign = document.querySelector('#reverse-yaw').checked ? -1 : 1;
  const pitchSign = document.querySelector('#reverse-pitch').checked ? -1 : 1;
  const yaw = (Number(heldKeys.has('KeyA')) - Number(heldKeys.has('KeyD'))) * step * yawSign;
  const pitch = (Number(heldKeys.has('KeyW')) - Number(heldKeys.has('KeyS'))) * step * pitchSign;
  if (!yaw && !pitch) {
    keyTimer = setTimeout(jogKeys, 150);
    return;
  }
  motionBusy = true;
  applyButton.disabled = true;
  document.querySelector('#center').disabled = true;
  updateKeyboardState();
  try {
    const response = await fetch('/api/jog', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yaw, pitch }), signal: AbortSignal.timeout(5000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '이동 실패');
    setCardValue(cards[0], result.angles[0]);
    setCardValue(cards[2], result.angles[2]);
    acknowledgedAngles = result.angles;
    message.className = '';
    message.textContent = `좌우 ${result.angles[0]}° · 상하 ${result.angles[2]}° 적용됨`;
  } catch (error) {
    keyboardReady = false;
    stopKeys();
    message.className = 'error';
    message.textContent = error.message;
  } finally {
    motionBusy = false;
    applyButton.disabled = false;
    document.querySelector('#center').disabled = false;
    if (heldKeys.size) keyTimer = setTimeout(jogKeys, 150);
    updateKeyboardState();
    updateAimControls();
  }
}

document.addEventListener('keydown', (event) => {
  if (event.ctrlKey || event.altKey || event.metaKey || event.isComposing) { stopKeys(); return; }
  if (trackingBusy) return;
  if (!['KeyW', 'KeyA', 'KeyS', 'KeyD'].includes(event.code) || !keyboardEnabled.checked || isEditing(event.target)) return;
  event.preventDefault();
  if (event.repeat || motionBusy && !heldKeys.size) return;
  if (!keyboardReady) {
    message.className = 'error';
    message.textContent = '추적 시작 또는 각도 적용으로 서보를 연결해 주세요';
    return;
  }
  heldKeys.add(event.code);
  if (!motionBusy && keyTimer === null) jogKeys();
});
document.addEventListener('keyup', (event) => {
  heldKeys.delete(event.code);
  if (!heldKeys.size) stopKeys();
});
window.addEventListener('blur', () => { stopKeys(); stopAim = true; });
document.addEventListener('visibilitychange', () => { if (document.hidden) { stopKeys(); stopAim = true; } });
document.addEventListener('focusin', (event) => { if (isEditing(event.target)) stopKeys(); });
keyboardEnabled.addEventListener('change', stopKeys);

const captureButton = document.querySelector('#capture');
const visionStatus = document.querySelector('#vision-status');
const cameraPhoto = document.querySelector('#camera-photo');
let capturing = false;

function updateAimControls() {
  const flagDisabled = !keyboardReady || trackingBusy || motionBusy || aiming || capturing;
  flagGoButton.disabled = flagDisabled;
  flagReturnButton.disabled = flagDisabled;
  aimButton.disabled = trackingBusy || !visionAvailable || capturing || motionBusy || aiming;
  aimStopButton.disabled = !trackingBusy;
  captureButton.disabled = trackingBusy || capturing || aiming || motionBusy;
  applyButton.disabled = trackingBusy || motionBusy || aiming || capturing;
  document.querySelector('#center').disabled = trackingBusy || motionBusy || aiming || capturing;
  document.querySelectorAll('.servo input, .servo button, #reverse-yaw, #reverse-pitch').forEach(
    input => { input.disabled = trackingBusy || aiming || motionBusy || capturing; });
  flagGoButton.disabled = flagDisabled;
  flagReturnButton.disabled = flagDisabled;
  window.dispatchEvent(new Event('controller-state'));
}

window.servoController = {
  get busy() { return motionBusy || capturing || aiming; },
  setTracking(busy, angles) {
    const wasBusy = trackingBusy;
    trackingBusy = busy;
    if (busy) stopKeys();
    if (busy && Array.isArray(angles)) {
      acknowledgedAngles = [...angles];
      keyboardReady = true;
      cards.forEach((card, index) => setCardValue(card, angles[index]));
      initialPositionSynced = true;
    }
    updateKeyboardState();
    updateAimControls();
    if (wasBusy && !busy) refreshStatus();
  },
};

async function centerTarget() {
  if (trackingBusy || capturing || motionBusy || !visionAvailable) return;
  await window.stepTracking.start();
}

function showDetection(result) {
  if (trackingBusy) return;
  document.querySelector('#snapshot-results').open = true;
  cameraPhoto.src = result.image_url;
  cameraPhoto.hidden = false;
  document.querySelector('#camera-placeholder').hidden = true;
  document.querySelector('#capture-time').textContent = `${(result.timing_ms.capture / 1000).toFixed(2)}초`;
  document.querySelector('#inference-time').textContent = `${(result.timing_ms.inference / 1000).toFixed(2)}초`;
  document.querySelector('#total-time').textContent = `${(result.timing_ms.total / 1000).toFixed(2)}초`;
  const table = document.querySelector('#detections');
  const body = table.querySelector('tbody');
  body.replaceChildren();
  result.detections.forEach((detection, index) => {
    const row = document.createElement('tr');
    const values = [index + 1, `${(detection.confidence * 100).toFixed(1)}%`, ...detection.center];
    values.forEach((value) => {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.append(cell);
    });
    body.append(row);
  });
  table.hidden = result.detections.length === 0;
  visionStatus.className = '';
  visionStatus.textContent = result.detections.length ? `표적 ${result.detections.length}개 검출됨` : '표적 미검출';
}

async function captureAndDetect() {
  if (trackingBusy || motionBusy || capturing || aiming) return;
  capturing = true;
  updateAimControls();
  captureButton.disabled = true;
  captureButton.textContent = '처리 중...';
  visionStatus.className = '';
  visionStatus.textContent = '촬영 · 검출 중';
  // Keep an earlier capture visibly separate from the pending result.
  cameraPhoto.classList.add('previous');
  try {
    const response = await fetch('/api/vision/capture', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '검출 실패');
    showDetection(result);
  } catch (error) {
    visionStatus.className = 'error';
    visionStatus.textContent = error.message;
  } finally {
    capturing = false;
    captureButton.disabled = false;
    captureButton.textContent = '한 장 촬영 · 검출';
    cameraPhoto.classList.remove('previous');
    updateAimControls();
  }
}

async function loadVisionState() {
  try {
    const response = await fetch('/api/vision', { cache: 'no-store' });
    if (!response.ok) throw new Error('카메라 상태 조회 실패');
    const state = await response.json();
    visionAvailable = state.model_available;
    if (state.result) showDetection(state.result);
    if (state.busy) {
      captureButton.disabled = true;
      visionStatus.textContent = '촬영 · 검출 중';
      setTimeout(loadVisionState, 1500);
    } else if (!capturing) {
      updateAimControls();
      if (state.error) throw new Error(state.error);
      if (!state.model_available) throw new Error(state.backend === 'desktop-pytorch' ? '데스크톱 추론 주소를 설정한 후 서버를 다시 시작하세요' : '모델 파일 없음');
    }
  } catch (error) {
    visionAvailable = false;
    updateAimControls();
    visionStatus.className = 'error';
    visionStatus.textContent = error.message;
  }
}

cards.forEach((card) => {
  card.querySelector('.angle').setAttribute('aria-label', `${card.querySelector('h2').textContent} 각도 숫자`);
});
captureButton.addEventListener('click', captureAndDetect);
aimButton.addEventListener('click', centerTarget);
aimStopButton.addEventListener('click', () => {
  window.stepTracking.stop();
});
loadVisionState();
