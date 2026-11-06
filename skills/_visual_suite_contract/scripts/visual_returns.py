"""R03 return preparation and atomic per-slot publication for the visual suite."""
from __future__ import annotations
import argparse
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
import zlib

import yaml
from PIL import Image

HOME=Path(__file__).resolve().parents[3]
IMAGE={'.jpg','.jpeg','.png','.webp'}


def module(path,name):
    spec=importlib.util.spec_from_file_location('_returns_'+name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def planner():return module(Path(__file__).with_name('visual_suite.py'),'planner')


def postprocess():return module(HOME/'scripts/visual_postprocess.py','postprocess')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe(root,path):
    root=Path(root).resolve();target=(root/path).resolve();target.relative_to(root);return target


def atomic(path,content):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix='.visual-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8',newline='\n') as stream:stream.write(content)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def write_manifest(path,value):atomic(path,yaml.safe_dump(value,allow_unicode=True,sort_keys=False))


@contextmanager
def manifest_lock(path):
    """OS advisory lock releases on crash; no stale PID guessing or killing."""
    path=Path(path).with_suffix('.lock');path.parent.mkdir(parents=True,exist_ok=True)
    handle=path.open('a+b');acquired=False
    try:
        handle.seek(0,2)
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            acquired=True
        except OSError as error:raise ValueError('另一个回填提交正在处理本套图片，请稍后重试；未覆盖最终版') from error
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        handle.close()


def identify(path,plan,kind):
    name=str(path).casefold().replace('\\','/');stem=Path(path).stem
    if kind=='listing':
        match=re.search(r'(?i)(?:^|[^a-z0-9])img0?([1-7])(?:[^0-9]|$)',stem)
        if not match:match=re.match(r'^0?([1-7])(?:[_\- .]|$)',stem)
        if not match:return None
        slot=f'IMG{int(match.group(1)):02}'
        aliases={'black':['黑色'],'khaki':['卡其']}
        def colors(text):return [v for v in plan['variants'] if any(alias.casefold() in text.casefold() for alias in [v,*aliases.get(v.casefold(),[]),*(plan.get('variant_aliases') or {}).get(v,[])])]
        variants=colors(stem)
        if len(variants)!=1:
            for parent in reversed(Path(path).parts[:-1]):
                possible=colors(parent)
                if len(possible)==1:variants=possible;break
        if not variants and len(plan['variants'])==1:variants=plan['variants'][:]
        if len(variants)!=1:return None
        return {'slot_id':slot,'variant':variants[0],'device':'listing'}
    match=re.search(r'(?i)(?:^|[^a-z0-9])(?:m|a)0?(\d+)([dm])?(?:[^a-z0-9]|$)',name)
    if not match:return None
    index=int(match.group(1))-1
    if not 0<=index<len(plan['aplus_slots']):return None
    device='desktop' if match.group(2) and match.group(2).lower()=='d' else 'mobile' if match.group(2) else None
    if not device:device='desktop' if 'desktop' in name else 'mobile' if 'mobile' in name else None
    if not device and plan.get('aplus_path')=='standard':device='desktop'
    if not device:return None
    return {'slot_id':plan['aplus_slots'][index]['slot_id'],'variant':plan.get('aplus_variant') or plan['variants'][0],'device':device}


