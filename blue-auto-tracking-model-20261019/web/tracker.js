(() => {
  const toggle = document.querySelector('#tracking-toggle');
  const statusText = document.querySelector('#tracking-status');
  const cameraState = document.querySelector('#camera-state');
  const cameraImage = document.querySelector('#camera-photo');
  const targetBox = document.querySelector('#target-box');
  const controller = window.servoController;
  let active = false;
  let pending = false;
  let leaving = false;
  let generation = 0;
  let lastFrame = 0;
  let imageUrl = null;
  let latestAngles = null;
  let availableFrame = 0;
  let showTrackerFrames = false;
  let firstState = true;

  async function post(path, payload = {}) {
    const response = await fetch(path, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '서버 요청 실패');
    return data;
  }

  function updateToggle() {
    toggle.disabled = pending || (!active && controller.busy);
  }

  function syncControls() {
    controller.setTracking(active || pending, latestAngles);
    updateToggle();
  }

  function render(state) {
    active = state.active;
    availableFrame = state.frame_seq || 0;
    if (active || (firstState && availableFrame)) showTrackerFrames = true;
    firstState = false;
    latestAngles = state.angles;
    const indicator = document.querySelector('#tracking-indicator');
    indicator.className = `tracking-indicator ${active ? 'active' : ''}`;
    indicator.textContent = active ? '자동 실행 중' : state.phase === 'completed' ? '보정 완료' : '대기';
    toggle.querySelector('span').textContent = active ? '중지' : '자동 실행 시작';
    const labels = {
      idle: '자동 실행 시작 또는 Space를 누르세요. 4회 추적과 거리 측정 후 최종 좌우·상하 보정까지 진행합니다.',
      starting: '서보 자동 연결 · 카메라 준비 중…',
      capturing: '현재 각도에서 새 사진을 촬영 중입니다.',
      observing: `현재 위치를 유지하며 다음 관측을 진행합니다 (${state.observations ?? 0}/${state.total_observations ?? 4}회).`,
      completed: state.offset_applied
        ? '좌우·상하 최종 서보 보정을 완료했습니다. 현재 각도를 유지합니다.'
        : `총 ${state.total_observations ?? 4}회 관측·보정을 마쳤습니다.`,
      measuring_distance: '마지막 추적 박스 크기를 학습 모델로 분석합니다.',
      distance_unreliable: state.distance_error || '거리를 확정하지 못해 최종 각도 보정을 진행하지 않았습니다.',
      return_failed: state.return_error || '최소 오차 위치로 복귀한 것을 확인하지 못해 후속 동작을 중지했습니다.',
      applying_offset: '측정표로 계산한 좌우·상하 추가 보정 각도로 이동합니다.',
      applying_yaw_offset: '최종 보정: 좌우 각도를 먼저 적용합니다.',
      offset_yaw_wait: '좌우 보정 완료 · 1초 대기 후 상하 각도를 적용합니다.',
      applying_pitch_offset: '최종 보정: 상하 각도를 적용합니다.',
      offset_settling: '좌우·상하 보정 완료 · 안정화 후 추적을 종료합니다.',
      detecting: '이 사진 한 장을 데스크톱에서 검출 중입니다.',
      settling: '계산한 각도로 이동했습니다. 진동이 멈추면 다시 촬영합니다.',
      searching: '표적을 찾는 중입니다.',
      reconnecting: `전송 지연으로 다시 연결 중입니다 (${state.retry_count || 1}/3). 현재 각도를 유지합니다.`,
      no_target: `${state.total_observations ?? 4}회 관측에서 파란색 표적을 확인하지 못했습니다.`,
      target_lost: '예상 위치에서 같은 파란색 표적을 확인하지 못해 종료했습니다.',
      tracking: '최고 신뢰도 표적을 추적 중입니다.',
      moving: '표적 중심으로 각도를 보정했습니다.',
      returning_to_best: '4회 중 최소 오차를 기록한 각도로 한 번 복귀합니다.',
      centered: '중앙 맞춤 완료 · 가로·세로 오차 각각 3px 이내, 중심 거리 4px 이내입니다.',
      direction_error: '중심에서 멀어져 멈췄습니다. 카메라 장착 상태와 보정 자료를 확인하세요.',
      limit: '측정된 각도 범위의 한계에 도달했습니다.',
      resolution_limit: '오차가 남았지만 0.1° 단위로 더 움직이면 개선되지 않아 멈췄습니다.',
      max_steps: `추가 보정 ${state.max_corrections ?? 8}회와 최종 확인 촬영을 마쳤습니다. 남은 오차를 확인하세요.`,
      stalled: `${state.max_stalled_observations ?? 8}회 연속 중심 오차가 줄지 않아 보정을 마쳤습니다.`,
      stopping: '추적을 중지하는 중…', stopped: '추적을 중지했습니다. 수동 조절이 가능합니다.',
      timeout: '브라우저 연결이 끊겨 추적을 종료했습니다.',
      error: state.error || '추적 오류가 발생했습니다.',
    };
    statusText.className = ['error', 'direction_error', 'limit', 'distance_unreliable', 'return_failed'].includes(state.phase) ? 'error' : '';
    statusText.textContent = labels[state.phase] || state.phase;
    if (state.returned_to_best) {
      statusText.textContent += ` 관측 중 최소 거리 ${state.best_distance_px} px.`;
      if (state.offset_applied) {
        statusText.textContent += ` ${state.distance_source_observation}번째 추적 박스 크기로 거리를 계산했습니다.`;
      } else if (state.distance_observations) {
        if (state.return_verified) statusText.textContent += ` 복귀 후 확인한 중심 오차 ${state.final_distance_px} px.`;
      } else {
        statusText.textContent += state.return_verified
          ? ` 마지막 촬영에서 확인한 현재 거리 ${state.final_distance_px} px.`
          : ' 최소 오차 각도로 복귀 명령을 보냈습니다. 복귀 후 위치는 재촬영하지 않았습니다.';
      }
    }
    document.querySelector('#aim-status').textContent = statusText.textContent;
    const width = state.width || 1920, height = state.height || 1080;
    cameraState.textContent = `추론 ${width} × ${height} PNG${active ? ` · 프레임 ${state.frame_seq}` : ''}`;
    if (state.preview_stage === 'returning') {
      cameraState.textContent += ' · 복귀 이동 중 (이동 전 사진)';
    } else if (state.preview_stage === 'before_offset') {
      cameraState.textContent += ' · 마지막 추적 사진 (복귀·최종 보정 전)';
    } else if (state.preview_stage === 'distance') {
      cameraState.textContent += ' · 복귀 후 거리 확인 사진';
    } else if (state.returned_to_best) {
      cameraState.textContent += state.return_verified
        ? ' · 현재 위치의 마지막 확인 사진' : ' · 마지막 추적 사진 (복귀 전)';
    }
    document.querySelector('.camera-frame').style.aspectRatio = `${width} / ${height}`;
    document.querySelector('#detection-count').textContent = state.eligible_detections != null
      ? `파랑 ${state.eligible_detections} / 전체 ${state.detections ?? 0}` : state.detections ?? '-';
    document.querySelector('#confidence').textContent = state.target ? `${state.target.class_name || 'target'} · ${(state.target.confidence * 100).toFixed(1)}%` : '-';
    document.querySelector('#target-center').textContent = state.target ? state.target.center.join(', ') : '-';
    const box = state.target?.box;
    let boxSize = '-';
    if (Array.isArray(box) && box.length === 4 && box.every(Number.isFinite)) {
      const boxWidth = box[2] - box[0], boxHeight = box[3] - box[1];
      if (boxWidth > 0 && boxHeight > 0) {
        boxSize = `${boxWidth.toFixed(1)} × ${boxHeight.toFixed(1)} px`;
      }
    }
    document.querySelector('#target-box-size').textContent = boxSize;
    const measurement = state.distance_measurement;
    document.querySelector('#distance-progress').textContent = state.distance_source_observation ? `${state.distance_source_observation}번째 추적 박스 사용` : '추적 박스 대기';
    document.querySelector('#distance-box-size').textContent = measurement
      ? `${measurement.box_size_px.map(v => v.toFixed(1)).join(' × ')} px · ${measurement.sample_count}장 사용` : '-';
    document.querySelector('#estimated-distance').textContent = measurement
      ? `약 ${measurement.distance_m.toFixed(2)} m${measurement.range_clamped ? ' (측정표 경계값)' : ''}` : '-';
    const signed = value => `${value > 0 ? '+' : ''}${value}°`;
    document.querySelector('#distance-offset').textContent = measurement
      ? `${signed(measurement.yaw_offset_deg)} / ${signed(measurement.pitch_offset_deg)}${state.offset_applied ? ' · 적용' : state.yaw_offset_applied ? ' · 좌우만 적용' : ''}` : '-';
    document.querySelector('#final-offset-status').textContent = state.phase === 'completed' && state.offset_applied
      ? '완료 · 최종 각도 유지'
      : state.offset_applied ? '좌우·상하 적용됨'
      : state.yaw_offset_applied ? '좌우 적용됨'
      : state.phase === 'distance_unreliable' ? '거리 확인 실패 · 미적용' : '대기';
    document.querySelector('#best-servo-angles').textContent = state.best_angles
      ? `${state.best_angles[0]}° / ${state.best_angles[2]}°` : '-';
    document.querySelector('#return-check').textContent = state.return_angles_applied
      ? `각도 복귀 완료${state.return_distance_px != null ? ` · 촬영 오차 ${state.return_distance_px} px` : ''}`
      : state.phase === 'return_failed' ? `실패 · ${state.return_distance_px} px`
      : state.return_attempt ? '최소 오차 각도로 이동 중' : '대기';
    document.querySelector('#center-error').textContent = state.error_px ? `${state.error_px.join(', ')} px` : '-';
    document.querySelector('#servo-angles').textContent = state.angles ? `${state.angles[0]}° / ${state.angles[2]}°` : '-';
    document.querySelector('#live-inference-time').textContent = state.inference_ms != null ? `${state.inference_ms} ms` : '-';
    document.querySelector('#move-count').textContent = `관측 ${state.observations ?? 0}/${state.total_observations ?? 4}회 · 보정 이동 ${state.moves ?? 0}회`;
    document.querySelector('#predicted-center').textContent = state.predicted_center ? state.predicted_center.join(', ') : '-';
    if (state.target && state.processed_seq === state.frame_seq &&
        lastFrame === state.processed_seq && cameraImage.src === imageUrl && !controller.busy) {
      const [x1, y1, x2, y2] = state.target.box;
      targetBox.style.left = `${x1 / width * 100}%`;
      targetBox.style.top = `${y1 / height * 100}%`;
      targetBox.style.width = `${(x2 - x1) / width * 100}%`;
      targetBox.style.height = `${(y2 - y1) / height * 100}%`;
      document.querySelector('#target-label').textContent = `${state.target.class_name || 'target'} ${(state.target.confidence * 100).toFixed(1)}%`;
      targetBox.hidden = false;
    } else {
      targetBox.hidden = true;
    }
    syncControls();
  }

  async function pollStatus() {
    const requestedGeneration = generation;
    try {
      const response = await fetch('/api/tracker/status', {cache: 'no-store'});
      if (!response.ok) throw new Error('추적 상태를 확인할 수 없습니다');
      const state = await response.json();
      if (requestedGeneration === generation && !pending) {
        render(state);
      }
    } catch (error) {
      statusText.className = 'error';
      statusText.textContent = error.message;
    } finally {
      setTimeout(pollStatus, active ? 500 : 1200);
    }
  }

  async function pollFrame() {
    const requestedGeneration = generation;
    try {
      if (showTrackerFrames && (active || availableFrame > lastFrame) && !document.hidden && !controller.busy) {
        const response = await fetch(`/api/tracker/frame?after=${lastFrame}`, {cache: 'no-store'});
        if (response.status === 200) {
          const frame = await response.blob();
          if (showTrackerFrames && requestedGeneration === generation) {
            lastFrame = Number(response.headers.get('X-Frame-Seq'));
            const oldUrl = imageUrl;
            imageUrl = URL.createObjectURL(frame);
            cameraImage.src = imageUrl;
            cameraImage.hidden = false;
            cameraImage.classList.remove('previous');
            document.querySelector('#camera-placeholder').hidden = true;
            if (oldUrl) setTimeout(() => URL.revokeObjectURL(oldUrl), 1000);
          }
        }
      }
    } catch {
      cameraState.textContent = '카메라 재연결 중';
    } finally {
      setTimeout(pollFrame, active ? 500 : 1000);
    }
  }

  async function toggleTracking() {
    if (pending || (!active && controller.busy)) return;
    pending = true;
    if (!active) latestAngles = null;
    generation++;
    syncControls();
    try {
      if (active) {
        render(await post('/api/tracker/stop'));
      } else {
        leaving = false;
        lastFrame = 0;
        showTrackerFrames = true;
        render(await post('/api/tracker/start', {
          reverse_yaw: document.querySelector('#reverse-yaw').checked,
          reverse_pitch: document.querySelector('#reverse-pitch').checked,
        }));
        if (leaving || document.hidden) render(await post('/api/tracker/stop'));
      }
    } catch (error) {
      statusText.className = 'error';
      statusText.textContent = error.message;
    } finally {
      pending = false;
      syncControls();
    }
  }

  function stopOnExit() {
    leaving = true;
    generation++;
    if (active || pending) {
      navigator.sendBeacon('/api/tracker/stop', new Blob(['{}'], {type: 'application/json'}));
    }
  }

  window.stepTracking = {
    start: () => { if (!active && !pending) return toggleTracking(); },
    stop: () => {
      if (pending) return stopOnExit();
      if (active) return toggleTracking();
    },
  };

  async function showCalibration() {
    const label = document.querySelector('#calibration-status');
    try {
      const response = await fetch('/api/calibration', {cache: 'no-store'});
      if (!response.ok) throw new Error('보정 상태 조회 실패');
      const state = await response.json();
      label.textContent = state.valid
        ? '현재 실행의 이동 반응 보정 · 11개 거리 측정값 보간 · 0.1° 조절'
        : '보정 자료 검증이 필요합니다. 자동 이동은 시작되지 않습니다.';
    } catch (error) { label.textContent = error.message; }
  }

  toggle.addEventListener('click', toggleTracking);
  document.querySelector('#capture').addEventListener('click', () => {
    if (active || pending) return;
    showTrackerFrames = false;
    generation++;
    targetBox.hidden = true;
  });
  window.addEventListener('controller-state', updateToggle);
  document.addEventListener('keydown', event => {
    if (event.code !== 'Space' || event.repeat || event.isComposing ||
        event.ctrlKey || event.altKey || event.metaKey ||
        event.target?.closest?.('input, textarea, select, button, a, [contenteditable], [role="textbox"]')) return;
    event.preventDefault();
    toggleTracking();
  });
  window.addEventListener('pagehide', stopOnExit);
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopOnExit(); });
  setInterval(async () => {
    if (!active || leaving || document.hidden) return;
    try { await post('/api/tracker/heartbeat'); } catch { /* Server lease stops tracking. */ }
  }, 2000);
  pollStatus();
  pollFrame();
  showCalibration();
})();
