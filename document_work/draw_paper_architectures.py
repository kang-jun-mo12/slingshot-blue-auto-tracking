from pathlib import Path
from html import escape
import json
import resvg_py

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'architecture_images'
OUT.mkdir(exist_ok=True)
PALETTE={
 'input':('#ECE7F7','#9B83D5'),
 'learn':('#DCEBFA','#6AA5DA'),
 'feature':('#FFF0CD','#E8AC45'),
 'compute':('#E0F3EB','#42AB91'),
 'check':('#FBE3E7','#ED8492'),
 'plain':('#FFFFFF','#8C949D'),
}

class Paper:
 def __init__(self,w,h,label):
  self.w,self.h=w,h
  self.s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
   '<defs><marker id="a" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="userSpaceOnUse"><path d="M0 0 L11 6 L0 12 Z" fill="#50565D"/></marker></defs>',
   '<rect width="100%" height="100%" fill="white"/>']
  self.text(35,42,[label],size=29,bold=True,anchor='start')
 def text(self,x,y,lines,size=30,bold=False,color='#20252B',anchor='middle',step=None,italic=False):
  spans=''.join(f'<tspan x="{x}" dy="{0 if i==0 else (step or int(size*1.36))}">{escape(str(t))}</tspan>' for i,t in enumerate(lines))
  self.s.append(f'<text x="{x}" y="{y}" font-family="Malgun Gothic, 맑은 고딕, Arial, sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" font-style="{"italic" if italic else "normal"}" text-anchor="{anchor}" fill="{color}">{spans}</text>')
 def box(self,x,y,w,h,title,lines=(),kind='input',size=29,ts=33):
  fill,stroke=PALETTE[kind]
  self.s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="11" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
  total=ts+(12+len(lines)*int(size*1.36) if lines else 0)
  top=y+(h-total)/2+ts*.8
  self.text(x+w/2,top,[title],size=ts,bold=True)
  if lines:self.text(x+w/2,top+ts*.5+size,lines,size=size)
 def group(self,x,y,w,h,title,kind='compute'):
  _,stroke=PALETTE[kind]
  self.s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="none" stroke="{stroke}" stroke-width="1.8" stroke-dasharray="9 7"/>')
  self.text(x+18,y+38,[title],size=28,bold=True,color=stroke,anchor='start')
 def arrow(self,points,label=None,lxy=None,dashed=False,color='#50565D'):
  pts=' '.join(f'{x},{y}' for x,y in points)
  self.s.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.4" stroke-linejoin="round" marker-end="url(#a)"'+(' stroke-dasharray="8 7"' if dashed else '')+'/>')
  if label:self.text(*lxy,label if isinstance(label,list) else [label],size=27,color=color)
 def dot(self,x,y):self.s.append(f'<circle cx="{x}" cy="{y}" r="4.5" fill="#50565D"/>')
 def op(self,x,y,label,r=30):
  self.s.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="white" stroke="#737B85" stroke-width="2"/>')
  self.text(x,y+11,[label],size=35)
 def save(self,name):
  svg=''.join(self.s)+'</svg>'
  (OUT/(name+'.svg')).write_text(svg,encoding='utf-8')
  png=resvg_py.svg_to_bytes(svg_string=svg,zoom=2,background='white',font_files=[
   'C:/Windows/Fonts/arial.ttf','C:/Windows/Fonts/arialbd.ttf',
   'C:/Windows/Fonts/malgun.ttf','C:/Windows/Fonts/malgunbd.ttf'])
  (OUT/(name+'.png')).write_bytes(png)
  return {'name':name,'width':self.w,'height':self.h,'style':'paper_reference'}

figures=[]

# (a) Three model blocks only; smaller lavender boxes are I/O variables.
p=Paper(1600,760,'(a) 전체 시스템 구조')
p.box(35,100,310,125,'카메라 이미지',['RGB / 1920 x 1080'])
p.box(565,100,250,125,'서보 상태',['좌우각, 상하각'])
p.box(855,100,250,125,'화면 중심',['(960, 540)'])
p.box(35,350,395,220,'1  YOLO 모듈',['YOLO26l-OBB','표적 검출'],kind='feature',ts=35,size=31)
p.box(600,350,445,220,'2  CAM',['중심 정렬 모듈','회귀 예측 + 관측 피드백'],kind='learn',ts=34,size=29)
p.box(1215,350,350,220,'3  DPCM',['GP 기반 상하 보정','동작각 출력·발사 연동','대기 0° / 발사 180°'],kind='compute',ts=32,size=26)
p.arrow([(190,225),(190,350)])
p.arrow([(690,225),(690,350)])
p.arrow([(980,225),(980,350)])
p.arrow([(430,460),(600,460)],'c = (x, y)',(515,430))
p.arrow([(1045,460),(1215,460)],'기준 각도',(1130,430))
p.arrow([(230,570),(230,680),(1290,680),(1290,570)],'박스 폭·높이 (w, h)',(760,654))
figures.append(p.save('01_system_architecture'))

# (b) Neural blocks verified from the checkpoint, with no invented servo head.
p=Paper(1600,1510,'(b) YOLO26l-OBB 검출 모듈')
p.box(570,85,460,110,'RGB 이미지',['1920 x 1080 x 3'])
p.box(430,255,740,125,'전처리',['정규화·직사각형 패딩 / 입력 기준 크기 1920','단일 영상 패딩 입력: 1920 x 1088'],kind='plain',size=27)
p.arrow([(800,195),(800,255)])
p.group(40,450,630,440,'특징 추출부',kind='learn')
p.box(85,525,540,105,'합성곱 + C3k2',['계층별 영상 특징 추출'],kind='learn',size=28)
p.box(85,710,235,115,'SPPF',['문맥 정보 통합'],kind='learn',size=25,ts=31)
p.box(390,710,235,115,'C2PSA',['주요 특징 강조'],kind='learn',size=25,ts=31)
p.arrow([(800,380),(800,415),(355,415),(355,525)])
p.arrow([(200,630),(200,710)])
p.arrow([(320,767),(390,767)])
p.group(930,450,630,440,'다중 해상도 특징 융합',kind='compute')
p.box(975,525,540,125,'확대·결합 + C3k2',['상위 → 하위 특징 융합'],kind='compute',size=26,ts=29)
p.box(975,715,540,125,'합성곱·결합 + C3k2',['하위 → 상위 특징 융합'],kind='compute',size=26,ts=29)
p.arrow([(1245,650),(1245,715)])
p.arrow([(670,600),(930,600)],['여러 해상도','특징'],(800,540))
p.group(125,980,1350,230,'OBB26 검출 헤드  /  P3/8, P4/16, P5/32',kind='check')
p.box(175,1060,370,105,'클래스 점수',['2개 클래스'],kind='check',size=27)
p.box(615,1060,370,105,'박스 회귀',['위치·크기'],kind='check',size=27)
p.box(1055,1060,370,105,'회전각 회귀',['회전각 1개'],kind='check',size=27)
p.arrow([(1245,890),(1245,980)])
p.box(270,1285,1060,130,'좌표 변환 및 파랑 표적 선택',[
 '꼭짓점 → 중심 (x,y), 수평 외접 박스 → 폭·높이 (w,h)',
 '파랑 적군 표적 추적·분홍 아군 표적 제외'],kind='plain',size=27,ts=31)
p.arrow([(800,1210),(800,1285)])
p.arrow([(800,1415),(800,1448)])
p.text(800,1488,['출력: 표적 중심, 박스 크기, 클래스, 신뢰도, 회전 박스 꼭짓점'],size=28)
figures.append(p.save('02_yolo_module'))

# (c) Empirical regression and response gain estimation shown as two branches.
p=Paper(1600,1550,'(c) CAM: 중심 정렬 모듈')
p.box(55,100,330,120,'표적 중심 c',['(x, y)'])
p.box(435,100,330,120,'서보 자세',['좌우각, 상하각'])
p.box(1030,100,515,120,'현재 실행 관측',['이동 전후 각도·중심점'],size=27)
p.group(40,300,730,660,'실측 기반 이동 모델',kind='feature')
p.box(90,385,630,150,'정규화 특징 벡터',['좌표·자세 (u,v,a,b)의 15개 다항 특징',
  '상수항·1차항·제곱항·교차항'],kind='feature',size=27,ts=31)
p.box(90,600,630,145,'강건 회귀 계수',['W_yaw, W_pitch: 각 15 x 2','특징 → 픽셀 이동 예측'],kind='learn',size=28,ts=30)
p.box(90,810,630,105,'기본 이동 행렬 J0',['2 x 2 행렬·검증된 축별 기준 모델'],kind='input',size=27,ts=32)
p.arrow([(220,220),(220,255),(450,255),(450,385)])
p.arrow([(600,220),(600,385)])
p.arrow([(405,535),(405,600)])
p.arrow([(405,745),(405,810)])
p.group(900,300,660,660,'현재 관측 응답 피드백',kind='compute')
p.box(950,385,560,150,'실측 응답 쌍',['각도 변화량·픽셀 이동량',
  '동일 표적·실행 여부와 신뢰도 확인'],kind='plain',size=27,ts=31)
p.box(950,600,560,145,'강건 회귀로 응답 이득 추정',['실제 이동과 예측 이동 비교',
  '현재 실행에서 얻은 관측만 사용'],kind='compute',size=27,ts=30)
p.box(950,810,560,105,'축별 응답 이득 g',['diag(g_yaw, g_pitch)'],kind='input',size=28,ts=32)
p.arrow([(1285,220),(1285,385)])
p.arrow([(1230,535),(1230,600)])
p.arrow([(1230,745),(1230,810)])
p.op(810,1050,'x')
p.arrow([(405,960),(405,1050),(780,1050)],'J0',(600,1025))
p.arrow([(1230,960),(1230,1050),(840,1050)],'diag(g)',(1080,1025))
p.box(455,1140,710,150,'감쇠를 적용한 역관계 각도 계산',['J = J0 diag(g);  delta_theta = -J^-1 e',
  'e = 표적 중심 - 화면 중심'],kind='compute',size=28,ts=32)
p.arrow([(810,1080),(810,1140)])
p.box(40,1140,320,150,'픽셀 오차 e',['c - (960, 540)','표적 중심 - 기준 중심'],kind='compute',size=25,ts=29)
p.dot(220,255)
p.arrow([(220,255),(15,255),(15,1090),(200,1090),(200,1140)])
p.arrow([(360,1215),(455,1215)],'e',(409,1185))
p.box(455,1360,710,130,'제약 조건을 적용한 각도 선택',['이동 제한: 8° / 3°·명령 간격: 0.1°',
  '출력: 목표 좌우·상하 각도, 예측 중심점'],kind='plain',size=27,ts=31)
p.arrow([(810,1290),(810,1360)])
figures.append(p.save('03_centering_module'))

# (d) GP regression for distance/pitch, followed by integrated servo-angle outputs.
p=Paper(1600,1640,'(d) DPCM: GP 기반 상하 보정 및 동작각 출력')
p.box(300,90,1000,120,'표적 바운딩 박스',['폭 w·높이 h'],size=29)
p.box(35,330,440,185,'박스 크기 특징',['s = sqrt(w h)','1/s 정규화'],kind='feature',size=28,ts=32)
p.box(580,330,440,185,'GP 1: 거리 예측',['마테른 커널, ν = 1.5',
  '정규화한 1/s → 거리 d'],kind='learn',size=27,ts=32)
p.arrow([(800,210),(800,265),(255,265),(255,330)])
p.arrow([(475,422),(580,422)])
p.arrow([(1020,422),(1125,422)])
p.box(1125,330,440,185,'거리 정규화',['예측 거리 d 정규화',
  '학습 자료의 통계값 사용'],kind='plain',size=26,ts=30)
p.box(35,690,985,185,'GP 2: 상하 각도 예측',['마테른 커널, ν = 1.5',
  '정규화한 d → 원시 상하 보정량 p'],kind='learn',size=27,ts=31)
p.arrow([(1345,515),(1345,782),(1020,782)])
p.box(35,900,270,80,'기준 상하 각도',kind='input',ts=27)
p.box(35,1015,660,165,'기준 각도 보정',['기준 상하 각도 + 예측 보정량',
  '상하 서보 목표 각도에 적용'],kind='compute',size=28,ts=32)
p.box(900,1015,660,165,'발사 목표각 출력',['대기 각도 0° → 발사 목표각 180°',
  '상하 보정 결과와 발사 동작 연동'],kind='compute',size=28,ts=32)
p.arrow([(170,980),(170,1015)])
p.arrow([(527,875),(527,1015)],'예측 보정량',(630,950))
p.arrow([(695,1097),(900,1097)],'보정 후',(797,1070))
p.group(35,1270,1530,240,'거리·상하 GP 추론: 회귀기별 계수 사용',kind='compute')
p.text(800,1360,['r = √3 |x - x_i| / ℓ',
  'k(x,x_i) = A (1+r) exp(-r)',
  '예측값 = μ + σ × Σ(k_i × α_i)',
  'μ: 평균, σ: 표준편차, ℓ: 길이 척도'],size=28)
p.text(800,1590,['거리·상하 학습 자료: 실측 11개 + 확인된 기존 계산 결과 100개'],size=25,color='#59616B')
figures.append(p.save('04_pitch_module'))

(OUT/'figures.json').write_text(json.dumps(figures,indent=2),encoding='utf-8')
print(json.dumps(figures,indent=2))