def filename(root,context,kind):
    p=planner();plan=p.load_plan(root)
    kw=module(HOME/'scripts/keyword_asset.py','filename_kw')
    series=Path(plan.get('series_root') or root)
    sku=(plan.get('sku_ids') or {}).get(context['variant'],context['variant'])
    allowed=kw.select(series,sku=sku,copy_allowed=True,limit=60)
    allowed={r['term_normalized']:r for r in allowed if r['marketplace']==plan.get('marketplace','US').upper()}
    slots=plan['slots'] if kind=='listing' else plan['aplus_slots']
    slot=next(s for s in slots if s['slot_id']==context['slot_id'])
    terms=[term for term in slot.get('keyword_embed') or [] if kw._normalize(term) in allowed]
    if not terms:terms=[r['term'] for r in allowed.values()][:1]
    if not terms:raise ValueError('缺可写的本品词，先修复词库许可，不能用品牌/禁止词凑文件名')
    core=re.sub('[^a-z0-9]+','-','-'.join(terms[:2]).casefold()).strip('-')
    names={'黑色':'black','卡其':'khaki','卡其色':'khaki','白色':'white','灰色':'gray','蓝色':'blue','红色':'red','绿色':'green','藏蓝':'navy'}
    original=context['variant'];color_name=(plan.get('variant_slugs') or {}).get(original) or names.get(original,original)
    color=re.sub('[^a-z0-9]+','-',color_name.casefold()).strip('-')
    if not re.search('[a-z]',color):color='variant-'+hashlib.sha256(original.encode()).hexdigest()[:8]
    identity=re.sub('[^a-z0-9]+','-',context['slot_id'].casefold()).strip('-')
    if not identity:identity='a'+str(next(i+1 for i,s in enumerate(slots) if s['slot_id']==context['slot_id']))
    device='' if kind=='listing' else '-'+context['device']
    result=f'{core}-{color}-{identity}{device}.png'
    scan=module(HOME/'scripts/compliance_scan.py','filename_scan').scan_fields({'filename':result},series=series,evidence={'sku':sku,'marketplace':plan.get('marketplace','US')})
    if scan['blocked']:raise ValueError('文件名命中品牌或合规 block，先换有依据的词')
    return result


def extract_images(root,archive,cache):
    root=Path(root).resolve();archive=safe(root,archive);cache=safe(root,cache)/sha(archive)[:16];files=[]
    with zipfile.ZipFile(archive) as bundle:
        members=bundle.infolist()
        if len(members)>500 or sum(member.file_size for member in members)>512*1024*1024:raise ValueError('回填 ZIP 过大，请按本次图位分包')
        for member in members:
            relative=member.filename.replace('\\','/')
            if (relative.startswith('/') or ':' in relative or '..' in Path(relative).parts
                    or stat.S_ISLNK(member.external_attr>>16)):raise ValueError('回填 ZIP 含越界路径或符号链接，未解压')
            if member.is_dir() or Path(relative).suffix.lower() not in IMAGE:continue
            path=safe(root,cache/relative);path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists():
                data=path.read_bytes()
                if len(data)!=member.file_size or zlib.crc32(data)&0xffffffff!=member.CRC:raise ValueError('解压副本已变化，请保留原 ZIP 并重新建立解压缓存')
            else:
                with bundle.open(member) as source,path.open('wb') as dest:shutil.copyfileobj(source,dest)
            files.append(path)
    return files


