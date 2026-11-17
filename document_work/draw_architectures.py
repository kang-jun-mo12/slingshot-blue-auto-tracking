from pathlib import Path
from html import escape
import json

import resvg_py
from PIL import ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'architecture_images'
OUT.mkdir(exist_ok=True)
BLUE = '#2563A6'
GREEN = '#26735B'
PURPLE = '#76509B'
INK = '#182637'
MUTED = '#526276'
GRAY = '#CFD7E1'
FONT = 'C:/Windows/Fonts/malgun.ttf'
BOLD = 'C:/Windows/Fonts/malgunbd.ttf'


class Figure:
    def __init__(self, width, height, title, subtitle):
        self.width, self.height = width, height
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                      '<defs><marker id="arrow" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L10,5.5 L0,11 Z" fill="#526276"/></marker></defs>',
                      '<rect width="100%" height="100%" fill="white"/>']
        self.text(width/2, 58, [title], size=48, bold=True)
        self.text(width/2, 113, [subtitle], size=30, color=MUTED)

    def text(self, x, y, lines, size=36, bold=False, color=INK, anchor='middle', step=None):
        step = step or int(size*1.45)
        spans = ''.join(f'<tspan x="{x}" dy="{0 if i == 0 else step}">{escape(str(s))}</tspan>' for i,s in enumerate(lines))
        self.parts.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="Malgun Gothic, 맑은 고딕, sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}">{spans}</text>')

    def box(self, x, y, w, h, title, lines=(), color=BLUE, fill='#F4F8FD', size=35, title_size=39):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{color}" stroke-width="2.5"/>')
        self.text(x+w/2, y+58, [title], size=title_size, bold=True, color=color)
        if lines:
            self.text(x+w/2, y+112, lines, size=size, step=int(size*1.48))

    def panel(self, x,y,w,h,title,color=BLUE,fill='#FAFCFF'):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{fill}" stroke="{GRAY}" stroke-width="2"/>')
        self.text(x+w/2,y+55,[title],size=40,bold=True,color=color)

    def arrow(self, points, dashed=False):
        pts=' '.join(f'{x},{y}' for x,y in points)
        self.parts.append(f'<polyline points="{pts}" fill="none" stroke="{MUTED}" stroke-width="3.5" stroke-linejoin="round" marker-end="url(#arrow)"'+(' stroke-dasharray="10 8"' if dashed else '')+'/>')

    def note(self,x,y,lines,size=31):
        self.text(x,y,lines,size=size,color=MUTED)

    def save(self,name):
        svg=''.join(self.parts)+'</svg>'
        (OUT/(name+'.svg')).write_text(svg,encoding='utf-8')
        png=resvg_py.svg_to_bytes(svg_string=svg,background='white',zoom=1.5,
                                 font_files=[FONT,BOLD],skip_system_fonts=False)
        (OUT/(name+'.png')).write_bytes(png)
        return {'name':name,'width':self.width,'height':self.height}


figures=[]

# Only the three functional model blocks are outlined in the overall figure.
f=Figure(1800,1020,'최종 시스템 아키텍처','세 모듈의 연결과 입력·출력')
f.text(290,180,['입력: 카메라 이미지','RGB · 1920×1080'],size=35)
f.arrow([(290,255),(290,310)])
f.box(50,310,480,205,'YOLO 모듈',['YOLO26l-OBB','표적 위치·크기 검출'],BLUE,size=36,title_size=46)
f.box(660,310,480,205,'중심점 맞춤 모듈',['실측 회귀 + 관측 피드백','화면 중심으로 정렬'],GREEN,'#F2FAF6',size=34,title_size=42)
f.box(1270,310,480,205,'상하 보정 모듈',['박스 크기 → 거리 → 각도','추가 상하 각도 계산'],PURPLE,'#F8F4FC',size=34,title_size=44)
f.arrow([(530,410),(660,410)])
f.note(595,260,['표적 중심','(u, v)'],size=28)
f.arrow([(1140,410),(1270,410)])
f.note(1205,260,['정렬 후','기준 각도'],size=28)
f.text(900,180,['추가 입력: 화면 중심 (960,540)','현재 서보 각도 · 현재 관측 피드백'],size=31)
f.arrow([(900,255),(900,310)])
f.arrow([(290,515),(290,680),(1385,680),(1385,515)])
f.note(815,652,['박스 폭·높이 (w,h): 마지막 유효 검출에서 재사용'],size=32)
f.text(900,565,['출력: 중심 맞춤용','좌우·상하 서보 목표 각도'],size=33,color=GREEN)
f.arrow([(1510,515),(1510,800)])
f.text(1510,855,['출력: 추가 상하 각도 Δpitch','기준 각도에 더해 최종 적용'],size=31,color=PURPLE)
f.note(560,840,['중심점 맞춤은 이동 후 재촬영하며 총 4회 관측','가장 작은 중심 오차의 각도로 복귀한 뒤 상하 보정 실행'],size=30)
f.note(900,960,['최종 적용: 좌우 -2° 보정 → 1초 대기 → 예측한 상하 보정 적용'],size=30)
figures.append(f.save('01_system_architecture'))

