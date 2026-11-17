from __future__ import annotations

import copy
import json
import math
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw, ImageFont
from pyhwpxlib import api
from pyhwpxlib.style_manager import ensure_char_style, ensure_para_style

ROOT = Path(__file__).resolve().parent.parent
WORK = Path(__file__).resolve().parent
SOURCE = next(ROOT.glob('*_v2.md'))
OUTPUT = ROOT / '파랑_자동추적_개발보고서.hwpx'
WIDTH = 45354
BATANG = '바탕'
GOTHIC = '맑은 고딕'
doc = api.create_document()
api.set_page_setup(doc, paper='A4', landscape=False,
                   margin_left=7087, margin_right=7087,
                   margin_top=5669, margin_bottom=5669,
                   margin_header=1417, margin_footer=1417)

chars = {}
for name, face, height, bold, color in [
    ('body', BATANG, 1100, False, '#000000'),
    ('bold', BATANG, 1100, True, '#000000'),
    ('title', GOTHIC, 1900, True, '#000000'),
    ('subtitle', GOTHIC, 1200, False, '#222222'),
    ('h1', GOTHIC, 1500, True, '#000000'),
    ('h2', GOTHIC, 1200, True, '#000000'),
    ('meta', GOTHIC, 900, False, '#555555'),
    ('code', GOTHIC, 950, False, '#222222'),
    ('table', BATANG, 1000, False, '#000000'),
    ('tablebold', BATANG, 1000, True, '#000000'),
]:
    chars[name] = ensure_char_style(doc, font_name=face, height=height,
                                    bold=bold, text_color=color)

paras = {
    'body': ensure_para_style(doc, align='JUSTIFY', line_spacing_value=160),
    'left': ensure_para_style(doc, align='LEFT', line_spacing_value=160),
    'title': ensure_para_style(doc, align='CENTER', line_spacing_value=130),
    'heading': ensure_para_style(doc, align='LEFT', line_spacing_value=135),
    'code': ensure_para_style(doc, align='LEFT', line_spacing_value=145, margin_left=700),
    'table': ensure_para_style(doc, align='LEFT', line_spacing_value=135),
    'tablecenter': ensure_para_style(doc, align='CENTER', line_spacing_value=135),
    'caption': ensure_para_style(doc, align='CENTER', line_spacing_value=140),
}


def plain(text):
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    return text.replace('**', '').replace('`', '').strip()


def paragraph(text, role='body', layout='body'):
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    chunks = re.split(r'(\*\*.*?\*\*|`[^`]+`)', text)
    p = api.add_paragraph(doc, '', para_pr_id_ref=paras[layout], char_pr_id_ref=chars[role])
    for chunk in chunks:
        if not chunk:
            continue
        bold = chunk.startswith('**') and chunk.endswith('**')
        content = chunk[2:-2] if bold else chunk.strip('`')
        run = p.add_new_run()
        run.char_pr_id_ref = chars['bold'] if bold and role == 'body' else chars[role]
        run.add_new_t().add_text(content)
    p._report_role = role
    return p