def prepare_returns(root,kind,*,assignments=None):
    root=Path(root).resolve();p=planner();plan=p.load_plan(root);post=postprocess();base=post.media_folder(root,kind,plan)
    raw=base/'02_回填/放这里';raw.mkdir(parents=True,exist_ok=True)
    legacy='GPT网页端回填_主副图' if kind=='listing' else 'GPT网页端回填_A+'
    sources=[raw,*[f for f in (root/'S3_视觉板块').iterdir() if f.is_dir() and f.name.startswith(legacy)]]
    files=[]
    for directory in sources:
        for path in sorted(directory.rglob('*')):
            if not path.is_file() or '_解压' in path.parts:continue
            path=safe(root,path)
            if path.suffix.lower()=='.zip':files+=extract_images(root,path,raw/'_解压')
            elif path.suffix.lower() in IMAGE:files.append(path)
    assigned=assignments or {};items=[];unknown=[];seen=set();contexts={}
    for source in files:
        fingerprint=sha(source)
        context=assigned.get(fingerprint) or identify(source,plan,kind)
        if not context:
            if (fingerprint,'unknown') not in seen:
                unknown.append({'file':source.relative_to(root).as_posix(),'sha256':fingerprint,'reason':'颜色或图位无法可靠识别，请明确对应项，不猜测'})
                seen.add((fingerprint,'unknown'))
            continue
        key=(context['slot_id'],context['variant'],context['device']);contexts.setdefault(key,[]).append(source)
        source_key=(fingerprint,*key)
        if source_key in seen:
            contexts[key].pop();continue
        seen.add(source_key)
        valid_slots={slot['slot_id'] for slot in plan['slots' if kind=='listing' else 'aplus_slots']}
        if context['variant'] not in plan['variants'] or context['slot_id'] not in valid_slots or context['device'] not in (['listing'] if kind=='listing' else ['desktop','mobile']):
            raise ValueError('候选映射不在已确认的颜色/图位/设备范围内，未猜测或交付')
        name=filename(root,context,kind)
        candidate=root/'S3_视觉板块/_阶段总览/回填候选'/uuid.uuid4().hex/name
        image_kind='main' if context['slot_id']=='IMG01' else 'secondary' if kind=='listing' else 'standard_aplus' if plan['aplus_path']=='standard' else 'aplus_'+context['device']
        canvas=next((s.get('canvas') for s in plan['aplus_slots'] if s['slot_id']==context['slot_id']),None) if image_kind=='standard_aplus' else None
        try:normalized=post.normalize_image(source,candidate,image_kind,dimensions=canvas)
        except (ValueError,OSError) as error:
            items.append({**context,'kind':kind,'source_return_path':source.relative_to(root).as_posix(),
                'status':'normalization_failed','errors':[str(error)],
                'repair':repair_pack(root,kind,context,[str(error)],fingerprint)})
            continue
        items.append({**context,'kind':kind,'candidate':candidate.relative_to(root).as_posix(),
                      'source_return_path':source.relative_to(root).as_posix(),'normalization':normalized,
                      'status':'awaiting_visual_review','final_basename':name})
    for key,values in contexts.items():
        if len(values)>1:
            unknown.append({'context':key,'files':[v.relative_to(root).as_posix() for v in values],'reason':'同一图位多份不同来源，请先选择实际要验收的一份'})
            for item in items:
                if (item['slot_id'],item['variant'],item['device'])==key:item['status']='needs_selection'
    result={'schema_version':'r03.s3','status':'needs_selection' if unknown else 'awaiting_visual_review' if items else 'waiting_external',
            'kind':kind,'items':items,'needs_selection':unknown,
            'filename_note':'Amazon 可能重新处理或改名；关键词命名用于管理和站外复用，不计已埋词，也不承诺排名效果。'}
    result['report']=render_review_page(root,result)
    target=root/'S3_视觉板块/_阶段总览/回填候选清单.json';atomic(target,json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


def render_review_page(root,result):
    """One readable, portable review report; machine evidence stays in its sidecar."""
    renderer=module(HOME/'skills/github-tabler-html-report/assets/salon_report.py','return_report')
    report=renderer.Report('回填图片 · 验收进度',stage='S3')
    section=report.section('overview','本次回填')
    section.kpis([('候选图片',str(len(result['items'])),None,'blue'),
                  ('待选择',str(len(result['needs_selection'])),None,'warn')])
    section.callout('info','原始回填保留。候选图片还需核对结构、文字、颜色及道具声明；通过后才进入最终版。')
    labels={'awaiting_visual_review':'待视觉复核','normalization_failed':'需返修','needs_selection':'待选择'}
    variants={'black':'黑色','khaki':'卡其色'}
    devices={'listing':'主副图','desktop':'桌面版','mobile':'手机版'}
    rows=[]
    for number,item in enumerate(result['items'],1):
        slot='主图' if item['slot_id']=='IMG01' else '副图 '+item['slot_id'][3:] if item['slot_id'].startswith('IMG') else 'A+ 模块 '+item['slot_id'][1:]
        title=variants.get(item['variant'],item['variant'])+' · '+slot+' · '+devices[item['device']]
        state=labels[item['status']]
        rows.append([renderer.esc(title),renderer.esc(state),renderer.esc(item['source_return_path']),
                     renderer.esc('；'.join(item.get('errors') or []) or '请查看下方图片，完成逐项复核')])
        candidate=item.get('candidate')
        if candidate:
            with Image.open(safe(root,candidate)) as image:
                from io import BytesIO
                image.thumbnail((800,1000));stream=BytesIO();image.convert('RGB').save(stream,format='PNG')
            report.section('candidate-'+str(number),title).image_card(
                'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode('ascii'),
                title,caption='预览缩略图；实际验收绑定完整候选图片及当前规划。',status=state)
    section.table(['图位','当前状态','原始文件','下一步'],rows)
    if result['needs_selection']:
        report.section('selection','需要明确对应项').table(['文件','原因'],[
            [renderer.esc(row.get('file') or '；'.join(row.get('files') or [])),renderer.esc(row['reason'])]
            for row in result['needs_selection']])
    section.callout('info',renderer.esc(result['filename_note']))
    target=Path(root)/'S3_视觉板块/_阶段总览'/('主副图_回填验收.html' if result['kind']=='listing' else 'A+_回填验收.html')
    atomic(target,report.render())
    return target.relative_to(root).as_posix()


def publish_candidate(root,candidate,manifest,row):
    manifest=safe(root,manifest)
    with manifest_lock(manifest):return _publish_candidate_locked(root,candidate,manifest,row)


def _publish_candidate_locked(root,candidate,manifest,row):
    """Only generated candidate/final files mutate; raw returns remain intact."""
    root=Path(root).resolve();candidate=safe(root,candidate);manifest=safe(root,manifest);target=safe(root,row['output_path'])
    final=(manifest.parent/'02_回填/最终版').resolve();target.relative_to(final)
    if candidate.name!=target.name:raise ValueError('文件名必须在最后写 XMP 前确定，不能发布时再改名')
    manifest.parent.mkdir(parents=True,exist_ok=True);final.mkdir(parents=True,exist_ok=True)
    old_path=None;backup=None;installed=False
    try:
        data=yaml.safe_load(manifest.read_bytes()) if manifest.exists() else {'schema_version':'r03.s3','items':[]}
        rows=deepcopy(data.get('items') or []);key=lambda r:(r['slot_id'],r['variant'],r['device'])
        old=next((item for item in rows if key(item)==key(row)),None)
        if old:
            old_path=safe(root,old['output_path']);old_path.relative_to(final)
            if not old_path.is_file() or sha(old_path)!=old['sha256']:raise ValueError('当前最终版已变化，先重新验收；未覆盖旧图')
            backup=safe(root,final/'_历史版本'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])/old_path.name)
            backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old_path,backup)
            if sha(backup)!=old['sha256']:raise ValueError('历史版本备份指纹不一致，未替换最终版')
        if target.exists() and target!=old_path:raise ValueError('最终版同名文件缺对应验收记录，未覆盖')
        if sha(candidate)!=row['sha256']:raise ValueError('候选在验收后发生变化，未替换最终版')
        os.replace(candidate,target);installed=True
        if sha(target)!=row['sha256']:raise ValueError('发布读回指纹不一致，将恢复旧最终版')
        if old_path and old_path!=target:old_path.unlink()
        rows=[item for item in rows if key(item)!=key(row)]+[row]
        write_manifest(manifest,{**data,'items':rows})
    except Exception:
        if installed:
            if old_path and backup:
                shutil.copy2(backup,old_path)
                if target!=old_path and target.exists() and sha(target)==row['sha256']:target.unlink()
            elif target.exists() and sha(target)==row['sha256']:target.unlink()
        raise
    return row


