# 데스크톱 PyTorch + Raspberry Pi PNG 추론

Pi 카메라 → 1920×1080 PNG → 데스크톱 YOLO26m `.pt` → 검출 좌표 → Pi 중심 맞추기.
화면 중심은 `(960, 540)`입니다. 중앙 맞춤은 파란색 `1: ally`에만 적용합니다. 분홍색 `0: enemy`는 이동 대상에서 제외합니다. 현재 기본 동작은 **한 장 촬영 → 한 번 추론 → 보정값으로 이동 → 정지 대기 → 재촬영**입니다.
연속 영상 추론은 사용하지 않습니다. 기존 각도별 사진으로 측정한 픽셀·서보 보정값을 Pi에 저장했으며,
계산한 목표 각도로 한 번에 이동하고 새 사진으로 확인합니다. 촬영·추론 관측을 총 8회 완료한 후 중심 거리 오차가 가장 작았던 서보 위치로 복귀합니다. 중앙에 들어오거나 오차가 줄지 않아도 조기 종료하지 않습니다.
PNG 전송·응답은 최대 15초 기다립니다. 일시적 통신 오류는 새 사진으로 재시도하고
3회 연속 실패하면 중지합니다. 발사 서보 D9는 변경하지 않습니다.
이 변경은 Pi 쪽에 적용되므로 데스크톱 추론 서버는 재설치할 필요가 없습니다.

## 1. 데스크톱에 설치

Pi의 `http://100.116.119.120:8080/desktop-yolo.zip`을 데스크톱에서 내려받아
압축을 푸세요. 이 주소는 Pi의 Tailscale 주소입니다.
Python 3.11 또는 3.12를 사용합니다. 명령은 압축을 푼 폴더에서 실행합니다.

현재 VS Code의 Remote SSH 터미널은 Linux Pi에서 실행됩니다.
아래 데스크톱 설치 명령은 추론할 Windows PC의 **로컬 PowerShell**에서 실행하세요.

확인된 Tailscale 장치:

| 장치 | 역할/OS | Tailscale IP |
|---|---|---|
| kimkangjo | Raspberry Pi / Linux | 100.116.119.120 |
| kangjunmo | 현재 VS Code SSH 접속 PC / Windows | 100.91.87.119 |
| DESKTOP-LQ1AGO2 | 추가 PC / Windows | 100.78.139.31 |

실제 추론 PC는 **kangjunmo (100.91.87.119)**입니다. 실행된 호스트 이름과 Tailscale IP로 확인했습니다.

## 빠른 실행 (선택한 Windows 데스크톱)

ZIP을 푼 폴더의 로컬 PowerShell에서 실행합니다. Python 3.12와 NVIDIA 드라이버가 필요합니다.
처음 실행하면 별도 가상환경에 PyTorch와 Ultralytics를 설치합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\start-desktop.ps1
```

`Ready`가 표시되면 해당 창을 켜 둡니다. Pi 서비스에는 이미
`http://100.91.87.119:8090` 주소를 설정했습니다.
CPU 실행은 위 명령 끝에 `-Cpu`를 추가합니다.

아래는 수동 설치 방법입니다.

Windows PowerShell (NVIDIA CUDA 12.8):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe desktop_inference.py --device 0
```

Linux (NVIDIA CUDA 12.8):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
.venv/bin/python -m pip install -r requirements-desktop.txt
.venv/bin/python desktop_inference.py --device 0
```

GPU가 없는 환경에서는 설치 URL을 `https://download.pytorch.org/whl/cpu`로,
실행 옵션을 `--device cpu`로 바꿉니다. CPU는 15초 응답 제한을 초과할 수 있습니다.
`--device auto`는 CUDA가 있으면 GPU, 없으면 CPU를 선택합니다.
GPU 사용 여부는 시작 로그와 `/health`의 `device`로 확인합니다.
추론 서버 PC의 방화벽과 Tailscale 접근 정책에서 Pi(`100.116.119.120`)가
TCP 8090에 접근할 수 있어야 합니다. 공유기 포트포워딩은 필요하지 않습니다.