def flowchart():
    path = WORK / 'workflow.png'
    image = Image.new('RGB', (1500, 1050), 'white')
    draw = ImageDraw.Draw(image)
    f = ImageFont.truetype('C:/Windows/Fonts/malgun.ttf', 29)
    heading = ImageFont.truetype('C:/Windows/Fonts/malgunbd.ttf', 35)
    small = ImageFont.truetype('C:/Windows/Fonts/malgun.ttf', 25)
    draw.text((110, 32), '중심 추적: 총 4회 관측', fill='#111111', font=heading)
    draw.text((865, 32), '최저 오차 복귀 및 최종 보정', fill='#111111', font=heading)

    def box(x, y, lines):
        draw.rounded_rectangle((x, y, x+620, y+100), radius=12,
                               outline='#555555', fill='#F6F6F6', width=3)
        text = '\n'.join(lines)
        bb = draw.multiline_textbbox((0, 0), text, font=f, align='center', spacing=5)
        draw.multiline_text((x+310-(bb[2]-bb[0])/2, y+50-(bb[3]-bb[1])/2-5),
                            text, fill='#111111', font=f, spacing=5, align='center')

    def arrow(points):
        draw.line(points, fill='#555555', width=4)
        x0, y0 = points[-2]
        x1, y1 = points[-1]
        a = math.atan2(y1-y0, x1-x0)
        wing = [(x1, y1)]
        for b in (a+2.65, a-2.65):
            wing.append((x1+16*math.cos(b), y1+16*math.sin(b)))
        draw.polygon(wing, fill='#555555')

    left_y = [130, 310, 490, 670]
    left = [
        ['라즈베리파이', '1920×1080 PNG 촬영'],
        ['PC의 YOLO26l-OBB 검출', '파랑 ally 선택·중심 계산'],
        ['실측 모델·현재 관측 피드백', '목표 좌우·상하 각도 계산'],
        ['Arduino 이동', '기구 안정화 후 다시 관측'],
    ]
    for y, lines in zip(left_y, left):
        box(70, y, lines)
    for y1, y2 in zip(left_y, left_y[1:]):
        arrow([(380, y1+100), (380, y2)])
    arrow([(70, 720), (30, 720), (30, 180), (70, 180)])
    draw.text((108, 824), '미완료 시 이동·재촬영 반복', fill='#333333', font=small)
    draw.text((108, 868), '관측 4회 완료 후 오른쪽 단계 진행', fill='#333333', font=small)
    right_y = [130, 290, 450, 610, 770, 930]
    right = [
        ['관측 중 중심 오차가', '가장 작았던 서보 각도로 복귀'],
        ['이번 실행의 마지막 유효 박스', '추가 촬영 없이 재사용'],
        ['GP 1', '박스 크기 → 거리 예측'],
        ['GP 2', '예측 거리 → 상하 보정량'],
        ['좌우 -2° 적용 → 1초 대기', '예측 상하 보정 적용'],
        ['안정화 후 종료'],
    ]
    for y, lines in zip(right_y, right):
        box(810, y, lines)
    for y1, y2 in zip(right_y, right_y[1:]):
        arrow([(1120, y1+100), (1120, y2)])
    arrow([(690, 360), (750, 360), (750, 180), (810, 180)])
    draw.text((704, 265), '4회', fill='#333333', font=small)
    draw.text((704, 302), '완료', fill='#333333', font=small)
    image.save(path)
    return path


table_counter = 0
body_font = ImageFont.truetype('C:/Windows/Fonts/batang.ttc', 100)


def line_count(text, units):
    # Measure at 100 px; HWPX table font is 1000 hwpunit.
    max_px = max(10, (units-700)/10)
    n, size = 1, 0.
    for ch in plain(text):
        cw = body_font.getlength(ch)
        if size+cw > max_px:
            n += 1
            size = 0.
        size += cw
    return n


def wrap_cell(text, units):
    max_px = max(10, (units-800)/10)
    lines, current = [], ''
    for char in text:
        if current and body_font.getlength(current+char) > max_px:
            # Prefer natural word boundaries while retaining long-path wrapping.
            split = current.rfind(' ')
            if split > len(current)//2:
                lines.append(current[:split])
                current = current[split+1:]+char
            else:
                lines.append(current)
                current = char
        else:
            current += char
    if current:
        lines.append(current)
    return '\n'.join(lines)


