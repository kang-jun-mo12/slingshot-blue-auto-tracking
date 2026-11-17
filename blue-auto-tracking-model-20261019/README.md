# 파랑 자동추적 — 학습 모델 적용 완성본

2026-10-19 패키지. 거리·상하 최종 보정 계산을 2026-10-14 학습한 가우시안 프로세스 모델로 교체했습니다.

## 실행 흐름

1. YOLO26l-OBB로 파란 ally 표적을 검출합니다.
2. 기존 가운데 맞추기 모델로 총 4회 관측하며 보정합니다.
3. 관측 중 중심 오차가 가장 작았던 서보 각도로 한 번 복귀합니다.
4. 이번 추적에서 마지막으로 검출된 박스 크기를 학습 모델에 입력합니다. 추가 사진은 촬영하지 않습니다.
5. 학습 모델로 거리와 상하 보정량을 예측합니다.
6. 좌우 −2° 적용 → 1초 대기 → 예측한 상하 보정 적용 → 안정화 후 종료합니다.

D9 깃발 각도는 유지하며 Go & Return은 기존 단독 버튼으로 실행합니다.

## 학습 모델 구성

- `learned_distance.py`: 운영용 NumPy 추론 및 입력 검사.
- `calibration/distance-model.json`: 실제 학습된 계수·정규화 값. 운영 서버가 읽는 모델입니다.
- `flag_cycle.py`: 학습 모델을 기본으로 호출하는 자동추적 흐름. workflow version 8.
- `calibration/flag-distance.json`: 기존 실측 범위·박스 비율 검사 및 사용자가 정한 추가 보정 설정.
- `calibration/distance-learning-20261014/`: 원래 학습 체크포인트, 학습 자료 111개, 검증 보고서.

거리와 상하 보정값은 학습 모델이 계산합니다. 실측표는 입력의 크기·비율·범위 검증에만 사용하며, 오류 시 표 방식으로 몰래 전환하지 않습니다. 학습 시 사용한 원래 상하 값에 현재 설정의 상하 trim −1°를 한 번 더합니다. 좌우는 사용자 설정인 −2°입니다.

기존 1.50~2.00m 범위 검사, 끝점 크기 2% 오차 허용과 경계값 제한, 박스 비율 검사, 서보 0~180° 제한, 중지 및 브라우저 연결 확인을 유지했습니다. 학습 계수 누락이나 측정표 불일치는 명확한 오류로 중단합니다.

운영 추론은 NumPy만 필요합니다. 학습에 사용한 scikit-learn·SciPy·joblib은 운영 서버에 필요하지 않습니다. 재학습 도구를 사용할 때만 `requirements-distance-learning.txt`를 별도 환경에 설치합니다.

## 라즈베리파이 설치 및 실행

압축을 푼 폴더에서 실행합니다. 기존 카메라·Arduino 연결 및 Tailscale 구성을 사용합니다.

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
bash start-pi.sh
```

기본 브라우저 주소는 `http://라즈베리파이주소:8080/#tracking`입니다. 기존 서버와 함께 시험할 때는 다른 포트를 지정합니다.

```bash
PORT=8081 bash start-pi.sh
```

기본 추론 PC 주소는 `http://100.91.87.119:8090`입니다. 변경할 때는 다음처럼 실행합니다.

```bash
YOLO_INFERENCE_URL=http://추론PC주소:8090 bash start-pi.sh
```

Arduino 포트를 직접 지정할 수도 있습니다.

```bash
bash start-pi.sh --serial-port /dev/ttyUSB0
```

## Windows YOLO 추론 PC

기존에 학습한 YOLO26l-OBB 체크포인트는 Windows PC의 다음 경로를 사용합니다.

`C:\yolo\yolo26l_obb_dataset_2856\runs\yolo26l_obb_1920x1088\weights\best.pt`

`start-obb.ps1`을 실행합니다. 경로가 다르면 `-Model` 인자로 지정합니다. 모델 파일이 없을 때 다른 사전학습 가중치를 자동으로 내려받아 대체하지 않습니다. 이 ZIP에는 거리·보정 학습 모델이 포함되어 있으며, 해당 Windows PC에 보관된 YOLO 체크포인트 자체는 포함하지 않았습니다. 상세 설치는 `DESKTOP_SETUP.md`를 참고하세요.

## 검증과 자료

- 운영 가상환경으로 자동추적·복귀·최종 보정·중지·각도 제한 등을 포함한 109개 테스트 통과.
- `test_learned_distance.py`에서 기존 표 계산기를 호출할 수 없게 막아도 자동추적이 학습 모델로 완료되는 것을 확인.
- 변환된 NumPy 모델을 원래 학습 모델과 122개 입력에서 비교: 거리 최대 차이 8.45e-13m, 상하 최대 차이 1.48e-11°.
- `validation/numpy-export-parity.json`: 변환 검증 결과.
- `validation/archived-image-comparison.json`: 저장 사진 비교 결과. 최종 모델 학습에 쓰지 않은 562장 기준 거리 평균 차이 약 0.074cm, 상하 명령 최대 차이 0.2°.

위 사진 비교는 기존 계산과의 차이입니다. 실제 장치에서 학습 모델을 적용한 성능은 아직 별도로 측정하지 않았습니다. 본 패키지를 만드는 과정에서 실제 서보를 움직이거나 기존 실행 서비스를 재시작하지 않았습니다.

```bash
.venv/bin/python -m unittest test_learned_distance test_flag_cycle test_flag test_calibration test_tracking_feedback test_live_tracker test_return_to_best test_servo_angles
.venv/bin/python test_live_browser.py
```

촬영 원본 수 GB, 가상환경, 과거 백업, 운영 로그는 제외했습니다. 실행에 필요한 코드, 웹 화면, Arduino 펌웨어, 중심 보정 모델, 학습된 거리·보정 모델과 검증 자료는 포함했습니다. `MANIFEST.json`에는 패키지 파일들의 SHA256이 있습니다.