# Checkpoint-verified architecture: Conv/C3k2/SPPF/C2PSA -> fusion -> OBB26.
f=Figure(1800,1670,'YOLO 모듈의 모델 구조','best.pt에 저장된 YOLO26l-OBB 구성 기준')
f.box(250,170,1300,175,'입력 및 전처리',['RGB 1920×1080 → 정규화·직사각형 패딩','imgsz=1920 · rect=True · 단일 원본의 패딩 입력 1920×1088'],BLUE,size=32)
f.arrow([(650,345),(650,405)])
f.panel(70,405,750,480,'Backbone  특징 추출',BLUE)
f.box(115,490,660,105,'Conv + C3k2',[],BLUE,title_size=35)
f.arrow([(115,545),(92,545),(92,754),(115,754)])
f.text(445,643,['단계적으로 해상도를 줄이며 특징 추출'],size=31)
f.box(115,690,300,130,'SPPF',['문맥 특징 집계'],BLUE,size=28,title_size=35)
f.box(475,690,300,130,'C2PSA',['어텐션 특징 보완'],BLUE,size=28,title_size=35)
f.arrow([(415,754),(475,754)])
f.panel(980,405,750,480,'Neck  다중 스케일 융합',BLUE)
f.box(1020,490,670,135,'Upsample + Concat + C3k2',['큰 특징맵으로 확장하며 결합'],BLUE,size=29,title_size=32)
f.box(1020,685,670,135,'Conv + Concat + C3k2',['다시 축소하며 특징 재결합'],BLUE,size=29,title_size=34)
f.arrow([(1355,625),(1355,685)])
f.arrow([(820,610),(980,610)])
f.note(900,535,['여러 해상도의','특징 전달'],size=26)
f.arrow([(1355,885),(1355,955)])
f.box(230,955,1340,230,'OBB26 검출 헤드',['P3/8 · P4/16 · P5/32 특징을 이용한 예측','클래스 점수 + 회전 박스 위치·크기 + 회전각','출력은 표적 검출 정보이며 서보 각도를 직접 예측하지 않음'],BLUE,size=34,title_size=42)
f.arrow([(900,1185),(900,1255)])
f.box(230,1255,1340,245,'좌표 변환과 추적 대상 선택',['OBB 네 꼭짓점 평균 → 중심 (u,v)','원본 영상 안의 수평 외접 박스 → 폭 w, 높이 h','class ID=1, name=ally인 파란 표적만 중심 추적에 사용'],BLUE,size=34,title_size=38)
f.text(900,1560,['출력: 중심점 · 외접 박스 크기 · 꼭짓점 · 신뢰도 · 클래스'],size=33,color=BLUE)
f.note(900,1620,['신경망 추론은 PC에서 수행하고, 검출 좌표를 Pi의 추적 코드로 전달한다.'],size=29)
figures.append(f.save('02_yolo_module'))

