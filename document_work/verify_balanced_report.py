from pathlib import Path
import json
import re
import zipfile
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent.parent
md = (root / '5조 고무줄 총 자동화 시스템 개발 보고서.md').read_text(encoding='utf-8')
original = (root / 'document_work/report_before_concision.md').read_text(encoding='utf-8')
assert (root / '파랑_자동추적_개발과정_및_최종모델_v2.md').read_text(encoding='utf-8') == md
assert (root / 'blue-auto-tracking-model-20261019/파랑_자동추적_개발과정_및_최종모델.md').read_text(encoding='utf-8').replace('(../architecture_images/', '(architecture_images/') == md
assert len(re.findall(r'^## [1-5]\. ', md, re.M)) == 5
images = re.findall(r'!\[.*?\]\((.*?)\)', md)
assert len(images) == 5
for name in images:
    assert (root / name).is_file(), name
section3 = md.split('### 3.3.')[1].split('## 4.')[0]
assert all(section3.count(x) == 1 for x in ['첫째,', '둘째,', '셋째,', '넷째,'])
assert '다섯째' not in section3
assert not re.search(r'성공률|4/15|5/15|10/15|4\.7cm', md.split('## 3.')[1].split('## 4.')[0])
for needed in ['4/15회, 약 26.7%', '5/15회, 약 33.3%', '10/15회, 약 66.7%', '약 11cm', '약 13cm', '약 4.7cm', '1.5~2.0m', '표적 두 개', '분홍 표적은 ally', '파랑 표적은 enemy', '15개 다항 특징', 'Matérn ν=1.5', '가우시안 프로세스(Gaussian Process, GP)', '초기 0°에서 목표각 180°']:
    assert needed in md, needed
for forbidden in ['github.com', 'GitHub', '최저 오차 각도 복귀', '최소 3초', '총 4회 관측', '마지막으로 유효하게', '신뢰도는 0.6', '상하 trim', '좌우 -2°', '발사 GP', '기구 안정화 후 자동추적']:
    assert forbidden not in md, forbidden
ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
assert '작성 기준:' not in md
assert '5조 강준모 정영서 조성호 허재원' in md
docx = root / '5조 고무줄 총 자동화 시스템 개발 보고서.docx'
with zipfile.ZipFile(docx) as z:
    assert z.testzip() is None
    xmls = [n for n in z.namelist() if n.endswith('.xml')]
    for name in xmls:
        ET.fromstring(z.read(name))
    tree = ET.fromstring(z.read('word/document.xml'))
    document_text = ''.join(tree.itertext())
    assert '작성 기준:' not in document_text
    assert '5조 강준모 정영서 조성호 허재원' in document_text
    hardware_extent = tree.find('.//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent')
    assert hardware_extent.attrib == {'cx': '3960000', 'cy': '2970000'}
    flat = re.sub(r'\s', '', document_text)
    checked = 0
    for line in md.splitlines():
        line = line.strip()
        if not line or line.startswith('```') or line.startswith('![') or re.match(r'^\|[-| ]+\|$', line):
            continue
        texts = line.strip('|').split('|') if line.startswith('|') else [re.sub(r'^(?:#{1,6}\s+|\d+\.\s+|-\s+)', '', line)]
        for text in texts:
            plain = re.sub(r'\s|\*\*|`', '', text)
            assert plain in flat, text
            checked += 1
    embedded = [z.read(n) for n in z.namelist() if n.startswith('word/media/')]
    assert len(embedded) == 5
    for name in images:
        assert (root / name).read_bytes() in embedded, name
    assert len(tree.findall('.//w:tbl', ns)) == 2
    assert len(tree.findall('.//w:drawing', ns)) == 5
stats = {
    'original_non_whitespace': len(re.sub(r'\s', '', original)),
    'revised_non_whitespace': len(re.sub(r'\s', '', md)),
    'reduction_percent': round(100 * (1 - len(re.sub(r'\s', '', md)) / len(re.sub(r'\s', '', original))), 1),
    'markdown_copies_match': True,
    'required_sections': 5,
    'figures': 5,
    'tables': 2,
    'word_content_items_checked': checked,
    'word_xml_parts_valid': len(xmls),
    'word_bytes': docx.stat().st_size,
}
for heading in ['### 5.2.', '### 5.3.', '### 5.4.']:
    section = md.split(heading, 1)[1].split('\n### ', 1)[0]
    for label in ['**입력:**', '**출력:**', '**학습 데이터:**']:
        assert section.count(label) == 1, (heading, label)
dataset = json.loads((root / 'blue-auto-tracking-model-20261019/models/yolo26l_obb_1920x1088/model_info.json').read_text(encoding='utf-8'))['dataset']
assert dataset['total_images'] == 2856 and dataset['train_images'] == 2560 and dataset['validation_images'] == 296
sources = dataset['sources']
assert sources['existing_real_obb']['train'] + sources['new_real_obb']['train'] == 1560
assert sources['synthetic_obb']['train'] == 1000
data_audit = json.loads((root / 'blue-auto-tracking-model-20261019/calibration/distance-learning-20261014/data-audit.json').read_text(encoding='utf-8'))
assert data_audit['accepted_rows'] == 111
for text in ['2,856장', '2,560장', '296장', '1,560장', '1,000장', '직접 실측한 총 111건', '실측 거리–실측 상하 보정량', 'ORB와 RANSAC']:
    assert text in md, text
for removed in ['11건은 직접 실측', '100건은 기존 추정기', '새로 독립 측정한 실측값과 구분']:
    assert removed not in md, removed
stats['module_input_output_training_sections'] = 3
stats['yolo_training_counts_match_metadata'] = True
stats['dpcm_training_data_source'] = 'User correction: all 111 records are direct measurements; historical source metadata unchanged.'
(root / 'document_work/balanced_report_audit.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(stats, ensure_ascii=False, indent=2))
