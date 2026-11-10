"""Offline S1 product and decision front; validates layout, not business truth."""
import base64
import html
import io
import re
from html.parser import HTMLParser
from pathlib import Path

LABELS={'invest':'建议投入','watch':'观望，暂不投入','reject':'不建议直接投入','no_data':'数据不足，禁止决策'}
CSS='''
.s1front{margin:18px 0;padding:22px;background:var(--card,#fff);border:1px solid var(--line,#ddd);border-radius:12px}
.s1front h2{margin:0 0 14px;font-size:22px}.s1front-grid{display:grid;grid-template-columns:minmax(220px,32%) minmax(0,1fr);gap:24px;align-items:start}
.s1front figure{margin:0;min-width:0}.s1front img{display:block;width:100%;height:220px;object-fit:contain;background:#f5f7fa;border-radius:8px}
.s1front figcaption{font-size:12px;line-height:1.6;color:#526277;margin-top:8px;overflow-wrap:anywhere}
.s1front .decision-label{font-size:24px;font-weight:800;line-height:1.35;color:#a35407}.s1front[data-verdict="invest"] .decision-label{color:#0f766e}
.s1front[data-verdict="reject"] .decision-label{color:#b42318}.s1front p{margin:9px 0}.s1front .front-meta{color:#526277;font-size:13px}
.s1front .missing-image{border:2px dashed #c78b38;padding:24px;background:#fff7e7;min-height:140px}
@media(max-width:700px){.s1front{padding:16px}.s1front-grid{grid-template-columns:1fr;gap:12px}.s1front .decision-copy{grid-row:1}.s1front img{height:190px}}
'''


def _image(path):
    raw=Path(path).read_bytes()
    if len(raw)>8_000_000:raise ValueError('首屏图片超过 8MB，请先另存适当尺寸的副本')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
    elif raw.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
    elif raw.startswith((b'GIF87a',b'GIF89a')):mime='image/gif'
    elif raw.startswith(b'RIFF') and raw[8:12]==b'WEBP':mime='image/webp'
    else:raise ValueError('首屏图片需要本地 PNG/JPEG/GIF/WebP，不接受网页或 SVG 代替')
    # Offline derivative only: preserve the user's source image byte-for-byte.
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(raw)) as original:
        if max(original.size) > 800 or len(raw) > 150 * 1024:
            picture = ImageOps.exif_transpose(original).convert('RGB')
            picture.thumbnail((800, 800))
            for width in (800, 700, 600):
                picture.thumbnail((width, width))
                for quality in (85, 75, 65, 55, 45):
                    out = io.BytesIO()
                    picture.save(out, format='JPEG', quality=quality, optimize=True)
                    raw = out.getvalue()
                    if len(raw) <= 150 * 1024: break
                if len(raw) <= 150 * 1024: break
            if len(raw) > 150 * 1024: raise ValueError('图片压缩未达到 150KB，请提供较小的研究参考图')
            mime = 'image/jpeg'
    return 'data:'+mime+';base64,'+base64.b64encode(raw).decode()


