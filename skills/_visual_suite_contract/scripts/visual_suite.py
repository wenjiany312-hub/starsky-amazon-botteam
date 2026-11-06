"""R03 inventory/planning inside the existing visual-suite contract.

Host reviews supply semantic labels; scanning alone never claims an image has
been understood. All variants share seven buyer tasks and a confirmed manual.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

import yaml
from PIL import Image

HOME=Path(__file__).resolve().parents[3]
IMAGE={'.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff'}
VIDEO={'.mp4','.mov','.avi','.webm','.mkv'}
RANK={'user':1,'manufacturer':2,'1688':3,'competitor':4}
TAGS={'装载效果','使用场景','结构','细节','尺寸规格','包装','工厂','重复'}
DEFAULT_ROOTS=[{'path':'S1_选品板块/1688-product-detail-scraper','kind':'1688'},
               {'path':'00_产品资料/补充图片/自拍','kind':'user'},
               {'path':'00_产品资料/补充图片/厂家','kind':'manufacturer'},
               {'path':'00_产品资料/状态截帧','kind':'manufacturer'}]


def helper(name):
    path=HOME/'scripts'/(name+'.py')
    spec=importlib.util.spec_from_file_location('_suite_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def folder(root):return Path(root)/'S3_视觉板块/amazon-listing-image-designer'


def safe(root,relative):
    root=Path(root).resolve();path=(root/relative).resolve();path.relative_to(root);return path


def inventory(root,*,roots=None,annotations=None,requirements=None):
    root=Path(root).resolve();annotations=annotations or {};assets={};errors=[]
    previous_path=folder(root)/'素材盘点.yaml'
    prior_document=yaml.safe_load(previous_path.read_bytes()) if previous_path.exists() else {}
    roots=roots if roots is not None else prior_document.get('scan_roots',DEFAULT_ROOTS)
    requirements=requirements if requirements is not None else prior_document.get('requirements',[])
    previous={a['sha256']:a for a in prior_document.get('assets') or []}
    for source in roots:
        base=safe(root,source['path']);kind=source['kind']
        if kind not in RANK:raise ValueError('素材来源类型不明：'+str(kind))
        if not base.exists():
            if kind in {'user','manufacturer'}:base.mkdir(parents=True,exist_ok=True)
            continue
        for path in sorted(base.rglob('*')):
            if not path.is_file() or path.suffix.lower() not in IMAGE|VIDEO or any(p.startswith('.') for p in path.relative_to(base).parts):continue
            path.resolve().relative_to(root)
            relative=path.relative_to(root).as_posix();fingerprint=sha(path)
            if relative.startswith(('S3_视觉板块/','00_产品资料/视频对照表/')):continue
            label=annotations.get(relative,annotations.get(fingerprint,annotations.get(path.name,{})))
            tags=label.get('tags') or []
            if set(tags)-TAGS:raise ValueError('未知素材标签：'+str(set(tags)-TAGS))
            item={'path':relative,'sha256':fingerprint,'kind':kind}
            old=previous.get(fingerprint,{})
            entry=assets.setdefault(fingerprint,{'asset_id':'SRC-'+fingerprint[:16],'sha256':fingerprint,
                'sources':[],'tags':old.get('tags',[])[:],'risks':old.get('risks',[])[:],
                'value':old.get('value','unreviewed'),**({'variant':old['variant']} if old.get('variant') else {}),
                'media_type':'video' if path.suffix.lower() in VIDEO else 'image'})
            if item not in entry['sources']:entry['sources'].append(item)
            entry['tags']=sorted(set(entry['tags'])|set(tags));entry['risks']=sorted(set(entry['risks'])|set(label.get('risks') or []))
            if label.get('value'):entry['value']=label['value']
            if label.get('use_value'):entry['use_value']=label['use_value']
            elif old.get('use_value'):entry['use_value']=old['use_value']
            if label.get('variant'):entry['variant']=label['variant']
            if label.get('views'):entry['views']=label['views']
            elif old.get('views'):entry['views']=old['views']
            if entry['media_type']=='image':
                try:
                    with Image.open(path) as image:entry['size']=list(image.size)
                except (OSError,ValueError) as error:errors.append(relative+': '+str(error))
            elif 'sheets' not in entry:
                output=root/'00_产品资料/视频对照表'/fingerprint[:16]
                try:
                    entry['sheets']=[p.relative_to(root).as_posix() for p in helper('video_keyframes').sheet(path,output)]
                    entry['frame_evidence']=(output/'sheet_evidence.json').relative_to(root).as_posix()
                except (OSError,ValueError) as error:
                    errors.append(relative+': '+str(error));entry['video_status']='needs_decode'
    rows=[]
    for entry in assets.values():
        entry['sources'].sort(key=lambda s:(RANK[s['kind']],s['path']))
        best=entry['sources'][0];entry.update(path=best['path'],source_kind=best['kind'])
        rows.append(entry)
    rows.sort(key=lambda r:r['asset_id'])
    gaps=[r for r in requirements if not any(r['tag'] in a['tags']
        and (not r.get('variant') or a.get('variant')==r['variant'])
        and (not r.get('view') or r['view'] in a.get('views',[])) for a in rows)]
    result={'schema_version':'r03.s3','assets':rows,'scan_roots':roots,'requirements':list(requirements),'decode_errors':errors,'gaps':gaps,
        'status':'needs_supplement' if gaps or errors else 'scanned_requires_visual_review' if any(a['value']=='unreviewed' for a in rows) else 'reviewed',
        'choices':['自己拍','找厂家','先用现有素材继续'],'inventory_digest':digest(rows)}
    out=folder(root)/'素材盘点.yaml';out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(yaml.safe_dump(result,allow_unicode=True,sort_keys=False),encoding='utf8')
    report_path=HOME/'skills/github-tabler-html-report/assets/salon_report.py'
    spec=importlib.util.spec_from_file_location('_suite_inventory_report',report_path)
    renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
    report=renderer.Report('素材盘点与补图清单',stage='S3')
    section=report.section('inventory','本次素材')
    section.kpis([('素材',str(len(rows)),None,'blue'),('待补画面',str(len(gaps)),None,'warn')])
    section.table(['文件','来源','分类','可用价值','风险'],[[a['path'],a['source_kind'],' / '.join(a['tags']) or '待查看',
        a.get('use_value') or a['value'],' / '.join(a['risks']) or '未记录风险，不等于已验收'] for a in rows])
    if gaps or errors:
        report.section('gaps','缺哪些画面').table(['缺口','处理'],[[r.get('description') or r['tag'],'自己拍 / 找厂家 / 先用现有素材继续'] for r in gaps]+[[error,'先处理解码或明确按缺失证据继续'] for error in errors])
    (out.parent/'素材盘点.html').write_text(report.render(),encoding='utf8')
    if gaps or errors or (root/'00_产品资料/拍摄清单.md').exists():
        shooting=root/'00_产品资料/拍摄清单.md';shooting.parent.mkdir(parents=True,exist_ok=True)
        text=shooting.read_text(encoding='utf8') if shooting.exists() else '# 共用拍摄清单\n\n1080p 以上、固定俯拍、干净浅色桌面、每个动作放慢。\n'
        text=re.sub(r'\n<!-- r03-inventory-start -->.*?<!-- r03-inventory-end -->\n','\n',text,flags=re.S)
        if gaps or errors:
            text+='\n<!-- r03-inventory-start -->\n## 素材角度与尺寸缺口\n\n'
            text+='\n'.join('- '+str(r.get('description') or r['tag'])+('（'+str(r['variant'])+'）' if r.get('variant') else '') for r in gaps)
            text+='\n'+'\n'.join('- 视频未解码：'+e for e in errors)+'\n<!-- r03-inventory-end -->\n'
        shooting.write_text(text,encoding='utf8')
    return result


def _manual(root):return helper('product_manual').read_confirmed(root)


SIZE_TAIL=re.compile(r'(?i)[\s\-_/·]*(?<![a-z])(?:\d*x{0,3}[sml]|[2-9]?x{1,3}l|one\s*size|均码|小号|中号|大号|特大号|\d+(?:\.\d+)?\s*(?:cm|mm|in|inch|码|号))$')


def collapse_size_variants(config):
    """Variants that differ only by size share one image suite (Amazon children of one colour share images).
    `variant_colors` may map each variant to its colour; otherwise a trailing size token is stripped."""
    variants=list(config.get('variants') or [])
    mapping=config.get('variant_colors') or {v:(SIZE_TAIL.sub('',str(v)).strip() or str(v)) for v in variants}
    colors=list(dict.fromkeys(mapping.get(v,v) for v in variants))
    if len(colors)==len(variants):return config
    members={c:[v for v in variants if mapping.get(v,v)==c] for c in colors}
    skus=config.get('sku_ids') or {}
    config.update(size_variants=variants,variant_colors={v:mapping.get(v,v) for v in variants},variants=colors,
                  sku_ids={c:skus.get(members[c][0],members[c][0]) for c in colors},
                  sku_ids_by_color={c:[skus.get(v,v) for v in members[c]] for c in colors})
    return config


CONTENT_KEYS=('slot_id','buyer_question','pad_asset_id','state_ids','action_ids','structure_ids','on_image_copy','props','module_kind','qa')


def content_digest(data):
    """What the user actually approved: image tasks, copy, pads, structure and colours. Technical fields
    (field names, keyword bookkeeping, hashes) can change without asking for a new confirmation."""
    view={'variants':data.get('variants'),'aplus_path':data.get('aplus_path'),
          **{key:[{k:slot.get(k) for k in CONTENT_KEYS if slot.get(k) not in (None,'',[])} for slot in data.get(key) or []] for key in ('slots','aplus_slots')}}
    return digest(view)


def preselect_keywords(root,config):
    """Fill keyword_embed (on-image keywords) from licensed words the approved copy already contains; never fail."""
    try:
        kw=helper('keyword_asset');series=Path(config.get('series_root') or root).resolve();snapshot=kw.read_snapshot(series)
        variant=(config.get('variants') or [''])[0];sku=(config.get('sku_ids') or {}).get(variant,variant)
        allowed=[r for r in snapshot.select_many(lists=('master',),sku=sku,copy_allowed=True,limit=60)['master']
                 if r['marketplace']==str(config.get('marketplace','US')).upper()]
    except Exception:
        return config
    for slot in (config.get('slots') or [])+(config.get('aplus_slots') or []):
        if slot.get('slot_id')=='IMG01':continue
        if not slot.get('keyword_embed') and slot.get('on_image_keywords'):slot['keyword_embed']=list(slot['on_image_keywords'])
        if slot.get('keyword_embed'):continue
        copy=kw._normalize(slot.get('on_image_copy') or '')
        hits=sorted((r['term'] for r in allowed if r['term_normalized'] and r['term_normalized'] in copy),key=len,reverse=True)[:2]
        if hits:slot['keyword_embed']=hits
    return config


def _inventory(root):
    path=folder(root)/'素材盘点.yaml';data=yaml.safe_load(path.read_bytes())
    if data['inventory_digest']!=digest(data['assets']):raise ValueError('素材盘点指纹不一致，需要重扫')
    for asset in data['assets']:
        for source in asset['sources']:
            if sha(safe(root,source['path']))!=source['sha256']:raise ValueError('素材来源已变化，需要重扫：'+source['path'])
    return data,path


def _check(root,config):
    _manual(root);inventory,path=_inventory(root)
    slots=config.get('slots') or [];modules=config.get('aplus_slots') or []
    if len(slots)!=7 or [s.get('slot_id') for s in slots]!=[f'IMG{i:02}' for i in range(1,8)]:raise ValueError('主副图必须是共用的 IMG01–07 七图任务表')
    questions=[re.sub(r'\W+','',str(s.get('buyer_question') or '')).casefold() for s in slots]
    if any(not q for q in questions) or len(set(questions))!=7:raise ValueError('买家问题缺失或重复；每图必须回答不同问题')
    if not config.get('variants'):raise ValueError('需要明确本次颜色变体；所有颜色共用七图任务表')
    if config.get('aplus_path','premium') not in {'premium','standard'}:raise ValueError('A+ 类型无效')
    if config.get('aplus_path')=='standard' and not (config.get('aplus_choice') or {}).get('source')=='user':raise ValueError('普通 A+ 仅按用户明确要求，不能自动降级')
    if config.get('aplus_path','premium')=='premium' and len(modules)<5:raise ValueError('高级 A+ 模块表最少 5 个模块，默认规划 7 个')
    if config.get('aplus_path')=='standard' and (not modules or any(not isinstance(m.get('canvas'),list) or len(m['canvas'])!=2 or not all(type(n) is int and 100<=n<=5000 for n in m['canvas']) for m in modules)):
        raise ValueError('普通 A+ 请沿用原标准模块表并写明已确认尺寸，不套高级模块数/双端尺寸')
    ids=[s.get('slot_id') for s in modules]
    if len(set(ids))!=len(ids) or not all(ids):raise ValueError('A+ 模块 ID 缺失或重复')
    if slots[0].get('on_image_copy'):raise ValueError('主图必须白底无字')
    if slots[0].get('props'):raise ValueError('主图不放未附送道具；道具展示放副图并标免责小字')
    assets={a['asset_id']:a for a in inventory['assets']}
    for slot in slots+modules:
        if not slot.get('buyer_question'):raise ValueError('模块缺少明确买家问题')
        if slot.get('pad_asset_id') not in assets:raise ValueError('任务垫图不在真实盘点表：'+str(slot.get('slot_id')))
        if assets[slot['pad_asset_id']]['value']=='unreviewed':raise ValueError('所选垫图尚未实际查看和标注：'+slot['slot_id'])
        helper('product_manual').read_confirmed(root,required_states=slot.get('state_ids') or (),required_actions=slot.get('action_ids') or ())
    return inventory,path


def write_plan(root,config):
    root=Path(root).resolve();config=deepcopy(config)
    if not config.get('aplus_slots') and config.get('s2_qa_source') and config.get('slots'):
        config['aplus_slots']=default_aplus_modules(root,config['s2_qa_source'],config['slots'][0]['pad_asset_id'])
    config.setdefault('aplus_scope','parent')
    collapse_size_variants(config);preselect_keywords(root,config)
    inventory,path=_check(root,config)
    config.setdefault('aplus_production_version','r03.m2')
    config.update(schema_version='r03.s3',aplus_path=config.get('aplus_path','premium'),
        generation_mode='one_suite',manual_confirmation_sha256=sha(root/'00_产品资料/说明书确认.json'),
        inventory_sha256=sha(path))
    assets=HOME/'skills/github-tabler-html-report/assets/salon_report.py'
    spec=importlib.util.spec_from_file_location('_suite_plan_report',assets);renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
    report=renderer.Report('视觉总规划 · 确认页',stage='S3');section=report.section('overview','这一套要解决什么')
    if config.get('size_variants'):section.p('尺码 '+' / '.join(config['size_variants'])+' 只差尺寸，共用同一套图；尺码差异在尺码图里写明。')
    section.kpis([('颜色',' / '.join(config['variants']),None,'blue'),('主副图','7 图',None,'ok'),('A+ 模块',str(len(config['aplus_slots'])),None,'blue')])
    section.p('主图白底无字；一套七图任务供全部颜色共用。视频形态第 9 步再选，品牌故事/旗舰店在核心交付后按需做。')
    for key,title in [('slots','主副图任务表'),('aplus_slots','高级 A+ 模块表' if config['aplus_path']=='premium' else '普通 A+ 模块表')]:
        report.section(key,title).table(['图位','买家问题','垫图','状态/动作','图上文案','图上埋词'],
            [[s['slot_id'],s['buyer_question'],s['pad_asset_id'],' / '.join((s.get('state_ids') or [])+(s.get('action_ids') or [])),s.get('on_image_copy',''),
              '、'.join(s.get('keyword_embed') or []) or ('—' if s['slot_id']=='IMG01' else '未埋词（可选）')] for s in config[key]])
    out=folder(root);out.mkdir(parents=True,exist_ok=True);data=out/'visual_suite.yaml';page=out/'视觉总规划.html'
    data.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False),encoding='utf8');page.write_text(report.render(),encoding='utf8')
    proof=out/'视觉规划确认.json'
    if proof.exists():proof.unlink()
    return {'yaml':str(data),'html':str(page)}


def load_plan(root,*,require_confirmation=True):
    root=Path(root).resolve();out=folder(root);data=yaml.safe_load((out/'visual_suite.yaml').read_bytes())
    _,inventory=_check(root,data)
    if data.get('manual_confirmation_sha256')!=sha(root/'00_产品资料/说明书确认.json') or data.get('inventory_sha256')!=sha(inventory):raise ValueError('说明书或素材指纹已变化，规划需要重验')
    if require_confirmation:
        proof=out/'视觉规划确认.json'
        if not proof.exists():raise ValueError('视觉总规划尚未确认')
        receipt=json.loads(proof.read_bytes())
        if receipt.get('content_sha256'):
            if receipt['content_sha256']!=content_digest(data):raise ValueError('已确认规划的画面内容（图位、文案、垫图、结构或颜色）变化，需要重新确认')
        else:
            for name,expected in receipt['files_sha256'].items():
                if sha(safe(out,name))!=expected:raise ValueError('已确认规划内容变化，需要重新确认')
    return data


def confirm_plan(root,decision):
    helper('product_manual')
    if decision.get('source')!='user' or str(decision.get('message') or '').strip().lower() in {'','继续','好','ok','下一步'}:raise ValueError('需要用户明确确认当前视觉规划')
    data=load_plan(root,require_confirmation=False);out=folder(root)
    proof={'decision':decision,'content_sha256':content_digest(data),'files_sha256':{name:sha(out/name) for name in ('visual_suite.yaml','视觉总规划.html')}}
    (out/'视觉规划确认.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8');return proof



def default_aplus_modules(root,qa_source,pad_asset_id):
    """One parent-level set, factual modules from current manual and S2, not invented copy."""
    root=Path(root).resolve();manual=_manual(root);source=safe(root,qa_source)
    data=json.loads(source.read_bytes()) if source.suffix=='.json' else yaml.safe_load(source.read_bytes())
    skus=data.get('skus') or data.get('listings') or [];qas=(skus[0].get('qa') or []) if skus else []
    if not qas and skus:
        fields={r['field']:r.get('en','') for r in skus[0].get('fields') or []}
        qas=[{'question':fields.get(f'qa.{i}.question'),'answer':fields.get(f'qa.{i}.answer')} for i in range(1,6)]
    if len(qas)!=5 or any(not q.get('question') or not q.get('answer') for q in qas):raise ValueError('S2 需要有来源的五条问答 Q&A，不能自行补造')
    kinds=['hero','hotspots','carousel','video','steps','qa','colors']
    questions=['这是什么，适合谁？','哪些结构解决收纳问题？','有哪些真实使用场景？','产品怎样使用？','按什么顺序开合收纳？','买家最关心的五个问题？','有哪些颜色可选？']
    source_ref={'path':source.relative_to(root).as_posix(),'sha256':sha(source)}
    rows=[{'slot_id':f'A{i+1:02}','module_kind':kind,'scope':'parent','buyer_question':questions[i],
        'pad_asset_id':pad_asset_id,'on_image_copy':'','qa_source':source_ref} for i,kind in enumerate(kinds)]
    rows[1]['structure_ids']=[r['id'] for r in manual.get('structure') or [] if r.get('evidence') and not r.get('missing_reason')]
    rows[4]['action_ids']=[r['id'] for r in manual.get('actions') or [] if r.get('evidence')]
    rows[4]['state_ids']=[r['id'] for r in manual.get('states') or []]
    rows[5]['qa']=qas
    return rows


def _aplus_desktop_rows(root,plan):
    base=helper('visual_postprocess').media_folder(root,'aplus',plan)
    data=yaml.safe_load((base/'media_manifest.yaml').read_bytes()) or {}
    return base,[row for row in data.get('items') or [] if row.get('device')=='desktop']


def release_aplus_mobile(root):
    """Approve current native desktop layouts after their real audits; no third global user gate."""
    root=Path(root).resolve();plan=load_plan(root);post=helper('visual_postprocess')
    try:base,rows=_aplus_desktop_rows(root,plan)
    except OSError:raise ValueError('桌面版尚无验收素材，先完成桌面版')
    check=post.verify_media_manifest(root,(base/'media_manifest.yaml').relative_to(root),'aplus',only_devices=['desktop'])
    if check['status']!='pass':raise ValueError('桌面版尚未全部验收：'+'；'.join(check['errors']))
    proof={'generator':'visual_suite.py','status':'accepted_desktop_layout','rows_sha256':digest(rows),
        'plan_confirmation_sha256':sha(folder(root)/'视觉规划确认.json'),
        'manual_confirmation_sha256':sha(root/'00_产品资料/说明书确认.json'),'validator_sha256':sha(HOME/'scripts/visual_postprocess.py')}
    (base/'桌面版式确认.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return proof


def aplus_mobile_ready(root,plan=None):
    root=Path(root).resolve()
    try:
        plan=plan or load_plan(root);base,rows=_aplus_desktop_rows(root,plan)
        proof=json.loads((base/'桌面版式确认.json').read_bytes())
        if proof.get('generator')!='visual_suite.py' or proof.get('status')!='accepted_desktop_layout':return False
        if proof.get('rows_sha256')!=digest(rows) or proof.get('plan_confirmation_sha256')!=sha(folder(root)/'视觉规划确认.json'):return False
        if proof.get('manual_confirmation_sha256')!=sha(root/'00_产品资料/说明书确认.json') or proof.get('validator_sha256')!=sha(HOME/'scripts/visual_postprocess.py'):return False
        post=helper('visual_postprocess')
        expected={(slot['slot_id'],plan.get('aplus_variant') or plan['variants'][0],'desktop') for slot in plan['aplus_slots']}
        if len(rows)!=len(expected) or {(r['slot_id'],r['variant'],r['device']) for r in rows}!=expected:return False
        for row in rows:
            file=safe(root,row['output_path']);file.relative_to((base/'02_回填/最终版').resolve())
            audit=row.get('final_audit') or {};_,review_sha=post.read_review(root,row['review_ref'])
            if row['sha256']!=sha(file) or audit.get('final_sha256')!=row['sha256'] or audit.get('final_basename')!=file.name or audit.get('review_sha256')!=review_sha:return False
            if audit.get('validator_sha256')!=proof['validator_sha256'] or audit.get('manual_confirmation_sha256')!=proof['manual_confirmation_sha256'] or audit.get('plan_confirmation_sha256')!=proof['plan_confirmation_sha256']:return False
            if row['normalization']['source_sha256']!=sha(safe(root,row['source_return_path'])):return False
        return True
    except (OSError,ValueError,KeyError,TypeError,yaml.YAMLError):return False


def write_aplus_upload_list(root):
    """Readable draft is useful; only current accepted media makes it an upload-ready set."""
    import html
    root=Path(root).resolve();plan=load_plan(root);post=helper('visual_postprocess');base=post.media_folder(root,'aplus',plan)
    registry=yaml.safe_load((HOME/'templates/amazon_platform_rules.yaml').read_bytes())
    kind='standard_aplus' if plan['aplus_path']=='standard' else 'premium_aplus'
    limits=registry.get('unverified',{}).get(kind,{})
    series=Path(plan.get('series_root') or root).resolve();kw=helper('keyword_asset');snapshot=kw.read_snapshot(series)
    variant=plan.get('aplus_variant') or plan['variants'][0];sku=(plan.get('sku_ids') or {}).get(variant,variant)
    allowed=snapshot.select_many(lists=('master',),sku=sku,copy_allowed=True,limit=200)['master']
    allowed=[r['term_normalized'] for r in allowed if r['marketplace']==plan.get('marketplace','US')]
    scan=helper('compliance_scan');rows=[];scans=[]
    try:media=yaml.safe_load((base/'media_manifest.yaml').read_bytes()).get('items') or []
    except OSError:media=[]
    lookup={(r['slot_id'],r['device']):r for r in media};seen=set()
    for slot in plan['aplus_slots']:
        source=slot.get('qa_source')
        if source and sha(safe(root,source['path']))!=source['sha256']:raise ValueError('S2 问答来源变化，先更新 A+ 规划')
        title=str(slot.get('upload_title') or slot.get('on_image_copy') or '').strip()
        body=str(slot.get('upload_body') or '').strip()
        if not title or not body:raise ValueError('A+ 上传标题和正文尚未从已核对文案补齐：'+slot['slot_id'])
        copy=kw._normalize(slot.get('on_image_copy') or '')
        if copy and copy not in kw._normalize(title+' '+body):raise ValueError('上传文案与图上已确认文案不一致：'+slot['slot_id'])
        for device in (['desktop'] if plan['aplus_path']=='standard' else ['desktop','mobile']):
            alt=str((slot.get('alt_text') or {}).get(device) or '').strip()
            terms=[term for term in allowed if term and term in kw._normalize(alt)]
            if not alt or not alt.isascii() or not 1<=len(terms)<=2:raise ValueError('图片关键词需描述性英文并含 1–2 个有许可本品词：'+slot['slot_id'])
            fields={'title':title,'body':body,'alt':alt};checked=scan.scan_fields(fields,series=series,evidence={'sku':sku,'marketplace':plan.get('marketplace','US')})
            if checked['blocked']:raise ValueError('A+ 上传文案合规 block：'+slot['slot_id'])
            scans.append(checked);counts={}
            for field,value in fields.items():
                limit=limits.get('alt_text_max' if field=='alt' else 'text_max')
                if isinstance(limit,int) and len(value)>limit:raise ValueError('上传字段超登记上限：'+field)
                counts[field]={'current':len(value),'max':limit,'reason':limits.get('reason','适用模块上限待核')}
            row=lookup.get((slot['slot_id'],device));path=row.get('output_path') if row else ''
            rows.append({'module':slot['slot_id'],'device':device,'file':path,'canvas':slot.get('canvas') if plan['aplus_path']=='standard' else [1464,600] if device=='desktop' else [600,450],
                **fields,'counts':counts,'repeat_alt':alt in seen,'status':'待验收' if not row else '待最终复验'})
            seen.add(alt)
    checked=post.verify_media_manifest(root,(base/'media_manifest.yaml').relative_to(root),'aplus')
    ready=checked['status']=='pass'
    lines=['# A+上传清单','',('全部素材已验收；提交前在 A+ Content Manager 核实资格及适用模块上限。' if ready else '草稿：待验收素材不代表可上传；先完成桌面，再独立构图手机。'),'','|模块 / 设备|文件 / 尺寸|图片关键词|标题 / 正文|字符数（当前 / 上限）|','|---|---|---|---|---|']
    cells=[]
    for row in rows:
        label='桌面' if row['device']=='desktop' else '手机';status='已验收' if ready else row['status']
        file=('[查看图片]('+Path(row['file']).name+')') if row['file'] else '待验收'
        count='；'.join(key+' 当前 '+str(v['current'])+' / '+(str(v['max']) if v['max'] is not None else '待核') for key,v in row['counts'].items())
        clean=lambda s:str(s).replace('|',' / ').replace('\n',' ')
        lines.append('|'+ '|'.join(map(clean,[row['module']+' / '+label,file+' '+str(row['canvas'])+' '+status,row['alt'],row['title']+' / '+row['body'],count]))+'|')
        buttons=''.join('<p>'+html.escape(value)+' <button data-copy="'+html.escape(value,quote=True)+'">一键复制</button></p>' for value in [row['alt'],row['title'],row['body']])
        link='<a href="'+html.escape(Path(row['file']).name,quote=True)+'">查看图片</a>' if row['file'] else '待验收'
        cells.append('<tr><td>'+html.escape(row['module']+' '+label)+'</td><td>'+link+' '+html.escape(str(row['canvas'])+' '+status)+'</td><td>'+buttons+'</td><td>'+html.escape(count)+('</td></tr>'))
    out=base/'02_回填/最终版';out.mkdir(parents=True,exist_ok=True)
    md=out/'A+上传清单.md';page=out/'A+上传清单.html'
    md.write_text('\n'.join(lines)+'\n',encoding='utf8',newline='\n')
    page.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A+上传清单</title><style>body{font:16px system-ui;max-width:1100px;margin:auto;padding:20px}td{padding:12px;border-bottom:1px solid #ddd;vertical-align:top;overflow-wrap:anywhere}.tablewrap{overflow:auto}button{padding:8px;cursor:pointer}</style><h1>A+上传清单</h1><p>'+html.escape(lines[2])+'</p><div class="tablewrap"><table>'+''.join(cells)+'</table></div><script>document.querySelectorAll("button[data-copy]").forEach(b=>b.onclick=async()=>{try{await navigator.clipboard.writeText(b.dataset.copy);b.textContent="已复制"}catch(e){b.textContent="请手动复制上方英文"}});</script></html>',encoding='utf8',newline='\n')
    proof={'status':'ready' if ready else 'draft','rows':rows,'scans':scans,'keyword_source_sha256':snapshot.sha256,
        'registry_sha256':sha(HOME/'templates/amazon_platform_rules.yaml'),'plan_confirmation_sha256':sha(folder(root)/'视觉规划确认.json'),
        'files_sha256':{f.name:sha(f) for f in [md,page]},'media_check':checked}
    (out/'A+上传清单_校验.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return {'md':str(md),'html':str(page),'status':proof['status'],'errors':checked['errors']}


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('root',type=Path);ap.add_argument('action',choices=['inventory','plan','confirm','check','aplus-modules','release-mobile','upload-list']);ap.add_argument('--input',type=Path)
    args=ap.parse_args();data=yaml.safe_load(args.input.read_bytes()) if args.input else {}
    try:
        if args.action=='aplus-modules':result=default_aplus_modules(args.root,**data)
        elif args.action=='release-mobile':result=release_aplus_mobile(args.root)
        elif args.action=='upload-list':result=write_aplus_upload_list(args.root)
        else:result=inventory(args.root,**data) if args.action=='inventory' else write_plan(args.root,data) if args.action=='plan' else confirm_plan(args.root,data) if args.action=='confirm' else load_plan(args.root)
        if args.action=='inventory':
            result={'status':result['status'],'assets':len(result['assets']),'gaps':result['gaps'],
                    'choices':result['choices'],'report':str(folder(args.root)/'素材盘点.html')}
        elif args.action=='check':
            result={'status':'confirmed','slots':len(result['slots']),'aplus_modules':len(result['aplus_slots']),
                    'report':str(folder(args.root)/'视觉总规划.html')}
        print(json.dumps(result,ensure_ascii=False));return 0
    except (OSError,ValueError,KeyError) as error:ap.exit(2,str(error)+'\n')


if __name__=='__main__':
    sys.path.insert(0,str(HOME/'scripts'))
    raise SystemExit(main())