def accept_return(root,item,review_ref):
    """Recheck current evidence, tag last, then commit only this slot."""
    root=Path(root).resolve();p=planner();plan=p.load_plan(root);post=postprocess()
    module(HOME/'scripts/product_manual.py','accept_manual').read_confirmed(root)
    context={key:item[key] for key in ('slot_id','variant','device')};kind=item['kind']
    if item.get('status')=='normalization_failed':return item['repair']
    if item.get('status')=='needs_selection':return {'status':'needs_user','reason':'同图位多份候选，请明确选择后再验收'}
    candidate=safe(root,item['candidate'])
    review,_=post.read_review(root,review_ref)
    if not review:return {'status':'needs_visual_review','reason':'请实际查看当前候选并逐项核对结构，不能以文件存在替代审阅'}
    base=post.media_folder(root,kind,plan);manifest=base/'media_manifest.yaml'
    reference=None
    if not (kind=='listing' and context['slot_id']=='IMG01'):
        main_manifest=post.media_folder(root,'listing',plan)/'media_manifest.yaml'
        if not main_manifest.exists():return {'status':'waiting_main_baseline','reason':'先验收各颜色主图，才能核对同变体色彩'}
        mains=yaml.safe_load(main_manifest.read_bytes()).get('items') or []
        reference={row['variant']:{'variant':row['variant'],'path':row['output_path'],'roi':row['normalization']['product_bbox']}
                   for row in mains if row['slot_id']=='IMG01' and row.get('final_audit')}
        if context['variant'] not in reference:return {'status':'waiting_main_baseline','reason':'该颜色主图尚未验收，先补主图色彩基准'}
    checked=post.inspect_image(root,candidate,*context.values(),review_ref,reference=reference)
    if checked['status']!='ready_to_finalize':
        errors=checked.get('errors') or []
        if any('视觉复核' in error or '尚未识别' in error or '来源未明确' in error for error in errors):
            return {'status':'needs_visual_review','errors':errors}
        return repair_pack(root,kind,context,errors,sha(candidate))
    proof=post.finalize_image(root,candidate,*context.values(),review_ref,reference=reference)
    target=safe(root,base/'02_回填/最终版'/candidate.name)
    row={**context,'asset_id':context['slot_id']+'_'+context['variant']+'_'+context['device'],
        'module_id':context['slot_id'],'platform':context['device'],'status':'accepted','reviewed':True,
        'output_path':target.relative_to(root).as_posix(),'sha256':proof['final_sha256'],
        'source_return_path':item['source_return_path'],'normalization':item['normalization'],
        'review_ref':review_ref,'final_audit':proof}
    publish_candidate(root,candidate,manifest,row)
    if kind=='aplus' and context['device']=='desktop' and plan.get('aplus_production_version')=='r03.m2' and plan['aplus_path']=='premium':
        desktop=[r for r in yaml.safe_load(manifest.read_bytes()).get('items') or [] if r.get('device')=='desktop']
        if len(desktop)==len(plan['aplus_slots']):
            p.release_aplus_mobile(root)
            module(Path(__file__).with_name('pack_gpt_web_feeds.py'),'mobile_feed').pack(root)
    return {'status':'slot_accepted','context':context,'output':row['output_path'],
            'manifest':manifest.relative_to(root).as_posix(),'filename_note':'关键词命名用于管理，不计已埋词或排名承诺。'}