def table(data):
    global table_counter
    data = [[plain(c) for c in row] for row in data]
    cols = len(data[0])
    # Preserve prose-heavy source tables as labeled report paragraphs.
    prose = cols >= 4 or (cols == 2 and len(data) <= 6) or (cols == 3 and len(data) > 7) or any(len(c) > 100 for r in data[1:] for c in r)
    if prose:
        for row in data[1:]:
            paragraph(row[0], role='h2', layout='heading')
            for label, value in zip(data[0][1:], row[1:]):
                paragraph(f'**{label}:** {value}')
        return
    widths = ([6000, 17000, WIDTH-23000] if table_counter == 0 else
              [13000, 10000, WIDTH-23000] if cols == 3 else
              [13000, WIDTH-13000])
    data = [[wrap_cell(c,w) for c,w in zip(row,widths)] for row in data]
    heights = [max(c.count('\n')+1 for c in row)*1450+850 for row in data]
    colors = {(0, c): '#EEEEEE' for c in range(cols)}
    styles = {(r, c): {'font_size': 10, 'bold': r == 0, 'text_color': '#000000'}
              for r in range(len(data)) for c in range(cols)}
    p = api.add_table(doc, rows=len(data), cols=cols, data=data,
                      width=WIDTH, col_widths=widths, row_heights=heights,
                      cell_margin=(350, 350, 300, 300), cell_colors=colors,
                      cell_styles=styles,
                      cell_aligns={(r,c): 'CENTER' if r == 0 else 'LEFT'
                                   for r in range(len(data)) for c in range(cols)},
                      page_break='CELL', repeat_header=True)
    table_counter += 1


lines = SOURCE.read_text(encoding='utf-8').splitlines()
i = 0
role_order = []
while i < len(lines):
    s = lines[i].strip()
    if not s:
        i += 1
        continue
    if s.startswith('```'):
        lang = s[3:].strip()
        block = []
        i += 1
        while i < len(lines) and not lines[i].strip().startswith('```'):
            block.append(lines[i])
            i += 1
        if lang == 'mermaid':
            api.add_image(doc, str(flowchart()), width=42000, height=29400)
            paragraph('그림 1. 최종 자동추적 및 거리·상하 보정 흐름', role='meta', layout='caption')
        else:
            for code_line in block:
                paragraph(code_line or ' ', role='code', layout='code')
        i += 1
        continue
    if s.startswith('|'):
        data = []
        while i < len(lines) and lines[i].strip().startswith('|'):
            row = [c.strip() for c in lines[i].strip().strip('|').split('|')]
            if not all(re.fullmatch(r':?-+:?', c) for c in row):
                data.append(row)
            i += 1
        table(data)
        continue
    m = re.match(r'^(#{1,3})\s+(.+)$', s)
    if m:
        level, text = len(m[1]), m[2]
        role = {1: 'title', 2: 'h1', 3: 'h2'}[level]
        if level == 1:
            paragraph('파랑 자동추적 개발 보고서', role='title', layout='title')
            paragraph('네 가지 구현 방법과 최종 적용 모델', role='subtitle', layout='caption')
        else:
            paragraph(text, role=role, layout='heading')
    elif re.match(r'^-\s+', s):
        items = []
        while i < len(lines) and re.match(r'^-\s+', lines[i].strip()):
            items.append(plain(lines[i].strip()[2:]))
            i += 1
        api.add_bullet_list(doc, items, char_pr_id_ref=chars['body'], margin_left=1200)
        continue
    elif re.match(r'^\d+\.\s+', s):
        items = []
        while i < len(lines) and re.match(r'^\d+\.\s+', lines[i].strip()):
            items.append(plain(re.sub(r'^\d+\.\s+', '', lines[i].strip())))
            i += 1
        api.add_numbered_list(doc, items, char_pr_id_ref=chars['body'])
        continue
    elif s.startswith('작성 기준:'):
        paragraph(s, role='meta', layout='caption')
    else:
        paragraph(s)
    i += 1

api.add_page_number(doc, pos='BOTTOM_CENTER', side_char='-', char_pr_id_ref=chars['meta'])
raw_path = WORK / 'report_raw.hwpx'
api.save(doc, str(raw_path))