def render_front(*,product_name,product_id,image_path,image_source,image_role,verdict,
                 conclusion,next_action,evidence_date,blockers=(),scope='production',missing_image_reason='',specifications=()):
    for label,value in [('产品名',product_name),('产品标识',product_id),('图源',image_source),('结论',conclusion),('下一步',next_action),('数据日期',evidence_date)]:
        if not isinstance(value,str) or not value.strip():raise ValueError('首屏缺少'+label)
    if verdict not in LABELS:raise ValueError('首屏判定枚举无效')
    if scope not in {'production','demo'}:raise ValueError('首屏运行范围应为 production 或 demo')
    if image_role not in {'selected','reference','specification'}:raise ValueError('图片需标记 selected、reference 或 specification')
    if scope=='demo' and verdict=='invest':raise ValueError('演示研究不能标记为建议投入或采购放行')
    if verdict=='invest' and blockers:raise ValueError('存在未闭环条件时不能标记建议投入')
    if image_role == 'specification' and (image_path or not specifications):raise ValueError('设计规格卡须有规格且不可夹带参考货源图')
    if not image_path and not missing_image_reason and image_role != 'specification':raise ValueError('首屏缺图必须说明待补事项')
    e=lambda x:html.escape(str(x),quote=True)
    role='选中产品图' if image_role=='selected' else '参考图（非已确认的选中 SKU 实物）'
    if image_role == 'specification':
        role = '设计规格（采购 SKU 尚未确定）'
        picture = '<div class="specification-card" data-front="specification"><strong>设计规格</strong><ul>' + ''.join('<li>'+e(x)+'</li>' for x in specifications) + '</ul></div>'
    elif image_path:
        picture='<img src="'+_image(image_path)+'" alt="'+e(product_name+' · '+role)+'">'
    else:
        picture='<div class="missing-image">缺少产品图片：'+e(missing_image_reason)+'<br>报告首屏待补图，不计为完整交付。</div>'
    problems='；'.join(str(x) for x in blockers) if blockers else '未另列阻塞项；仍按正文证据和验收范围执行'
    extra = ''
    if len(blockers) > 3 or len(problems) > 200:
        categories = list(dict.fromkeys(str(x).split('：',1)[0] for x in blockers))
        summary = '、'.join(x for x in categories if len(x) <= 12)
        problems = (summary + '等证据待核；' if summary else '') + '共 ' + str(len(blockers)) + ' 项待解决。'
        extra = '<details open data-initial-open="false"><summary>查看全部 '+str(len(blockers))+' 项条件与缺口</summary><ul>'+''.join('<li>'+e(x)+'</li>' for x in blockers)+'</ul></details>'
    return ('<section id="s1-product-decision" class="s1front" data-verdict="'+verdict+'" data-scope="'+scope+'">'
        '<h2>'+('目标产品' if image_role == 'specification' else '选中产品')+' · '+e(product_name)+'</h2><div class="s1front-grid"><figure>'+picture+
        '<figcaption>'+role+'<br>图片来源：'+e(image_source)+'<br>仅用于产品研究说明，非 Listing 图片授权。</figcaption></figure>'
        '<div class="decision-copy"><div class="front-meta">'+e(product_id)+' · 数据日期 '+e(evidence_date)+'</div>'
        '<p class="front-meta">'+('演示研究 · 不构成商业验收或采购放行' if scope=='demo' else '实际业务研究 · 以下结论受证据和前提约束')+'</p>'
        '<div class="decision-label">是否可以做：'+LABELS[verdict]+'</div>'
        '<p data-front="conclusion"><strong>一句话结论：</strong>'+e(conclusion)+'</p>'
        '<p data-front="blockers"><strong>条件与缺口：</strong>'+e(problems)+'</p>'+extra+
        '<p data-front="next"><strong>当前允许的下一步：</strong>'+e(next_action)+'</p>'
        '</div></div></section>')


class FrontParser(HTMLParser):
    def __init__(self):
        super().__init__();self.sections=[];self.depth=0;self.attrs={};self.text=[];self.images=[];self.fields=set();self.hidden=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='section':
            self.sections.append(attrs.get('id'))
            if attrs.get('id')=='s1-product-decision':self.depth=1;self.attrs=attrs
            elif self.depth:self.depth+=1
        if self.depth:
            if tag=='img':self.images.append(attrs)
            if attrs.get('data-front'):self.fields.add(attrs['data-front'])
            style=attrs.get('style','').replace(' ','').lower()
            if 'hidden' in attrs or 'display:none' in style or 'visibility:hidden' in style:self.hidden=True
    def handle_endtag(self,tag):
        if tag=='section' and self.depth:self.depth-=1
    def handle_data(self,text):
        if self.depth:self.text.append(text)


def inspect_front(text):
    if 'data-report-version="3"' in text:
        import sys
        scripts=str(Path(__file__).resolve().parents[3]/'scripts')
        if scripts not in sys.path:sys.path.insert(0,scripts)
        from s1_report_v3 import inspect_html
        return inspect_html(text)
    if 'data-report-version="2"' in text:
        from s1_report_v2 import inspect_html
        return inspect_html(text)
    p=FrontParser();p.feed(text);errors=[]
    if not p.sections or p.sections[0]!='s1-product-decision':errors.append('产品与结论首屏必须是正文第一个 section')
    if p.sections.count('s1-product-decision')!=1:errors.append('缺少或重复产品决策首屏')
    if p.hidden:errors.append('首屏关键内容被隐藏')
    if p.attrs.get('data-verdict') not in LABELS:errors.append('缺少明确可行性判定')
    if not {'conclusion','blockers','next'}.issubset(p.fields):errors.append('缺一句话结论、条件缺口或允许的下一步')
    visible=''.join(p.text)
    if '图片来源' not in visible or '是否可以做' not in visible:errors.append('首屏未标注图片来源或是否可做')
    valid_image=False
    for image in p.images:
        source=image.get('src','');m=re.fullmatch(r'data:image/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/=]+)',source)
        try:
            if m and base64.b64decode(m.group(2),validate=True) and image.get('alt'):valid_image=True
        except ValueError:pass
    if not valid_image and 'specification' not in p.fields:errors.append('首屏缺少可离线显示且带说明的产品图片')
    return errors
