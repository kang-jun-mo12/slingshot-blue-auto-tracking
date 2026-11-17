import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const work = path.dirname(fileURLToPath(import.meta.url));
const root = path.dirname(work);
const before = fs.readFileSync(path.join(work, 'report_before_concision.md'), 'utf8').replaceAll('\r\n', '\n');
const current = fs.readFileSync(path.join(root, '파랑_자동추적_개발보고서.md'), 'utf8');
if (!fs.existsSync(path.join(work, 'report_over_condensed_backup.md'))) fs.writeFileSync(path.join(work, 'report_over_condensed_backup.md'), current);
let blocks = before.trim().split(/\n\n+/);
const omit = [
  '**영상 중심을 이용한 방향 정렬:**',
  '**중심 정렬과 상하 보정량 예측의 구분:**',
  '**모델 설계에 대한 의미:**',
  '논의의 핵심은 중간 처리 단계를',
  '이 방식은 방법 1보다 표적의 위치를',
  'YOLO가 추출한 표적 중심을 카메라',
  '카메라가 막대 아래에서 같은 방향을',
  '최종 시스템은 YOLO26l-OBB로 표적을 검출하고',
  '| 방법 | 시도한 구성 | 비교 실험 구성 |',
  '각 방법을 15회씩 시도한 결과를 다음과 같이',
  '카메라와 막대의 방향을 맞춘 하드웨어는',
  '보고서의 세 모델은 기능에 따른 구분이다.',
  '그림 3은 YOLO26l-OBB의 특징 추출부,',
  '카메라 화면 중심은 막대와 같은 방향을',
  'CAM은 이 오차를 줄이는 서보 각도를 계산한다.',
  '실제 계산에는 감쇠와 이동량 제한을 적용한다.',
  '중심 판단 기준은 축별 오차',
  '```text\n모델 1 YOLO:',
  '최종 적용 모델은 방법 3이다. 그림 2는',
  '그림 5는 두 단계 GP 예측 결과가',
  'DPCM은 중심 맞춤을 마친 다음 위로',
  '카메라가 막대 아래에 있으므로 두 기준 사이에는',
];
for (const prefix of omit) {
  const found = blocks.filter(b => b.startsWith(prefix));
  if (found.length !== 1) throw new Error(`Expected one paragraph: ${prefix} (${found.length})`);
  blocks = blocks.filter(b => !b.startsWith(prefix));
}
const replace = [
  ['본 과제는 카메라 이미지에서', '본 과제는 카메라로 파랑 enemy 표적을 찾아 방향을 정렬하고, 거리별 상하 보정 후 고무줄을 발사하는 시스템을 개발하는 것이다. 이미지에서 서보 각도를 직접 예측하는 두 방식과, 검출·중심 정렬·상하 보정을 분리하는 방식을 비교했다. 최종 시스템은 **YOLO 검출 모델 + 중심 정렬 모듈(CAM, Center Alignment Module) + 거리 기반 상하 보정 모듈(DPCM, Distance-based Pitch Correction Module)**로 구성했다.'],
  ['**구동부와 카메라:**', '**구동부와 카메라:** 방향 조절 서보가 움직이면 막대 아래의 카메라도 함께 움직인다. 화면 중심이 바라보는 방향과 막대의 지향 방향을 맞춰, 영상 중심을 기구의 방향 정렬 기준으로 사용한다. 라즈베리파이가 촬영한 영상을 PC로 전송하면 YOLO와 보정 모듈이 서보 목표 각도를 계산한다.'],
  ['학습에는 이미지와 그 이미지에 대응하는', '학습에는 이미지와 올바른 서보 각도의 쌍이 필요하다. 동일한 픽셀 위치라도 촬영 시작 자세에 따라 목표 절대각이 달라질 수 있으므로, 현재 서보 자세를 입력하거나 시작 자세를 일정하게 맞춰야 한다.'],
  ['여기서 마스킹은 검출 박스를 기준으로', '마스킹은 YOLO의 검출 박스 밖을 가리는 전처리다. YOLO가 표적을 찾고, CNN은 배경을 제거한 이미지에서 필요한 서보 각도를 학습한다.'],
  ['전처리에서는 표적의 원래 위치와 크기를', '전처리에서는 표적의 원래 위치와 크기를 유지해야 한다. 검출 영역만 잘라 동일한 크기로 확대하면 중심 오차와 크기 정보가 사라질 수 있으므로, 원본 영상에서 배경을 가리는 방식으로 구성한다.'],
  ['논의한 세 방법을 각각 구성해 실제 장치에서', '논의한 세 방법을 모두 실제 장치에 적용해 비교했다. 방법 1은 전체 이미지에서, 방법 2는 YOLO 검출 후 마스킹한 이미지에서 서보 각도를 예측했다. 방법 3은 검출·중심 정렬·상하 보정을 분리해 연결했다. 실험 조건과 결과는 4절에 정리했다.'],
  ['실패 시 평균 거리는 실패한 시도만을', '실패 시 평균 거리는 실패한 시도만을 대상으로 계산했다. 성공 여부와 함께 실패했을 때 표적에 얼마나 가깝게 도달했는지를 평가했다.'],
  ['YOLO 결과는 두 갈래로 전달된다.', 'YOLO가 검출한 표적 중심점은 CAM으로, 박스의 폭·높이는 DPCM으로 전달한다. CAM이 방향을 정렬하면 DPCM이 기준 자세에 상하 보정량을 반영하고 발사 서보 목표각을 출력한다.'],
  ['**특징 추출부 Backbone:**', '**특징 추출부:** Conv·C3k2로 영상을 단계적으로 축소하며 표적 특징을 추출한다. SPPF로 문맥 정보를 모으고 C2PSA의 어텐션으로 특징을 보완한다.'],
  ['**검출 헤드 OBB26:**', '**검출 헤드 OBB26:** 융합된 특징에서 클래스 점수와 회전 박스의 위치·크기·회전각을 예측한다. 이를 원본 영상 좌표계의 꼭짓점과 박스로 변환해 다음 모듈에 전달한다.'],
  ['카메라 원본 해상도는 1920×1080이며,', '카메라 원본 해상도는 1920×1080이다. 추론 전처리에서 영상 비율을 유지하고, 검출한 OBB의 네 꼭짓점 평균으로 표적 중심점을 계산한다.'],
  ['**현재 실행의 피드백:**', '**관측 피드백:** 이동 전후의 표적 중심과 명령 각도 차이를 비교해 축별 응답 이득을 구한다. 이 이득으로 기본 이동 행렬을 보완해 실제 기구의 응답을 다음 각도 계산에 반영한다.'],
  ['**GP 회귀의 구성과 역할:**', '**GP 회귀의 구성과 역할:** 두 회귀기는 Matérn ν=1.5 커널과 관측 잡음 항으로 학습한다. 학습 입력과 새 입력의 커널 값, 학습 계수를 결합해 예측 평균을 계산하고, 이를 거리와 상하 보정량으로 사용한다.'],
  ['**상하 각도 GP 회귀기:**', '**상하 각도 GP 회귀기:** 정규화한 거리 d에서 상하 보정량 p를 예측한다. 거리 GP와 같은 종류의 커널을 사용하지만, 서로 다른 입력·출력 관계에 맞춰 학습한 계수를 갖는다.'],
];
for (const [prefix, replacement] of replace) {
  const index = blocks.findIndex(b => b.startsWith(prefix));
  if (index < 0) throw new Error(`Missing replacement: ${prefix}`);
  blocks[index] = replacement;
}
let result = blocks.join('\n\n') + '\n';
result = result.replace(' 표적 인식부터 최종 각도 결정까지 하나의 모델로 연결하는 구성을 비교하기 위해 시도했다.', '');
result = result.replace(' 이 과정은 방법 3을 완성하기 위한 내부 개발 과정이다.', '');
result = result.replace('검출 결과를 직접 확인할 수 있어 표적 선택과 각도 예측을 단계별로 점검할 수 있고, 전체 이미지에서 각도를 바로 출력하는 방법과 비교할 수 있다고 판단했다.', '검출 결과를 확인하며 표적 선택과 각도 예측을 단계별로 점검할 수 있다는 점도 고려했다.');
result = result.replace('### 5.2. 모델 1 YOLO26l-OBB 검출 모델', '### 5.2. 모델 1 YOLO26l-OBB: 표적의 위치와 크기를 검출하는 모델');
const count = s => s.replace(/\s/g, '').length;
const stats = { originalCharacters: before.length, revisedCharacters: result.length, originalNonWhitespace: count(before), revisedNonWhitespace: count(result), reductionPercent: +(100 * (1 - count(result) / count(before))).toFixed(1) };
fs.writeFileSync(path.join(root, '파랑_자동추적_개발보고서.md'), result);
fs.writeFileSync(path.join(root, '파랑_자동추적_개발과정_및_최종모델_v2.md'), result);
fs.writeFileSync(path.join(root, 'blue-auto-tracking-model-20261019', '파랑_자동추적_개발과정_및_최종모델.md'), result.replaceAll('(architecture_images/', '(../architecture_images/'));
fs.writeFileSync(path.join(work, 'balanced_report_stats.json'), JSON.stringify(stats, null, 2));
console.log(stats);