def repair_pack(root,kind,context,reasons,failure_sha):
    root=Path(root).resolve();p=planner();plan=p.load_plan(root);post=postprocess()
    base=(root/'S3_视觉板块'/('出图_品牌故事' if kind=='story' else '出图_旗舰店') if kind in {'story','store'} else post.media_folder(root,kind,plan))/'03_返修包'
    base.mkdir(parents=True,exist_ok=True);ledger_path=base/'返修记录.json'
    ledger=json.loads(ledger_path.read_bytes()) if ledger_path.exists() else {}
    plan_hash=sha(p.folder(root)/'视觉规划确认.json')
    if kind in {'story','store'}:
        brand=module(HOME/'skills/_brand_visual_contract/scripts/brand_preview.py','repair_brand_context');_,bound=brand.r03_read_slot(root,context['slot_id']);plan_hash+=':'+bound
    key='|'.join([context['slot_id'],context['variant'],context['device'],plan_hash])
    record=ledger.get(key) or {'attempt':0}
    if record.get('failure_sha')==failure_sha:return record
    if record['attempt']>=2:return {'status':'needs_user','attempt':2,'reason':'该图位已返修两次仍未通过，停止自动重出；原最终版保留',
        'choices':['补真实素材','修订已确认规划','暂停当前返修，保留其他已验收图']}
    packer=module(Path(__file__).with_name('pack_gpt_web_feeds.py'),'repair_packer');expected=packer._pack_r03(root,write=False) if kind not in {'story','store'} else None
    if kind in {'story','store'}:
        brand=module(HOME/'skills/_brand_visual_contract/scripts/brand_preview.py','repair_brand');group=brand.r03_feed_group(root,kind)
    else:group=expected['groups'][kind]
    index=next(i for i,item in enumerate(group['items']) if (item['slot'],item['variant'],item['device'])==(context['slot_id'],context['variant'],context['device']))
    folder=base/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+hashlib.sha256((context['slot_id']+'|'+context['variant']).encode()).hexdigest()[:16]+'_'+uuid.uuid4().hex[:6]);folder.mkdir()
    prompt=group['prompt_blocks'][index]+'\nCorrect only this slot. Keep the approved product and exact copy. Required corrections:\n'+'\n'.join('- '+str(reason) for reason in reasons)+'\n'
    (folder/'提示词.txt').write_text(prompt,encoding='utf8')
    pad=group['items'][index]['pad'];shutil.copy2(group['pads'][pad],folder/pad)
    (folder/'投喂说明.md').write_text('只返修本图位，其他最终版保留。沿用同一个网页对话；图片原样放回“放这里”后回复“已回填”。\n\n'+'\n'.join(str(r) for r in reasons),encoding='utf8')
    record={'status':'repair_ready','attempt':record['attempt']+1,'failure_sha':failure_sha,'folder':folder.relative_to(root).as_posix(),
            'context':context,'reasons':reasons,'channels':packer.available_channels(repair_image_count=1),
            'history':[*record.get('history',[]),{'failure_sha':failure_sha,'folder':folder.relative_to(root).as_posix(),'reasons':reasons}]}
    ledger[key]=record;atomic(ledger_path,json.dumps(ledger,ensure_ascii=False,indent=2)+'\n');return record


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('root',type=Path)
    ap.add_argument('action',choices=['prepare','accept','check']);ap.add_argument('--kind',choices=['listing','aplus'],required=True)
    ap.add_argument('--input',type=Path);args=ap.parse_args(argv)
    try:
        data=json.loads(args.input.read_bytes()) if args.input else {}
        if args.action=='prepare':
            result=prepare_returns(args.root,args.kind,assignments=data.get('assignments'))
            result={'status':result['status'],'candidates':len(result['items']),'needs_selection':result['needs_selection'],
                    'report':result['report'],
                    'evidence':'S3_视觉板块/_阶段总览/回填候选清单.json'}
        elif args.action=='accept':result=accept_return(args.root,data['item'],data['review_ref'])
        else:
            p=planner().load_plan(args.root);post=postprocess();path=post.media_folder(args.root,args.kind,p)/'media_manifest.yaml'
            result=post.verify_media_manifest(args.root,path,args.kind)
        print(json.dumps(result,ensure_ascii=False));return 0 if result.get('status') not in {'fail','needs_user'} else 2
    except (OSError,ValueError,KeyError,TypeError) as error:ap.exit(2,str(error)+'\n')


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(HOME/'scripts'))
    raise SystemExit(main())
