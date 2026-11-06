"""Build two flat ChatGPT-web feed folders (prompts + selected pad images) and their return folders."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

IMG_EXT = {'.jpg', '.jpeg', '.png', '.webp'}
HOW_TO = '00_把本文件夹全部上传.md'
RETURN_README = '00_把网页端生成的图放这里.md'
FEEDS = {'listing': ('GPT网页端投喂_主副图', 'GPT网页端回填_主副图', '主副图', '主副图回填好了'),
         'aplus': ('GPT网页端投喂_A+', 'GPT网页端回填_A+', 'A+', 'A+ 回填好了')}
HOME=Path(__file__).resolve().parents[3]
if str(HOME/'scripts') not in sys.path:sys.path.insert(0,str(HOME/'scripts'))


def _module(path,name):
    spec=importlib.util.spec_from_file_location('_feed_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def _modern_plan(root):
    path=root/'S3_视觉板块/amazon-listing-image-designer/visual_suite.yaml'
    return path.is_file() and load(path).get('schema_version')=='r03.s3'


def available_channels(*,repair_image_count=0,oracle_tools=(),home=HOME):
    """Gate scaffold only; no installation, login or autonomous web driver."""
    config=load(Path(home)/'templates/visual_channels.yaml')['visual']
    options=[{'id':'B','label':'网页端，同一个对话出整套'}]
    if 1<=repair_image_count<=config['direct_repair_max_images']:
        options.append({'id':'A','label':'对话内返修选定的少量图片'})
    if config.get('channel_c_enabled') and any('oracle' in str(name).casefold() for name in oracle_tools):
        options.append({'id':'C','label':'网页端自动驾驶（本机测试入口）'})
    return options


def summary(info):
    if info.get('schema_version')!='r03.s3':return info
    return {'status':'package_ready_not_generated','channel':info['channel'],
            'groups':{kind:{'feed':group['feed'],'images':len(group['items']),'return_dir':group['return_dir']}
                      for kind,group in info['groups'].items()},
            'evidence':'S3_视觉板块/_阶段总览/GPT网页端投喂清单.json'}


def _pack_r03(root,*,write=True):
    planner=_module(Path(__file__).with_name('visual_suite.py'),'planner')
    plan=planner.load_plan(root)
    manual=_module(HOME/'scripts/product_manual.py','manual').read_confirmed(root)
    kw=_module(HOME/'scripts/keyword_asset.py','keywords')
    scanner=_module(HOME/'scripts/compliance_scan.py','compliance')
    series=Path(plan.get('series_root') or root).resolve()
    snapshot=kw.read_snapshot(series)
    inventory,_=planner._inventory(root);assets={a['asset_id']:a for a in inventory['assets']}
    sources={source['id']:source for source in manual.get('sources') or []}
    structures={p['id']:p for p in manual.get('structure') or [] if not p.get('missing_reason')
        and p.get('status') not in {'hypothesis','pending','unknown'} and p.get('evidence')
        and all(sources.get(ref.get('source'),{}).get('kind') in {'user','manufacturer','1688'} for ref in p['evidence'])}
    facts='\n'.join(f"- {p['id']}: {p.get('name','')}; {p.get('position','')}; {p.get('movement','')}" for p in structures.values())
    blocked=list(plan.get('blocked_facts') or [])
    for conflict in manual.get('conflicts') or []:
        candidates=set(re.findall(r'\b\d+\b',' '.join([conflict.get('topic',''),*conflict.get('alternatives',[])])))
        resolution=(manual.get('confirmed_resolutions') or {}).get(conflict['id'],'')
        chosen=set() if re.search('待核|待补|未知|不声明',resolution) else set(re.findall(r'\b\d+\b',resolution))
        blocked.extend(sorted(candidates-chosen))
    groups={};used_keywords={};scans=[];keyword_notes=[]
    mobile_ready=planner.aplus_mobile_ready(root,plan) if plan.get('aplus_production_version')=='r03.m2' else True
    for kind,slots,variants in [('listing',plan['slots'],plan['variants']),('aplus',plan['aplus_slots'],[plan.get('aplus_variant') or plan['variants'][0]])]:
        prompts=[];pads={};items=[]
        for variant in variants:
            sku=(plan.get('sku_ids') or {}).get(variant,variant)
            allowed=snapshot.select_many(lists=('master',),sku=sku,copy_allowed=True,limit=60)['master']
            allowed={r['term_normalized']:r for r in allowed if r['marketplace']==plan.get('marketplace','US').upper()}
            for slot in slots:
                identity=slot['slot_id'];copy=slot.get('on_image_copy') or ''
                # R08.2: on-image keywords are a bonus, not a gate. Unlicensed or absent ones are dropped with a note;
                # the compliance scan below still blocks anything the copy itself must not say.
                keywords=[] if kind=='listing' and identity=='IMG01' else list(slot.get('keyword_embed') or [])
                if not keywords and identity!='IMG01':keyword_notes.append(identity+'：图上文案没有埋词库词（可选）')
                for term in keywords:
                    normalized=kw._normalize(term)
                    if normalized not in allowed:
                        # A word whose copy permission is withdrawn must not stay on the image.
                        if copy and normalized in kw._normalize(copy):raise ValueError('图上文案用了当前不可写的词库词：'+term+' / '+str(sku)+'（'+identity+'）')
                        keyword_notes.append(identity+'：'+term+' 不是本 SKU 当前可写的词库词，未计入埋词');continue
                    if copy and normalized not in kw._normalize(copy):keyword_notes.append(identity+'：'+term+' 没出现在图上文案里，未计入埋词');continue
                    used_keywords[variant+':'+normalized]={'term':term,'sku':sku,'fact_refs':allowed[normalized]['permissions']['copy']['fact_refs']}
                missing=set(slot.get('structure_ids') or [])-set(structures)
                if missing:raise ValueError('结构表没有这些部件，不能绘制：'+','.join(sorted(missing)))
                for claim in blocked:
                    pattern=(r'(?<!\d)'+re.escape(str(claim))+r'(?!\d)') if str(claim).isdigit() else r'(?<!\w)'+re.escape(str(claim))+r'(?!\w)'
                    if re.search(pattern,copy,re.I):raise ValueError('文案含待核或禁止事实：'+str(claim))
                visible={identity:copy}
                if slot.get('props'):visible[identity+'.disclaimer']='Tools not included'
                scanned=scanner.scan_fields(visible,series=series,evidence={'sku':sku,'marketplace':plan.get('marketplace','US')})
                scans.append({'slot':identity,'variant':variant,**scanned})
                if scanned['blocked']:raise ValueError('合规 block：'+identity+'；先修正文案，不交投喂包')
                asset=assets[slot['pad_asset_id']];source=planner.safe(root,asset['path'])
                pad='pad_'+asset['asset_id']+source.suffix.lower();pads[pad]=source
                devices=(['desktop'] if plan['aplus_path']=='standard' else ['desktop','mobile']) if kind=='aplus' else ['listing']
                if kind=='aplus' and plan.get('aplus_production_version')=='r03.m2' and plan['aplus_path']=='premium' and not mobile_ready:devices=['desktop']
                for device in devices:
                    size='2000x2000' if identity=='IMG01' else '1600x2000' if kind=='listing' else '1464x600' if device=='desktop' else '600x450'
                    if kind=='aplus' and plan['aplus_path']=='standard':size='x'.join(map(str,slot['canvas']))
                    description=[f'Create {identity} / {variant} / {device}. Target canvas: {size}.',
                        'Product reference: '+pad+'. Lock silhouette, seams, zipper, buckles, handle position and verified structure. Recompose background, lighting and layout; do not paste the supplier photo into a template.',
                        'Buyer question: '+slot['buyer_question'],
                        'Verified product structure (do not invent extra parts, holes or attachments):\n'+facts]
                    if identity=='IMG01':description+=['Pure white RGB 255 background. Product only, no text, props, logo or watermark. Product occupies about 85% of canvas.']
                    else:
                        description+=['Render this approved visible copy directly in the image, with no extra words: '+copy]
                        if slot.get('props'):description+=['Display props: '+', '.join(slot['props'])+'. Keep them visually distinct from supplied contents. Add readable small text in a consistent lower caption area: Tools not included.']
                    if kind=='aplus':description+=['This is one shared parent-level set covering all product colors. Recompose desktop and mobile independently. All small on-image copy must remain readable at 390px preview width, aiming for at least 16px.']
                    if kind=='aplus' and slot.get('module_kind'):
                        description+=['Parent A+ module: '+slot['module_kind']+'. Cover the complete verified color set: '+', '.join(plan['variants'])+'.']
                    if slot.get('qa'):
                        description+=['S2 Q&A factual reference only; render only the exact approved visible copy above, without inventing answers: '+json.dumps(slot['qa'],ensure_ascii=False)]
                    if slot.get('state_ids') or slot.get('action_ids'):
                        description+=['Verified states/actions for this module: '+json.dumps({key:[row for row in manual.get(key) or [] if row['id'] in slot.get('state_ids' if key=='states' else 'action_ids',[])] for key in ['states','actions']},ensure_ascii=False)]
                        for state in manual.get('states') or []:
                            if state['id'] in slot.get('state_ids',[]):
                                frame=planner.safe(root,state['image']);frame_name='state_'+planner.sha(frame)[:16]+frame.suffix.lower();pads[frame_name]=frame
                                description+=['Verified state '+state['id']+' reference: '+frame_name]
                    steps=[row for wanted in slot.get('action_ids') or [] for row in manual.get('actions') or [] if row.get('id')==wanted]
                    if steps:
                        # Step images were drawn past their real end state (dog84 IMG03: the last panel covered more than
                        # the product ever does). Spell out each panel's start and end, and stop at the last verified state.
                        panels=[f"Panel {i}: {row.get('name','')} - from {row.get('from','')} to {row.get('to','')}"
                                +(f"; how: {row['motion']}" if row.get('motion') else '')
                                +(f"; avoid: {row['common_mistakes']}" if row.get('common_mistakes') else '') for i,row in enumerate(steps,1)]
                        description+=['Step sequence with exactly '+str(len(steps))+' panel(s), in this order:\n'+'\n'.join(panels),
                            'The final panel shows exactly the end state of the last step ('+str(steps[-1].get('to',''))+') and nothing beyond it: '
                            'no extra folding, wrapping or covering. Show hands only while a step needs them; take hands away in the final panel. '
                            'Match each panel to the verified state frames above; when unsure, show less, not more.']
                    if slot.get('creative_direction'):description+=['Composition and lighting direction: '+slot['creative_direction']]
                    description+=['Return a finished image, preserving product color '+(' / '.join(plan['variants']) if kind=='aplus' else variant)+'. Local post-processing will verify actual dimensions; requested size alone is not acceptance.']
                    prompts.append('\n'.join(description));items.append({'slot':identity,'variant':variant,'device':device,'canvas':size,'pad':pad,'copy':copy,'props':slot.get('props') or [],'structure_ids':slot.get('structure_ids') or []})
        groups[kind]={'prompt':'\n\n'.join(prompts)+'\n','prompt_blocks':prompts,'pads':pads,'items':items}
    planner.load_plan(root)
    if kw.read_snapshot(series).sha256!=snapshot.sha256:
        raise ValueError('词库在打包期间变化，请重跑，不交混合版本的投喂包')
    if len({json.dumps(scan['layer_hashes'],sort_keys=True) for scan in scans})!=1:
        raise ValueError('合规依据在打包期间变化，请重跑')
    if not write:return {'groups':groups,'plan':plan,'keyword_sha256':snapshot.sha256,'keyword_notes':keyword_notes}
    # Validate everything before touching any deliverable or user's returns.
    output={'schema_version':'r03.s3','channel':'B','keyword_source_sha256':snapshot.sha256,
        'generation_choice':GENERATION_CHOICE,'keyword_selections':list(used_keywords.values()),'keyword_notes':list(dict.fromkeys(keyword_notes)),'manual_confirmation_sha256':plan['manual_confirmation_sha256'],
        'plan_confirmation_sha256':planner.sha(planner.folder(root)/'视觉规划确认.json'),
        'scans':scans,'files_sha256':{},'groups':{}}
    for kind,data in groups.items():
        name='出图_主副图' if kind=='listing' else '出图_高级A+' if plan['aplus_path']=='premium' else '出图_普通A+'
        base=root/'S3_视觉板块'/name;feed=base/'01_投喂包';feed.mkdir(parents=True,exist_ok=True)
        for directory in ('02_回填/放这里','02_回填/最终版','03_返修包'):(base/directory).mkdir(parents=True,exist_ok=True)
        for pad,source in data['pads'].items():shutil.copy2(source,feed/pad)
        (feed/'提示词.txt').write_text(data['prompt'],encoding='utf8')
        instructions=('# 网页端投喂说明\n\n'+GENERATION_CHOICE+'\n\n## 方式 1：ChatGPT 网页端\n\n'
                      '在同一个对话出完整一套；若需分批，沿用同一个对话和同一套产品资料。\n\n上传顺序：以下垫图 → 提示词.txt。说明文件不用粘贴进提示词。\n\n')
        instructions+='\n'.join(f'{i}. {name}' for i,name in enumerate(data['pads'],1))
        if kind=='aplus' and plan.get('aplus_production_version')=='r03.m2':instructions+='\n\n先回填桌面版并验收版式，再用 release-mobile 解锁手机版投喂；手机版独立构图，不直接裁桌面图。'
        instructions+='\n\n把生成图片或 ZIP 原样放回 `../02_回填/放这里/`，不用改名；回复“已回填”，再自动后处理和验收。\n'
        (feed/'投喂说明.md').write_text(instructions,encoding='utf8')
        paths=[feed/'提示词.txt',feed/'投喂说明.md',*[feed/p for p in data['pads']]]
        output['files_sha256'].update({p.relative_to(root).as_posix():planner.sha(p) for p in paths})
        output['groups'][kind]={'feed':feed.relative_to(root).as_posix(),'items':data['items'],'return_dir':(base/'02_回填/放这里').relative_to(root).as_posix()}
    out=root/'S3_视觉板块/_阶段总览/GPT网页端投喂清单.json';out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return output


GENERATION_CHOICE=('## 出图方式（二选一，用户选了再动手）\n\n'
    '1. **ChatGPT 网页端出图（推荐）**：把本文件夹的垫图和提示词上传到同一个对话，一次出整套，不满意的单张用网页端局部改图。效果更好，不耗 Codex 额度，也不会把图片塞进 Codex 会话。\n'
    '2. **在 Codex 里直接生图（省事）**：用同一份提示词和垫图生成；会消耗额度，效果可能不如网页端，会话也会变长。\n\n'
    '两种方式的图都放回 `../02_回填/放这里/`，后面的验收完全一样。用户没选之前，助手不得自动生图。')


def verify_pack(root):
    root=Path(root).resolve();errors=[]
    try:
        expected=_pack_r03(root,write=False)
        path=root/'S3_视觉板块/_阶段总览/GPT网页端投喂清单.json'
        proof=load(path if path.exists() else root/'S3_视觉板块/GPT网页端投喂清单.json')
        if proof.get('keyword_source_sha256')!=expected['keyword_sha256']:errors.append('词库指纹已变化，重打投喂包')
        if proof.get('manual_confirmation_sha256')!=expected['plan']['manual_confirmation_sha256']:errors.append('说明书确认已变化，重打投喂包')
        for relative,fingerprint in proof.get('files_sha256',{}).items():
            path=(root/relative).resolve();path.relative_to(root)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=fingerprint:errors.append('投喂文件已变化：'+relative)
        for kind,data in expected['groups'].items():
            group=proof['groups'][kind];feed=(root/group['feed']).resolve();feed.relative_to(root)
            if (feed/'提示词.txt').read_text(encoding='utf8')!=data['prompt']:errors.append('提示词不再对应已确认规划：'+kind)
            for name,source in data['pads'].items():
                target=feed/name
                if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=hashlib.sha256(source.read_bytes()).hexdigest():errors.append('垫图与来源不一致：'+name)
    except (OSError,ValueError,KeyError,TypeError) as error:errors.append(str(error))
    return {'status':'fail' if errors else 'pass','errors':errors}


def pack_video(root,package_path,*,write=True):
    root=Path(root).resolve();package_path=Path(package_path).resolve();package_path.relative_to(root)
    package=load(package_path);decision=package.get('format_decision') or {}
    if decision.get('source')!='user' or not str(decision.get('message') or '').strip():
        raise ValueError('视频形态须沿用用户明确选择的回执')
    if package.get('format') not in {'clips','single','defer'}:raise ValueError('视频形态必须是分段、单段或后补')
    planner=_module(Path(__file__).with_name('visual_suite.py'),'video_planner');planner.load_plan(root)
    manual=_module(HOME/'scripts/product_manual.py','video_manual')
    document=manual.read_confirmed(root)
    video=_module(HOME/'scripts/视频出片.py','evidence_video');evidence=video.r03_validate_package(root,package)
    if package['format']!='defer' and not video.r03_generation_allowed(root,package,evidence['tier']):raise ValueError('视频已达一次生成和一次返修上限：补拍 / 改做展示类 / 本次跳过')
    states={state['id']:state for state in document.get('states') or []}
    prompts=[];notes=[];references={}
    if package['format']!='defer' and not package.get('shots'):raise ValueError('缺视频镜头；证据不足可明确选择后补')
    for shot in package.get('shots') or []:
        prompt=str(shot.get('prompt') or '').strip()
        if not prompt or any(token in prompt for token in ('【用法】','【操作】','上传顺序','下载后存到','【提示词】')):
            raise ValueError('操作用法不能混在提示词里，请放入投喂说明字段')
        if not shot.get('state_ids'):raise ValueError('视频镜头必须引用真实状态，不能只凭文字脑补')
        manual.read_confirmed(root,required_states=shot['state_ids'],required_actions=shot.get('action_ids') or ())
        duration=shot.get('duration')
        if not isinstance(duration,(int,float)) or duration<=0:raise ValueError('镜头时长无效')
        state_names=[]
        for identity in shot['state_ids']:
            source=planner.safe(root,states[identity]['image'])
            name='state_'+planner.sha(source)[:16]+source.suffix.lower()
            references[name]=source;state_names.append(identity+' = '+name)
        scanner=_module(HOME/'scripts/compliance_scan.py','video_scan')
        scanned=scanner.scan_fields({shot['id']:prompt},series=root)
        if scanned['blocked']:raise ValueError('视频提示词命中合规 block，先修正再投喂')
        if evidence['tier']=='C':prompt+='\nThe product remains stationary. Only camera, lighting and background move; no product operation is demonstrated.'
        if shot.get('props'):prompt+='\nShow readable caption: Tools not included.'
        prompts.append('Reference states: '+'; '.join(state_names)+'\n'+prompt)
        notes.append(f"- {shot['id']}：{duration:g} 秒；状态 {' / '.join(shot['state_ids'])}")
    feed=root/'S3_视觉板块/视频_即梦/01_投喂包'
    pure='\n\n'.join(prompts)+'\n'
    instructions='# 视频投喂说明\n\n'+evidence['tier']+' 档：'+evidence['method']+'。补拍共用 00_产品资料/拍摄清单.md。最多一次生成 + 一次返修；仍不合格可补拍、改展示或本次后补。\n\n先上传对应真实状态帧，再粘贴提示词；时长在生成界面单独设置，不混进粘贴内容。\n\n'+'\n'.join('- 垫图：'+name for name in references)+'\n\n'+'\n'.join(notes)+'\n'
    outputs={'prompt':str(feed/'提示词.txt'),'instructions':str(feed/'投喂说明.md'),
             'package_sha256':planner.sha(package_path),'evidence_tier':evidence['tier'],'evidence_sources':evidence['sources'],'video_status':'deferred' if package['format']=='defer' else 'package_ready_not_generated'}
    if write:
        feed.mkdir(parents=True,exist_ok=True)
        for name,source in references.items():shutil.copy2(source,feed/name)
        (feed/'提示词.txt').write_text(pure,encoding='utf8');(feed/'投喂说明.md').write_text(instructions,encoding='utf8')
        for directory in ('放这里','最终版'):(feed.parent/'02_回填'/directory).mkdir(parents=True,exist_ok=True)
        (feed.parent/'03_返修包').mkdir(exist_ok=True)
    else:
        if (feed/'提示词.txt').read_text(encoding='utf8')!=pure or (feed/'投喂说明.md').read_text(encoding='utf8')!=instructions:
            raise ValueError('视频投喂文件与当前镜头包不一致，请重新打包')
        for name,source in references.items():
            if not (feed/name).is_file() or planner.sha(feed/name)!=planner.sha(source):raise ValueError('视频状态垫图已变化，需要重打投喂包')
    return outputs


def load(path: Path):
    return yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}


def selected_pads(manifest: dict):
    items = manifest.get('items') or manifest.get('assets') or manifest.get('slots') or []
    out = []
    for item in items:
        status = str(item.get('status') or 'selected').lower()
        if status in {'excluded', 'internal_reference_only', 'rejected'}:
            continue
        rel = item.get('path') or item.get('relative_path')
        if not rel:
            continue
        out.append(item)
    return out


def first_existing(root: Path, candidates):
    for rel in candidates:
        path = root / rel
        if path.is_file() and path.stat().st_size:
            return path
    return None


def copy_flat(src: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def write_how_to(folder: Path, title: str, pads: list[str], back: str):
    lines = [
        f'# {title}',
        '',
        '把**本文件夹里的全部文件**一次性拖进 ChatGPT Images 网页端，不要再去别的目录找垫图。',
        '',
        '上传顺序：先垫图，再出发图文档。',
        '',
        '| 序 | 文件 |',
        '|---:|---|',
    ]
    for i, name in enumerate(pads, 1):
        lines.append(f'| {i} | `{name}` |')
    lines += [
        '',
        '图和文案一次生成。主图白底无字。未核实规格不要写进图。',
        '',
        f'生成好以后：把下载的压缩包或图片放进旁边的 `{back}/`（不用改名、不用解压），再回对话说一声。',
        '',
    ]
    (folder / '00_把本文件夹全部上传.md').write_text('\n'.join(lines), encoding='utf-8')


def ensure_return_dir(s3: Path, kind: str) -> Path:
    """Where downloads from the web come back. Created once, never cleared by re-packing."""
    feed, back, label, say = FEEDS[kind]
    folder = s3 / back
    folder.mkdir(parents=True, exist_ok=True)
    (folder / RETURN_README).write_text('\n'.join([
        f'# {label}回填：从 ChatGPT 网页端下载回来的图放这里',
        '',
        '- 下载的压缩包（.zip）或单张图片直接拖进本文件夹，不用改名、不用解压；多次下载可以都放进来。',
        f'- 放好后回到对话说：「{say}」。助手从这里取原图，按亚马逊规格导出并验收，原图保留不动。',
        f'- 本文件夹不会被重新打包清掉；上传用的垫图和提示词在旁边的 `{feed}/`。',
        '- 悬浮窗里点「回填」这一行就能直接打开本文件夹。',
        '',
    ]), encoding='utf-8')
    return folder


def rescue_foreign(feed_dir: Path, keep: set, return_dir: Path) -> list:
    """Anything in the feed folder the packer did not put there (downloads, unzipped sets) moves to the return folder."""
    moved = []
    if not feed_dir.is_dir():
        return moved
    for item in sorted(feed_dir.iterdir()):
        if item.name in keep or item.name == '.DS_Store':
            continue
        target = return_dir / item.name
        n = 1
        while target.exists():
            target = return_dir / f'{item.stem}_{n}{item.suffix}'
            n += 1
        shutil.move(str(item), str(target))
        moved.append(target.name)
    return moved


def pack(root: Path) -> dict:
    root = root.resolve()
    if _modern_plan(root):
        return _pack_r03(root)
    s3 = root / 'S3_视觉板块'
    manifest_path = first_existing(root, [
        'S3_视觉板块/amazon-listing-image-designer/产品垫图/asset_manifest.yaml',
        'S3_视觉板块/amazon-listing-image-designer/asset_manifest.yaml',
    ])
    if not manifest_path:
        raise FileNotFoundError('找不到 asset_manifest.yaml，先挑选本品垫图')
    manifest = load(manifest_path)
    pads = selected_pads(manifest)

    listing_md = first_existing(root, [
        'S3_视觉板块/amazon-listing-image-designer/08_主副图出图文档.md',
        'S3_视觉板块/amazon-listing-image-designer/08_主副图设计方案.md',
    ])
    aplus_md = first_existing(root, [
        'S3_视觉板块/amazon-premium-aplus-designer/09_高级A+出图文档.md',
        'S3_视觉板块/amazon-premium-aplus-designer/09_高级A+方案.md',
        'S3_视觉板块/amazon-standard-aplus-designer/09S_标准A+出图文档.md',
        'S3_视觉板块/amazon-standard-aplus-designer/09S_标准A+方案.md',
    ])
    if not listing_md:
        raise FileNotFoundError('找不到主副图出图文档')
    if not aplus_md:
        raise FileNotFoundError('找不到 A+ 出图文档')

    listing_dir = s3 / FEEDS['listing'][0]
    aplus_dir = s3 / FEEDS['aplus'][0]
    listing_back = ensure_return_dir(s3, 'listing')
    aplus_back = ensure_return_dir(s3, 'aplus')

    listing_pads_dir = s3 / 'amazon-listing-image-designer/产品垫图/主副图'
    aplus_pads_dir = next((p for p in [
        s3 / 'amazon-listing-image-designer/产品垫图/高级A+',
        s3 / 'amazon-premium-aplus-designer/产品垫图',
        s3 / 'amazon-listing-image-designer/产品垫图/标准A+',
    ] if p.is_dir() and any(x.suffix.lower() in IMG_EXT for x in p.iterdir())), None)

    listing_slot_files = sorted(
        [p for p in listing_pads_dir.iterdir() if p.suffix.lower() in IMG_EXT and p.name.upper().startswith('IMG')],
        key=lambda p: p.name,
    ) if listing_pads_dir.is_dir() else []
    aplus_slot_files = sorted(
        [p for p in aplus_pads_dir.iterdir() if p.suffix.lower() in IMG_EXT],
        key=lambda p: p.name,
    ) if aplus_pads_dir else []

    # Anything the user dropped into a feed folder (e.g. an unzipped download) survives re-packing.
    listing_keep = {HOW_TO, listing_md.name, *(p.name for p in listing_slot_files)}
    aplus_keep = {HOW_TO, aplus_md.name, *(p.name for p in aplus_slot_files)}
    if not listing_slot_files:
        listing_keep |= {f'{i:02d}_{item.get("asset_id") or Path(item.get("path") or item.get("relative_path")).stem}'
                         f'{Path(item.get("path") or item.get("relative_path")).suffix.lower()}' for i, item in enumerate(pads, 1)}
    if not aplus_slot_files:
        aplus_keep |= listing_keep
    rescued = {'listing': rescue_foreign(listing_dir, listing_keep, listing_back),
               'aplus': rescue_foreign(aplus_dir, aplus_keep, aplus_back)}
    for folder in (listing_dir, aplus_dir):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
    copy_flat(listing_md, listing_dir / listing_md.name)
    copy_flat(aplus_md, aplus_dir / aplus_md.name)

    listing_names = []
    aplus_names = []
    if listing_slot_files:
        for src in listing_slot_files:
            copy_flat(src, listing_dir / src.name)
            listing_names.append(src.name)
    else:
        for i, item in enumerate(pads, 1):
            src = root / (item.get('path') or item.get('relative_path'))
            if not src.is_file():
                raise FileNotFoundError('垫图文件不存在：' + str(src))
            dest_name = f'{i:02d}_{item.get("asset_id") or src.stem}{src.suffix.lower()}'
            copy_flat(src, listing_dir / dest_name)
            listing_names.append(dest_name)
    if aplus_slot_files:
        for src in aplus_slot_files:
            copy_flat(src, aplus_dir / src.name)
            aplus_names.append(src.name)
    else:
        for name in listing_names:
            copy_flat(listing_dir / name, aplus_dir / name)
            aplus_names.append(name)

    if not listing_names:
        raise FileNotFoundError('主副图垫图未按 IMG01–IMG07 拷进 产品垫图/主副图')
    write_how_to(listing_dir, '主副图投喂包', listing_names + [listing_md.name], FEEDS['listing'][1])
    write_how_to(aplus_dir, 'A+ 投喂包', aplus_names + [aplus_md.name], FEEDS['aplus'][1])

    info = {
        'listing_dir': listing_dir.relative_to(root).as_posix(),
        'aplus_dir': aplus_dir.relative_to(root).as_posix(),
        'listing_return_dir': listing_back.relative_to(root).as_posix(),
        'aplus_return_dir': aplus_back.relative_to(root).as_posix(),
        'rescued_to_return': rescued,
        'listing_prompt': listing_md.name,
        'aplus_prompt': aplus_md.name,
        'pads': listing_names,
    }
    (s3 / 'GPT网页端投喂清单.json').write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--video-package',type=Path)
    args = parser.parse_args()
    info = pack_video(args.root,args.video_package) if args.video_package else pack(args.root)
    print(json.dumps(summary(info), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