NS = {'hh': 'http://www.hancom.co.kr/hwpml/2011/head',
      'hp': 'http://www.hancom.co.kr/hwpml/2011/paragraph',
      'hc': 'http://www.hancom.co.kr/hwpml/2011/core',
      'hs': 'http://www.hancom.co.kr/hwpml/2011/section'}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)
q = lambda p, name: '{'+NS[p]+'}'+name

with zipfile.ZipFile(raw_path) as z:
    parts = {n: z.read(n) for n in z.namelist()}
header = ET.fromstring(parts['Contents/header.xml'])
section = ET.fromstring(parts['Contents/section0.xml'])
refs = header.find('hh:refList', NS)
para_props = refs.find('hh:paraProperties', NS)
original_para_props = {p.get('id'): copy.deepcopy(p) for p in para_props}
cp_map = {p.get('id'): p for p in refs.find('hh:charProperties', NS)}

# Explicit report paragraph roles, with no blank spacer paragraphs.
role_specs = {
    'body': (160, 0, 400, False, 'JUSTIFY'),
    'title': (135, 0, 1200, True, 'CENTER'),
    'subtitle': (140, 0, 1000, True, 'CENTER'),
    'h1': (140, 1400, 650, True, 'LEFT'),
    'h2': (140, 900, 400, True, 'LEFT'),
    'meta': (140, 0, 650, False, 'CENTER'),
    'code': (145, 150, 150, False, 'LEFT'),
    'table': (135, 0, 0, False, 'LEFT'),
    'tablecenter': (135, 0, 0, False, 'CENTER'),
}
role_pp = {}
base_pp = para_props[0]
for role, (spacing, before, after, keep, align) in role_specs.items():
    pp = copy.deepcopy(base_pp)
    ident = str(max(int(x.get('id')) for x in para_props)+1)
    pp.set('id', ident)
    pp.find('hh:align', NS).set('horizontal', align)
    ls = pp.find('.//hh:lineSpacing', NS)
    ls.set('type', 'PERCENT')
    ls.set('value', str(spacing))
    bs = pp.find('hh:breakSetting', NS)
    if bs is not None:
        bs.set('keepWithNext', '1' if keep else '0')
        bs.set('keepLines', '1' if keep else '0')
        bs.set('widowOrphan', '1')
        bs.set('breakLatinWord', 'CHAR')
    for margin in pp.findall('.//hh:margin', NS):
        for item in margin:
            name = item.tag.split('}')[-1]
            item.set('value', str(before if name == 'prev' else after if name == 'next' else
                                   700 if role == 'code' and name == 'left' else 0))
    para_props.append(pp)
    role_pp[role] = ident
para_props.set('itemCnt', str(len(para_props)))

styles = refs.find('hh:styles', NS)
base_style = copy.deepcopy(styles[0])
role_styles = {}
for role in ('body', 'title', 'subtitle', 'h1', 'h2', 'code', 'meta'):
    st = copy.deepcopy(base_style)
    ident = str(max(int(x.get('id')) for x in styles)+1)
    st.set('id', ident)
    st.set('type', 'PARA')
    st.set('name', '보고서 '+role)
    st.set('engName', 'Report '+role)
    st.set('paraPrIDRef', role_pp[role])
    st.set('charPrIDRef', chars[role])
    styles.append(st)
    role_styles[role] = ident
styles.set('itemCnt', str(len(styles)))

