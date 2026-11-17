import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { deflateRawSync } from 'node:zlib';

const work = path.dirname(fileURLToPath(import.meta.url));
const root = path.dirname(work);
const source = '5조 고무줄 총 자동화 시스템 개발 보고서.md';
if (!source) throw new Error('Markdown source missing');
const md = fs.readFileSync(path.join(root, source), 'utf8');
const output = path.join(root, '5조 고무줄 총 자동화 시스템 개발 보고서.docx');
const contentWidth = 9072;
const xml = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const plain = s => s.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replaceAll('**','').replaceAll('`','').trim();
const parts = new Map();
const envelope = s => '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'+s;
const relationships = [
  ['rIdStyles','styles','styles.xml'],['rIdNumbering','numbering','numbering.xml'],
  ['rIdSettings','settings','settings.xml'],['rIdFooter','footer','footer1.xml'],
];

function runs(text, style='') {
  text = text.replace(/\[([^\]]+)\]\([^)]+\)/g,'$1');
  return text.split(/(\*\*.*?\*\*|`[^`]+`)/g).filter(Boolean).map(t => {
    const bold = t.startsWith('**') && t.endsWith('**');
    const code = t.startsWith('`') && t.endsWith('`');
    const value = bold ? t.slice(2,-2) : code ? t.slice(1,-1) : t;
    const pr = (bold?'<w:b/>':'')+(code?'<w:rStyle w:val="CodeChar"/>':'')+style;
    return `<w:r>${pr?`<w:rPr>${pr}</w:rPr>`:''}<w:t xml:space="preserve">${xml(value)}</w:t></w:r>`;
  }).join('');
}
function para(text, style='Normal', extra='') {
  return `<w:p><w:pPr><w:pStyle w:val="${style}"/>${extra}</w:pPr>${runs(text)}</w:p>`;
}
function listPara(text, numbered) {
  return para(text,'ListParagraph',`<w:numPr><w:ilvl w:val="0"/><w:numId w:val="${numbered?2:1}"/></w:numPr>`);
}
const body=[];
let tables=0;
function table(data) {
  data=data.map(r=>r.map(plain));
  const cols=data[0].length;
  const prose=cols>=4 || (cols===2 && data.length<=6 && data[0][1]!=='저장된 결과') || (cols===3 && data.length>7) || data[0].includes('실제 모델 파일') || data.slice(1).some(r=>r.some(c=>c.length>100));
  if(prose) {
    for (const row of data.slice(1)) {
      body.push(para(row[0],'Heading3'));
      for(let c=1;c<row.length;c++) body.push(para(`**${data[0][c]}:** ${row[c]}`));
    }
    return;
  }
  const widths = cols===3 ? (data[0][0]==='축' ? [1600,3736,3736] : data[0][0]==='구성' ? [2200,3000,contentWidth-5200] : [2600,2200,contentWidth-4800]) : [3700,contentWidth-3700];
  const borders=['top','left','bottom','right','insideH','insideV'].map(x=>`<w:${x} w:val="single" w:sz="4" w:color="D9D9D9"/>`).join('');
  let t=`<w:tbl><w:tblPr><w:tblW w:w="${contentWidth}" w:type="dxa"/><w:tblInd w:w="110" w:type="dxa"/><w:tblLayout w:type="fixed"/><w:tblBorders>${borders}</w:tblBorders><w:tblCellMar><w:top w:w="90" w:type="dxa"/><w:left w:w="110" w:type="dxa"/><w:bottom w:w="90" w:type="dxa"/><w:right w:w="110" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>${widths.map(w=>`<w:gridCol w:w="${w}"/>`).join('')}</w:tblGrid>`;
  data.forEach((row,r)=>{
    t+=`<w:tr><w:trPr>${r===0?'<w:tblHeader/>':''}<w:cantSplit/></w:trPr>`;
    row.forEach((cell,c)=>{
      t+=`<w:tc><w:tcPr><w:tcW w:w="${widths[c]}" w:type="dxa"/><w:vAlign w:val="center"/>${r===0?'<w:shd w:fill="E7ECF0"/>':''}</w:tcPr>`;
      const align=r===0 || (c===0 && tables===0) || (c===1 && tables===1) ? 'center':'left';
      t+=para(r===0?`**${cell}**`:cell,'TableText',`<w:jc w:val="${align}"/>`);
      t+='</w:tc>';
    });
    t+='</w:tr>';
  });
  t+='</w:tbl>';
  body.push(t,para('','TableGap'));
  tables++;
}
let figureCount=0;
function picture(imagePath, description) {
  const resolved=path.resolve(root,imagePath);
  if(!resolved.startsWith(root+path.sep))throw new Error('Image outside report workspace');
  const bytes=fs.readFileSync(resolved);
  if(bytes.toString('hex',0,8)!=='89504e470d0a1a0a')throw new Error('Expected PNG diagram');
  const width=bytes.readUInt32BE(16), height=bytes.readUInt32BE(20);
  // Keep the first hardware photograph within the remaining first-page space.
  // Other diagrams retain their larger size so the architecture labels remain legible.
  const cx=figureCount===0?3960000:5600000, cy=Math.round(cx*height/width);
  const id=++figureCount, rid=`rIdFigure${id}`, filename=path.basename(resolved);
  relationships.push([rid,'image',`media/${filename}`]);
  parts.set(`word/media/${filename}`,bytes);
  body.push(`<w:p><w:pPr><w:pStyle w:val="Figure"/><w:keepNext/></w:pPr><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="${cx}" cy="${cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="${id}" name="Figure ${id}" descr="${xml(description)}"/><wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="0" name="${xml(filename)}"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="${rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${cx}" cy="${cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>`);
}
const lines=md.split(/\r?\n/);
for(let i=0;i<lines.length;) {
  let s=lines[i].trim();
  if(!s) {i++;continue;}
  const image=s.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
  if(image) {picture(image[2],image[1]);i++;continue;}
  if(s.startsWith('```')) {
    const lang=s.slice(3).trim(), code=[];
    for(i++;i<lines.length&&!lines[i].trim().startsWith('```');i++)code.push(lines[i]);
    i++;
    if(lang==='mermaid') throw new Error('Use a verified image for report diagrams');
    else code.forEach((t,n)=>body.push(para(t||' ','CodeBlock',n<code.length-1?'<w:keepNext/>':'')));
    continue;
  }
  if(s.startsWith('|')) {
    const data=[];
    for(;i<lines.length&&lines[i].trim().startsWith('|');i++) {
      const row=lines[i].trim().replace(/^\||\|$/g,'').split('|').map(x=>x.trim());
      if(!row.every(x=>/^:?-+:?$/.test(x))) data.push(row);
    }
    table(data);continue;
  }
  const h=s.match(/^(#{1,4})\s+(.+)$/);
  if(h) {
    const level=h[1].length;
    if(level===1) {
      body.push(para('고무줄 총 자동화 시스템 개발 보고서','Title'));
      body.push(para('세 가지 구현 방법과 최종 적용 모델','Subtitle'));
    } else body.push(para(h[2],level===2?'Heading1':level===3?'Heading2':'Heading3',level===2&&h[2].startsWith('5. ')?'<w:pageBreakBefore/>':''));
  } else if(/^[-*]\s+/.test(s)) body.push(listPara(s.replace(/^[-*]\s+/,''),false));
  else if(/^\d+\.\s+/.test(s)) body.push(listPara(s.replace(/^\d+\.\s+/,''),true));
  else if(/^그림 \d+\./.test(s))body.push(para(s,'Caption'));
  else if(/^5조\s/.test(s))body.push(para(s,'Metadata'));
  else body.push(para(s));
  i++;
}
const fonts=(face='바탕',size=22,bold=false)=>`<w:rPr><w:rFonts w:ascii="${face==='바탕'?'Times New Roman':'Arial'}" w:hAnsi="${face==='바탕'?'Times New Roman':'Arial'}" w:eastAsia="${face}" w:cs="Arial"/><w:color w:val="000000"/><w:sz w:val="${size}"/><w:szCs w:val="${size}"/>${bold?'<w:b/>':''}<w:lang w:val="en-US" w:eastAsia="ko-KR"/></w:rPr>`;
function style(id,name,run,props,based='Normal') {
  return `<w:style w:type="paragraph" w:styleId="${id}"><w:name w:val="${name}"/>${id!=='Normal'?`<w:basedOn w:val="${based}"/>`:''}<w:next w:val="Normal"/><w:qFormat/><w:pPr>${props}</w:pPr>${run}</w:style>`;
}
const styles=[
  style('Normal','Normal',fonts(),'<w:spacing w:before="0" w:after="80" w:line="384" w:lineRule="auto"/><w:jc w:val="both"/><w:widowControl/>'),
  style('Title','Title',fonts('맑은 고딕',38,true),'<w:keepNext/><w:jc w:val="center"/><w:spacing w:before="0" w:after="100" w:line="300" w:lineRule="auto"/>'),
  style('Subtitle','Subtitle',fonts('맑은 고딕',24),'<w:keepNext/><w:jc w:val="center"/><w:spacing w:before="0" w:after="140" w:line="300" w:lineRule="auto"/>'),
  style('Metadata','Metadata',fonts('맑은 고딕',20),'<w:keepNext/><w:jc w:val="center"/><w:spacing w:before="0" w:after="140" w:line="280" w:lineRule="auto"/>'),
  style('Heading1','heading 1',fonts('맑은 고딕',30,true),'<w:keepNext/><w:keepLines/><w:jc w:val="left"/><w:outlineLvl w:val="0"/><w:spacing w:before="280" w:after="140" w:line="310" w:lineRule="auto"/>'),
  style('Heading2','heading 2',fonts('맑은 고딕',24,true),'<w:keepNext/><w:keepLines/><w:jc w:val="left"/><w:outlineLvl w:val="1"/><w:spacing w:before="200" w:after="100" w:line="300" w:lineRule="auto"/>'),
  style('Heading3','heading 3',fonts('맑은 고딕',22,true),'<w:keepNext/><w:keepLines/><w:jc w:val="left"/><w:outlineLvl w:val="2"/><w:spacing w:before="140" w:after="60" w:line="300" w:lineRule="auto"/>'),
  style('ListParagraph','List Paragraph',fonts(),'<w:spacing w:before="0" w:after="80" w:line="384" w:lineRule="auto"/><w:ind w:left="360" w:hanging="220"/>'),
  style('TableText','Table Text',fonts('바탕',20),'<w:spacing w:before="0" w:after="0" w:line="312" w:lineRule="auto"/><w:jc w:val="left"/>'),
  style('TableGap','Table Gap',fonts('바탕',4),'<w:spacing w:before="0" w:after="80" w:line="60" w:lineRule="exact"/>'),
  style('CodeBlock','Code Block',fonts('맑은 고딕',19),'<w:jc w:val="left"/><w:spacing w:before="20" w:after="20" w:line="312" w:lineRule="auto"/><w:ind w:left="160"/>'),
  style('Figure','Figure',fonts('바탕',20),'<w:keepNext/><w:jc w:val="center"/><w:spacing w:before="160" w:after="60" w:line="240" w:lineRule="auto"/>'),
  style('Caption','Caption',fonts('맑은 고딕',18),'<w:jc w:val="center"/><w:spacing w:before="0" w:after="120" w:line="280" w:lineRule="auto"/>'),
].join('');
parts.set('word/styles.xml',envelope(`<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults><w:rPrDefault>${fonts()}</w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="384" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>${styles}<w:style w:type="character" w:styleId="CodeChar"><w:name w:val="Code Character"/>${fonts('맑은 고딕',19)}</w:style></w:styles>`));
parts.set('word/document.xml',envelope(`<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>${body.join('')}<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1417" w:bottom="1134" w:left="1417" w:header="567" w:footer="567" w:gutter="0"/><w:cols w:space="720"/><w:docGrid w:type="default" w:linePitch="0"/></w:sectPr></w:body></w:document>`));
parts.set('word/settings.xml',envelope('<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:zoom w:percent="100"/><w:defaultTabStop w:val="720"/><w:characterSpacingControl w:val="doNotCompress"/><w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat><w:updateFields w:val="true"/></w:settings>'));
parts.set('word/footer1.xml',envelope('<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:pPr><w:jc w:val="center"/><w:spacing w:before="0" w:after="0"/></w:pPr><w:r>'+fonts('바탕',18)+'<w:t>- </w:t></w:r><w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple><w:r><w:t> -</w:t></w:r></w:p></w:ftr>'));
const numbering=[['bullet','•'],['decimal','%1.']].map(([fmt,marker],n)=>`<w:abstractNum w:abstractNumId="${n}"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="${fmt}"/><w:lvlText w:val="${marker}"/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="360"/></w:tabs><w:ind w:left="360" w:hanging="220"/></w:pPr>${fonts('맑은 고딕',22)}</w:lvl></w:abstractNum>`).join('');
parts.set('word/numbering.xml',envelope(`<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">${numbering}<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>`));
parts.set('word/_rels/document.xml.rels',envelope(`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${relationships.map(([id,type,target])=>`<Relationship Id="${id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/${type}" Target="${target}"/>`).join('')}</Relationships>`));
parts.set('_rels/.rels',envelope('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>'));
parts.set('docProps/core.xml',envelope('<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>고무줄 총 자동화 시스템 개발 보고서</dc:title><dc:subject>세 가지 구현 방법과 최종 적용 모델</dc:subject></cp:coreProperties>'));
const overrides=[['document','application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'],['styles','application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml'],['numbering','application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml'],['settings','application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml'],['footer1','application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml']];
parts.set('[Content_Types].xml',envelope(`<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>${overrides.map(([n,type])=>`<Override PartName="/word/${n}.xml" ContentType="${type}"/>`).join('')}<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>`));

// Deterministic OOXML packaging using only the Codex Node runtime.
const crcTable=Array.from({length:256},(_,i)=>{let c=i;for(let j=0;j<8;j++)c=(c&1)?0xedb88320^(c>>>1):c>>>1;return c>>>0;});
function crc32(b){let c=0xffffffff;for(const v of b)c=crcTable[(c^v)&255]^(c>>>8);return (c^0xffffffff)>>>0;}
const locals=[],central=[];let offset=0;
for(const [name,value] of parts) {
  const data=Buffer.isBuffer(value)?value:Buffer.from(value,'utf8');
  const filename=Buffer.from(name,'utf8'), compressed=deflateRawSync(data), crc=crc32(data);
  const local=Buffer.alloc(30);local.writeUInt32LE(0x04034b50,0);local.writeUInt16LE(20,4);local.writeUInt16LE(0x800,6);local.writeUInt16LE(8,8);local.writeUInt32LE(crc,14);local.writeUInt32LE(compressed.length,18);local.writeUInt32LE(data.length,22);local.writeUInt16LE(filename.length,26);
  locals.push(local,filename,compressed);
  const entry=Buffer.alloc(46);entry.writeUInt32LE(0x02014b50,0);entry.writeUInt16LE(20,4);entry.writeUInt16LE(20,6);entry.writeUInt16LE(0x800,8);entry.writeUInt16LE(8,10);entry.writeUInt32LE(crc,16);entry.writeUInt32LE(compressed.length,20);entry.writeUInt32LE(data.length,24);entry.writeUInt16LE(filename.length,28);entry.writeUInt32LE(offset,42);
  central.push(entry,filename);offset+=local.length+filename.length+compressed.length;
}
const directory=Buffer.concat(central),end=Buffer.alloc(22);end.writeUInt32LE(0x06054b50,0);end.writeUInt16LE(parts.size,8);end.writeUInt16LE(parts.size,10);end.writeUInt32LE(directory.length,12);end.writeUInt32LE(offset,16);
fs.writeFileSync(output,Buffer.concat([...locals,directory,end]));
const audit={output,source,tables,figures:figureCount,paragraphs:body.filter(s=>s.startsWith('<w:p>')).length,mainHeadings:(body.join('').match(/w:pStyle w:val="Heading1"/g)||[]).length,bodyFont:'바탕',bodyPt:11,lineSpacingPercent:160,parts:parts.size,bytes:fs.statSync(output).size};
if(audit.mainHeadings!==6)throw new Error('Expected hardware introduction and five report sections');
if(audit.figures!==5)throw new Error('Expected hardware image, overall diagram, and three module diagrams');
fs.writeFileSync(path.join(work,'word_audit.json'),JSON.stringify(audit,null,2));
console.log(JSON.stringify(audit,null,2));