서버는 처음에 모델을 로드하고 1920×1080 워밍업을 한 뒤 `Ready`를 출력합니다.
브라우저에서 `http://localhost:8090/health`를 열어 `ready: true`를 확인하세요.

설치 명령 근거: [PyTorch 공식 버전별 설치 안내](https://pytorch.org/get-started/previous-versions/).
추론 설정: [Ultralytics Predict](https://docs.ultralytics.com/modes/predict/).

## WinError 10049가 나타나는 경우

실행 중인 Windows에 할당되지 않은 주소로 서버를 열려고 할 때 발생합니다.
현재 스크립트는 특정 PC 주소를 고정하지 않고 `0.0.0.0`에서 수신합니다.
이전 ZIP을 받았다면 다시 설치할 필요 없이 압축을 푼 폴더에서 실행하세요.

```powershell
hostname
tailscale ip -4
.\.desktop-venv\Scripts\python.exe desktop_inference.py --host 0.0.0.0 --device 0
```

`tailscale ip -4`가 `100.91.87.119`과 다르면 Pi의 추론 주소도 실제 실행 PC에 맞춰야 합니다.
`0.0.0.0`은 서버의 수신 설정이며 Pi가 요청을 보낼 주소로 사용하지 않습니다.

## 2. Pi에서 연결

아래 주소는 선택한 데스크톱의 Tailscale IP입니다.
Windows는 `ipconfig`, Linux는 `hostname -I`로 확인할 수 있습니다.

```bash
cd /home/kimkangjo/servo-control
curl http://100.91.87.119:8090/health
# 서비스가 8080에서 실행 중이면 먼저 중지
systemctl --user stop servo-control.service
.venv/bin/python server.py --inference-url http://100.91.87.119:8090
```

Pi의 Picamera2는 OS 패키지(`python3-picamera2`)를 사용합니다.
기존 `.venv`는 `--system-site-packages`로 생성되어 있습니다.
Pi에는 데스크톱용 PyTorch 패키지를 설치할 필요가 없습니다.

서비스로 유지하려면 `systemctl --user edit servo-control.service`에 다음을 저장합니다.

```ini
[Service]
Environment=YOLO_INFERENCE_URL=http://100.91.87.119:8090
```

```bash
systemctl --user daemon-reload
systemctl --user restart servo-control.service
```

## 3. 사용

`http://PI_IP:8080` 한 페이지에서 각도 조절, 촬영, 중심 맞추기, 연속 추적을 모두 사용합니다.
**추적 시작** 또는 **Space**를 누르면 서보를 자동 연결하고 추적합니다. 먼저 각도 적용을 누를 필요가 없습니다.
처음 연결할 때 각도가 알려져 있지 않으면 90°로 초기화하며, 이미 적용된 각도가 있으면 유지합니다.
다시 Space 또는 추적 중지를 누르면 수동 조절이 가능합니다. WASD는 수동 이동 키입니다.
기존 `/tracker.html` 주소는 통합 화면으로 이동합니다. 브라우저를 새로고침하세요.
단발 사진·원본 저장·데스크톱 추론 전송은 모두 1920×1080 PNG입니다.
브라우저 미리보기만 960×540 JPEG로 보내 전송량을 줄입니다.
Picamera2는 RGB 원본을 PNG로 인코딩하며 JPEG를 거치지 않습니다.

모델 클래스는 `0=enemy`, `1=ally`입니다. 기본값은 둘 다 검출합니다.
단발 중심 맞추기는 화면 중심/이전 표적에 가까운 것을, 연속 추적은 신뢰도 1위를 선택합니다.
특정 클래스만 필요하면 데스크톱 실행에 `--classes 0` 또는 `--classes 1`을 추가합니다.

서보를 움직이지 않고 이미지 한 장의 전송만 확인하려면:

```bash
.venv/bin/python remote_inference.py http://100.91.87.119:8090 /path/to/1920x1080.png
```

PNG는 JPEG보다 전송량이 큽니다. 카메라는 10 FPS를 요청하지만 실제 추적 FPS는
PNG 인코딩·네트워크·GPU 성능에 따라 달라집니다. 화면의 처리 시간은 통신을 포함합니다.

## 모델 출처와 확인

- 저장소: https://github.com/kang-jun-mo12/yolo-target-dataset
- 커밋: `5f9d70f10196a66ec1b5b7d1d953587942ade8a4`
- 경로: `models/yolo26m_1920_90_5_5/best.pt`
- 로컬 파일: `models/yolo26m_1920_best.pt` (44,244,505 bytes)
- SHA-256: `8db6b40e92e3b4ee747e8a6c2aff38b68867c1d703486c1812fe93f792a4d2b6`
- 학습 버전에 맞춘 Ultralytics: `8.4.165`

원본과 SHA-256 체크섬이 일치합니다. 관련 테스트 43개와 통합 화면/키보드 브라우저 테스트를 통과했습니다.
Pi 서비스의 추론 주소는 `http://100.91.87.119:8090`이며 실제 데스크톱 GPU 장치 `0`에 연결했습니다.

실제 카메라 단발 촬영과 연속 PNG 3장의 전송·추론을 확인했습니다.
연속 검증에서 각각 카메라 프레임 1, 8, 15를 처리해 중간 프레임을 쌓지 않고 최신 프레임을 선택했습니다.
각 PNG는 약 3MB이며 전송 포함 왕복은 3.48~3.62초, 서버 추론은 44~158ms였습니다.
모든 요청에서 두 개의 표적이 검출됐습니다. 현재 속도 제한은 주로 이미지 전송에 있습니다.
검증 중 서보는 움직이지 않았습니다. 실제 기구 중심 맞추기는 통합 화면에서 추적 시작을 누르면 자동 초기화 후 시작합니다.

## Tailscale 전송 지연 수정

데스크톱 `/health`는 정상이지만 PNG 왕복이 6.15초 걸려 기존 5초 제한을 넘는 경우가 확인됐습니다.
응답 대기를 15초로 변경하고 통신 오류 시 최신 프레임으로 재시도합니다.
브라우저 미리보기는 샘플 기준 2,993,530 bytes PNG에서 53,025 bytes JPEG로 줄였습니다.
데스크톱 추론용 이미지는 원본 1920×1080 PNG를 유지합니다.
느린 추론 시간을 카메라 미수신 시간으로 잘못 계산하던 문제도 수정했습니다.
이 변경은 Pi 서비스에 적용되므로 이미 실행 중인 데스크톱 서버를 다시 설치할 필요가 없습니다.

## YOLO26l-OBB 교체 준비 (2026-10-08)

사용자가 지정한 학습 결과 경로는 `C:\yolo\yolo26l_obb_dataset_2856\runs\yolo26l_obb_1920x1088`입니다.
`start-obb.ps1`은 해당 폴더의 `weights\best.pt`를 선택하고 경로·수정 시각·SHA256을 출력합니다.
`--expected-task obb --require-blue-target`으로 OBB 모델인지, 기존 파랑 클래스 `1=ally`가 맞는지 확인한 후 시작합니다.
새 가중치 파일 및 실제 추론은 Windows에서 스크립트 실행 후 확인해야 합니다.

데스크톱용 업데이트: `http://100.116.119.120:8080/obb-inference-update.zip`.
부트스트랩: `http://100.116.119.120:8080/install-obb.ps1`.
OBB 회전 꼭짓점은 `obb_corners`로 전달하며, 카메라 이동에는 꼭짓점의 평균 중심을 사용합니다.
이미지 가장자리에서 미리보기용 사각 박스를 잘라도 실제 OBB 중심은 유지합니다.
일반 detect 모델도 계속 지원합니다. 가중치 교체 완료 여부는 `/health`의 `task`, `class_names`, `model_sha256`로 확인합니다.