# The centering model is an empirical regression and a controller, not a CNN.
f=Figure(1800,1680,'중심점 맞춤 모듈의 모델 구조','실측 다항 회귀로 이동을 예측하고 현재 세션 응답으로 보완')
f.box(180,175,1440,145,'입력',['표적 중심 (x,y) · 현재 좌우·상하 각도 · 이동 전후 관측'],GREEN,'#F2FAF6',size=34)
f.arrow([(600,320),(430,385)])
f.arrow([(1200,320),(1370,385)])
f.box(60,385,740,200,'1  정규화와 15개 특징 생성',['화면 위치 (u,v), 서보 자세 (a,b)','상수·1차·제곱·교차항으로 특징 φ 구성'],GREEN,'#F2FAF6',size=32,title_size=37)
f.box(1000,385,740,200,'3  현재 실행의 관측 쌍 구성',['실제로 명령한 Δ각도와 관측한 Δ픽셀','같은 표적·세션·신뢰도 등 조건 검사'],GREEN,'#F2FAF6',size=32,title_size=37)
f.arrow([(430,585),(430,655)])
f.arrow([(1370,585),(1370,655)])
f.box(60,655,740,190,'2  실측 회귀 계수로 J₀ 계산',['축별 15×2 계수 → 2×2 이동 행렬','검증을 통과한 축별 prior를 반영'],GREEN,'#F2FAF6',size=32,title_size=36)
f.box(1000,655,740,190,'4  관측 응답으로 축별 이득 추정',['강건한 응답 비교로 g_yaw, g_pitch 계산','새 신경망을 재학습하는 과정은 아님'],GREEN,'#F2FAF6',size=31,title_size=34)
f.arrow([(430,845),(430,920),(650,920),(650,970)])
f.arrow([(1370,845),(1370,920),(1150,920),(1150,970)])
f.box(350,970,1100,180,'5  보완한 이동 행렬과 중심 오차',['J = J₀ × diag(g_yaw, g_pitch)','중심 오차 = (x - 960, y - 540)'],GREEN,'#F2FAF6',size=35,title_size=38)
f.arrow([(900,1150),(900,1220)])
f.box(250,1220,1300,230,'6  각도 후보 계산과 제한',['Δ각도 후보 = −J⁻¹e → 감쇠·이동량 제한','측정 범위 안 최대 8° / 범위 밖 최대 3° · 0.1° 후보 선택','출력: 좌우·상하 목표 각도와 예측 중심'],GREEN,'#F2FAF6',size=33,title_size=40)
f.note(900,1520,['반복 제어: 이동 → 최소 3초 안정화 → 재촬영·검출 → 응답 보완'],size=32)
f.note(900,1577,['총 4회 관측 중 최저 중심 오차의 각도로 복귀'],size=32)
f.note(900,1636,['특징 φ = [1,u,v,u²,uv,v²,a,b,a²,b²,ab,ua,va,ub,vb]'],size=29)
figures.append(f.save('03_centering_module'))

# Explicit two-GP cascade, geometry checks separated from learned inference.
f=Figure(1800,1540,'상하 보정 모듈의 모델 구조','두 개의 Matérn GP 회귀기를 순서대로 사용하는 하나의 보정 모듈')
f.box(180,175,1440,145,'입력',['마지막 유효 박스의 폭 w·높이 h · 신뢰도 · 복귀 후 기준 각도'],PURPLE,'#F8F4FC',size=32)
f.arrow([(320,320),(320,385)])
f.box(60,385,520,230,'1  입력 조건 검사',['신뢰도 ≥ 0.6','크기·비율·범위 검사','부적합하면 보정 중단'],PURPLE,'#F8F4FC',size=31,title_size=36)
f.box(640,385,520,230,'2  크기 특징과 정규화',['s = √(w×h)','경계 크기 제한 후 1/s','학습 시 범위로 정규화'],PURPLE,'#F8F4FC',size=31,title_size=35)
f.box(1220,385,520,230,'3  거리 GP 회귀기',['Matérn ν=1.5','정규화된 1/s → 거리 d','저장된 계수로 추론'],PURPLE,'#F8F4FC',size=31,title_size=36)
f.arrow([(580,500),(640,500)])
f.arrow([(1160,500),(1220,500)])
f.arrow([(1480,615),(1480,735)])
f.box(1220,735,520,230,'4  거리 제한과 정규화',['거리 d를 1.50~2.00m로 제한','거리의 학습 범위로 정규화','두 번째 GP의 입력 생성'],PURPLE,'#F8F4FC',size=29,title_size=34)
f.box(640,735,520,230,'5  상하 각도 GP 회귀기',['Matérn ν=1.5','정규화된 d → 원시 보정 p','거리별 추가 상하 각도 예측'],PURPLE,'#F8F4FC',size=30,title_size=33)
f.box(60,735,520,230,'6  방향·trim 적용',['추가 각도 = 원시 보정 p - 1°','0.1° 단위로 반올림','trim은 한 번만 적용'],PURPLE,'#F8F4FC',size=28,title_size=35)
f.arrow([(1220,850),(1160,850)])
f.arrow([(640,850),(580,850)])
f.arrow([(320,965),(320,1085)])
f.box(60,1085,660,240,'출력과 최종 적용',['추가 상하 각도 Δpitch','최종 pitch = 기준 pitch + Δpitch','0~180° 검사 후 서보 명령'],PURPLE,'#F8F4FC',size=32,title_size=38)
f.panel(810,1085,930,240,'두 GP에서 사용하는 추론 계산',PURPLE,'#FFFFFF')
f.text(1275,1190,['r = √3 × |x−xᵢ| / ℓ','k(x,xᵢ) = A(1+r) exp(−r)','예측값 = mean + std × Σ k(x,xᵢ)αᵢ'],size=31)
f.note(900,1405,['학습: 실측 11개 + 기존 계산 결과 100개 = 총 111개 자료'],size=32)
f.note(900,1465,['운영: distance-model.json의 계수로 NumPy 추론 · 좌우 −2°는 별도 설정값'],size=30)
figures.append(f.save('04_pitch_module'))

(OUT/'figures.json').write_text(json.dumps(figures,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(figures,indent=2))
