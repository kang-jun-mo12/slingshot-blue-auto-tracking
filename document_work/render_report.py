from pathlib import Path
import json
import re
from xml.etree import ElementTree as ET

import resvg_py
from PIL import Image, ImageDraw, ImageFont
from pyhwpxlib.rhwp_bridge import RhwpEngine

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / 'preview'
OUT.mkdir(exist_ok=True)
for pattern in ('page-*.png', 'page-*.svg'):
    for previous in OUT.glob(pattern):
        assert previous.resolve().parent == OUT.resolve()
        previous.unlink()
path = next(ROOT.glob('*.hwpx'))
fonts = {'바탕': 'C:/Windows/Fonts/batang.ttc',
         'batang': 'C:/Windows/Fonts/batang.ttc',
         '맑은 고딕': 'C:/Windows/Fonts/malgun.ttf',
         'malgun gothic': 'C:/Windows/Fonts/malgun.ttf'}
engine = RhwpEngine(font_map=fonts)
audit = []
with engine.load(str(path)) as report:
    print('PAGES', report.page_count, flush=True)
    for i in range(report.page_count):
        svg = report.render_page_svg(i, embed_fonts=True)
        (OUT / f'page-{i+1:02}.svg').write_text(svg, encoding='utf-8')
        png = resvg_py.svg_to_bytes(svg_string=svg, background='white', zoom=1.4,
                                   font_files=list(set(fonts.values())))
        (OUT / f'page-{i+1:02}.png').write_bytes(png)
        content = [''.join(t.itertext()) for t in ET.fromstring(svg).iter()
                   if t.tag.endswith('}text')]
        audit.append({'page': i+1, 'text_elements': len(content),
                      'first_text': content[:3], 'last_text': content[-3:]})
        print('RENDERED', i+1, flush=True)

pngs = sorted(OUT.glob('page-*.png'))
thumb_w, thumb_h = 278, 400
cols = 4
rows = (len(pngs)+cols-1)//cols
contact = Image.new('RGB', (cols*thumb_w, rows*thumb_h), '#dddddd')
d = ImageDraw.Draw(contact)
for i, p in enumerate(pngs):
    im = Image.open(p).convert('RGB')
    im.thumbnail((thumb_w-12, thumb_h-28))
    x, y = (i%cols)*thumb_w+6, (i//cols)*thumb_h+24
    contact.paste(im, (x,y))
    d.text((x, y-20), str(i+1), fill='black')
contact.save(OUT / 'contact.png')
(OUT / 'render_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
print('DONE', len(pngs), flush=True)