list_styles = {}
for p in section.findall('hp:p', NS):
    old_pp_id = p.get('paraPrIDRef')
    runs = p.findall('hp:run', NS)
    role = 'body'
    for run in runs:
        ref = run.get('charPrIDRef')
        for name in ('title','subtitle','h1','h2','meta','code'):
            if ref == chars[name] and ''.join(run.itertext()).strip():
                role = name
                break
        if role != 'body':
            break
    p.set('paraPrIDRef', role_pp[role])
    p.set('styleIDRef', role_styles[role])
    original_pp = original_para_props.get(old_pp_id)
    list_heading = original_pp.find('hh:heading', NS) if original_pp is not None else None
    if list_heading is not None and list_heading.get('type') in ('BULLET', 'NUMBER'):
        if old_pp_id not in list_styles:
            pp = copy.deepcopy(original_pp)
            ident = str(max(int(x.get('id')) for x in para_props)+1)
            pp.set('id', ident)
            ls = pp.find('.//hh:lineSpacing', NS)
            ls.set('type', 'PERCENT')
            ls.set('value', '160')
            for margin in pp.findall('.//hh:margin', NS):
                for item in margin:
                    name = item.tag.split('}')[-1]
                    item.set('value', str(400 if name == 'next' else 1200 if name == 'left' else
                                           -1000 if name == 'intent' else 0))
            para_props.append(pp)
            list_styles[old_pp_id] = ident
        p.set('paraPrIDRef', list_styles[old_pp_id])
    # Section 5 begins on a new page to keep the final-model description together.
    text = ''.join(p.itertext())
    if role == 'h1' and text.startswith('5. '):
        p.set('pageBreak', '1')
    if p.find('.//hp:pic', NS) is not None:
        pp = copy.deepcopy(base_pp)
        ident = str(max(int(x.get('id')) for x in para_props)+1)
        pp.set('id', ident)
        ls = pp.find('.//hh:lineSpacing', NS)
        ls.set('type', 'FIXED')
        ls.set('value', '30500')
        ls.set('unit', 'HWPUNIT')
        pp.find('hh:align', NS).set('horizontal', 'CENTER')
        para_props.append(pp)
        p.set('paraPrIDRef', ident)
        for pos in p.findall('.//hp:pic/hp:pos', NS):
            pos.set('affectLSpacing', '1')
            pos.set('allowOverlap', '0')

para_props.set('itemCnt', str(len(para_props)))

for tbl in section.findall('.//hp:tbl', NS):
    tbl.set('pageBreak', 'CELL')
    tbl.set('repeatHeader', '1')
    for r, tr in enumerate(tbl.findall('hp:tr', NS)):
        for tc in tr.findall('hp:tc', NS):
            tc.set('header', '1' if r == 0 else '0')
            for p in tc.findall('.//hp:p', NS):
                p.set('paraPrIDRef', role_pp['tablecenter' if r == 0 else 'table'])
                for run in p.findall('hp:run', NS):
                    run.set('charPrIDRef', chars['tablebold' if r == 0 else 'table'])

parts['Contents/header.xml'] = ET.tostring(header, encoding='utf-8', xml_declaration=True)
parts['Contents/section0.xml'] = ET.tostring(section, encoding='utf-8', xml_declaration=True)
with zipfile.ZipFile(OUTPUT, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('mimetype', parts.pop('mimetype'), compress_type=zipfile.ZIP_STORED)
    for name, data in parts.items():
        z.writestr(name, data)

audit = {'source': SOURCE.name, 'output': OUTPUT.name,
         'paper': 'A4', 'body_font': BATANG, 'body_size_pt': 11,
         'body_line_spacing_percent': 160, 'table_size_pt': 10,
         'tables': len(section.findall('.//hp:tbl', NS)),
         'images': len(section.findall('.//hp:pic', NS)),
         'main_headings': sum(1 for p in section.findall('hp:p', NS)
                              if p.get('styleIDRef') == role_styles['h1']),
         'package_integrity': None}
with zipfile.ZipFile(OUTPUT) as z:
    assert z.testzip() is None
    for name in z.namelist():
        if name.endswith('.xml') or name.endswith('.hpf'):
            ET.fromstring(z.read(name))
audit['package_integrity'] = True
assert audit['main_headings'] == 5
(WORK / 'layout_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(audit, ensure_ascii=True, indent=2))
