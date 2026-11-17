# 고무줄 총 자동화 시스템 — 파랑 자동추적 최종본 (2026-10-19)

YOLO 표적 검출 → 가운데 맞추기(센터링) → 학습 모델 기반 거리·상하 보정으로 파란 표적을 자동 조준하는 시스템입니다.

## 폴더 구성

| 경로 | 내용 |
|---|---|
| `blue-auto-tracking-model-20261019/` | 실행 코드 전체 (라즈베리파이 서버, Windows 추론 PC, 아두이노 펌웨어, 테스트) |
| `blue-auto-tracking-model-20261019/models/yolo26l_obb_1920x1088/best.pt` | YOLO26l-OBB 학습 가중치 |
| `blue-auto-tracking-model-20261019/calibration/distance-model.json` | 거리·상하 보정 학습 모델 (가우시안 프로세스) |
| `blue-auto-tracking-model-20261019/calibration/distance-learning-20261014/` | 학습 체크포인트·학습 자료·검증 보고서 |
| `architecture_images/` | 시스템·YOLO·센터링·상하 보정 모듈 구조도 |
| `document_work/` | 보고서 작성용 스크립트와 중간 산출물 |
| `5조 고무줄 총 자동화 시스템 개발 보고서.docx` / `.md` | 개발 보고서 |
| `파랑_자동추적_개발보고서.hwpx` | 개발 보고서 (한글) |

설치·실행 방법은 [`blue-auto-tracking-model-20261019/README.md`](blue-auto-tracking-model-20261019/README.md)를 참고하세요.
