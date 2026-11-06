"""Cheap current-file checks for S3 navigation, never a replacement for delivery gates."""
import hashlib
import json
from pathlib import Path
import yaml


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe(root,path):
    target=(root/path).resolve();target.relative_to(root);return target


def review_hash(root,ref):
    if isinstance(ref,str):return sha(safe(root,ref))
    data=json.loads(safe(root,ref['path']).read_bytes())
    for part in ref['selector'][1:].split('/'):
        part=part.replace('~1','/').replace('~0','~');data=data[int(part)] if isinstance(data,list) else data[part]
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def summary(root,home):
    root=Path(root).resolve();home=Path(home);base=root/'S3_视觉板块'
    plan_path=base/'amazon-listing-image-designer/visual_suite.yaml'
    try:plan=yaml.safe_load(plan_path.read_bytes()) or {}
    except (OSError,ValueError,yaml.YAMLError):plan={}
    if plan.get('schema_version')!='r03.s3':return {'text':'','links':[],'counts':{}}
    if not plan.get('variants') or not plan.get('slots') or not plan.get('aplus_slots'):
        return {'text':'规划未完整，先补齐并确认','links':[],'counts':{}}
    upstream_current=True
    try:
        manual_folder=root/'00_产品资料';manual=yaml.safe_load((manual_folder/'产品使用说明书.yaml').read_bytes())
        for name,digest in (manual.get('source_sha256') or {}).items():
            if sha(safe(root,name))!=digest:raise ValueError('manual source changed')
        confirmation=json.loads((manual_folder/'说明书确认.json').read_bytes())
        for name,digest in confirmation['files_sha256'].items():
            if sha(safe(manual_folder,name))!=digest:raise ValueError('manual changed')
        inventory_path=plan_path.parent/'素材盘点.yaml'
        if plan.get('inventory_sha256')!=sha(inventory_path):raise ValueError('inventory refreshed')
        inventory=yaml.safe_load(inventory_path.read_bytes())
        for asset in inventory['assets']:
            for source in asset['sources']:
                if sha(safe(root,source['path']))!=source['sha256']:raise ValueError('asset source changed')
        confirmation=json.loads((plan_path.parent/'视觉规划确认.json').read_bytes())
        for name,digest in confirmation['files_sha256'].items():
            if sha(safe(plan_path.parent,name))!=digest:raise ValueError('plan changed')
    except (OSError,ValueError,KeyError,TypeError,yaml.YAMLError):upstream_current=False
    primary=base/'出图_主副图';aplus=base/('出图_普通A+' if plan.get('aplus_path')=='standard' else '出图_高级A+')
    counts={};validator=home/'scripts/visual_postprocess.py'
    def accepted(folder):
        if not upstream_current:return set()
        try:
            rows=(yaml.safe_load((folder/'media_manifest.yaml').read_bytes()) or {}).get('items') or []
            good=set()
            for row in rows:
                try:
                    proof=row.get('final_audit') or {};file=safe(root,row['output_path'])
                    file.relative_to((folder/'02_回填/最终版').resolve())
                    if (proof.get('status')!='pass' or row.get('sha256')!=sha(file)
                            or proof.get('final_sha256')!=row['sha256'] or proof.get('final_basename')!=file.name
                            or proof.get('validator_sha256')!=sha(validator)
                            or proof.get('manual_confirmation_sha256')!=sha(root/'00_产品资料/说明书确认.json')
                            or proof.get('plan_confirmation_sha256')!=sha(plan_path.parent/'视觉规划确认.json')
                            or proof.get('review_sha256')!=review_hash(root,row['review_ref'])
                            or (row.get('normalization') or {}).get('source_sha256')!=sha(safe(root,row['source_return_path']))):continue
                    good.add((row['slot_id'],row['variant'],row['device']))
                except (OSError,ValueError,KeyError,TypeError):continue
            return good
        except (OSError,ValueError,TypeError,yaml.YAMLError):return set()
    listing=accepted(primary);plus=accepted(aplus);labels=[]
    for variant in plan.get('variants') or []:
        expected={(slot['slot_id'],variant,'listing') for slot in plan.get('slots') or []}
        counts[variant]={'accepted':len(listing & expected),'expected':len(expected)}
        labels.append({'black':'黑色','khaki':'卡其色'}.get(variant,variant)+' '+str(counts[variant]['accepted'])+'/'+str(len(expected)))
    devices=['desktop'] if plan.get('aplus_path')=='standard' else ['desktop','mobile']
    expected={(slot['slot_id'],plan.get('aplus_variant') or plan['variants'][0],device)
              for slot in plan.get('aplus_slots') or [] for device in devices}
    counts['aplus']={'accepted':len(plus & expected),'expected':len(expected)}
    labels.append(('普通 A+' if plan.get('aplus_path')=='standard' else '高级 A+')+' '+str(len(plus & expected))+'/'+str(len(expected)))
    paths=[('📷 补充图片',root/'00_产品资料/补充图片'),('📘 产品使用说明书',root/'00_产品资料/产品使用说明书.html')]
    for name,folder in [('主副图',primary),('A+',aplus)]:
        paths.extend([(name+' · 投喂包',folder/'01_投喂包'),(name+' · 回填（放这里）',folder/'02_回填/放这里'),
                      (name+' · 返修包',folder/'03_返修包'),(name+' · 最终版',folder/'02_回填/最终版')])
    paths.extend([('📋 A+ 上传清单',aplus/'02_回填/最终版/A+上传清单.html'),('🎬 视频投喂',base/'视频_即梦/01_投喂包'),('🎬 视频回填',base/'视频_即梦/02_回填/放这里'),('🎬 视频验收对照',base/'视频_即梦/02_回填/抽帧对照'),('🎬 视频最终版',base/'视频_即梦/02_回填/最终版')])
    return {'text':('证据已变化或未确认，先重验 · ' if not upstream_current else '')+' · '.join(labels),'counts':counts,'links':[{'label':label,'path':str(path),
             'exists':path.exists(),'count':0} for label,path in paths]}
