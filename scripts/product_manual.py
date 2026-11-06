"""Single evidence-bound product manual shared by S3 planning and media checks.

The host interprets images/video and supplies structured facts. This module
validates provenance, renders them, records user confirmation and detects drift;
it never infers unseen product structure or grants a business approval itself.
"""
from __future__ import annotations
import argparse
import base64
from copy import deepcopy
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import re

import yaml

FOLDER='00_产品资料'
PRIORITY={'user':1,'manufacturer':2,'1688':3,'competitor':4,'hypothesis':5}


def _file(root,path):
    root=Path(root).resolve();p=(root/str(path)).resolve();p.relative_to(root)
    if not p.is_file():raise ValueError('缺少真实证据文件：'+str(path))
    return p


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def complexity(data):
    override=data.get('complexity_override') or {}
    if override:
        if override.get('source')!='user' or not override.get('message') or override.get('level') not in {'L0','L1','L2','L3'}:
            raise ValueError('复杂度调整需要用户明确选择及原因')
        return override['level']
    flags=data.get('capabilities') or {}
    return 'L3' if flags.get('connection') else 'L2' if flags.get('assembly') else 'L1' if flags.get('moving_parts') else 'L0'


def validate(root,data):
    errors=[];identity=data.get('identity') or {};level=complexity(data)
    for field in ('name','audience','includes','excludes'):
        if field not in identity or identity[field] is None:errors.append('身份卡缺字段：'+field)
    if not data.get('scenes'):errors.append('缺场景表 scenes')
    if level!='L0':
        for field,label in (('structure','结构'),('states','状态'),('actions','动作')):
            if not data.get(field):errors.append('缺'+label+'表 '+field)
    if level in {'L2','L3'} and not data.get('assembly'):
        errors.append('缺安装/组装表 assembly，每步需要真实画面或明确待补')
    if level=='L3' and not data.get('connection'):
        errors.append('缺连接步骤、指示灯/界面状态 connection')
    sources={s['id']:s for s in data.get('sources') or []}
    if len(sources)!=len(data.get('sources') or []):errors.append('来源 ID 重复')
    hashes={}
    for source in sources.values():
        if source.get('kind') not in PRIORITY:errors.append('未知证据来源类型：'+str(source.get('kind')))
        if source.get('kind')!='hypothesis':
            if not source.get('path'):errors.append('来源缺少本地证据：'+source['id'])
            else:hashes[source['path']]=_sha(_file(root,source['path']))
    for group in ('structure','states','actions','assembly','connection','scenes'):
        for row in data.get(group) or []:
            if row.get('missing_reason'):
                if row.get('image'):errors.append('待补画面不能同时标为真实已具备：'+str(row.get('id')))
                continue
            refs=row.get('evidence') or []
            if not refs:errors.append(group+' 缺证据或待核说明：'+str(row.get('id') or row.get('name')))
            for ref in refs:
                source=sources.get(ref.get('source'))
                if not source:errors.append('引用未知来源：'+str(ref.get('source')));continue
                if source.get('kind')=='hypothesis' and row.get('status')!='hypothesis':
                    errors.append('一般经验必须标为待核，不能冒充本品事实')
                if group in {'structure','states','actions','assembly','connection'} and source.get('kind')=='competitor' and row.get('status')!='hypothesis':
                    errors.append('竞品只能提供使用参照，不能冒充本品结构或操作证据')
                if 'time' in ref and (not isinstance(ref['time'],(int,float)) or ref['time']<0):errors.append('证据秒数无效')
                if ref.get('file'):hashes[ref['file']]=_sha(_file(root,ref['file']))
                if 'time' in ref and Path(source.get('path') or '').suffix.lower() in {'.mp4','.mov','.avi','.webm','.mkv'}:
                    if not ref.get('frame_proof') or not ref.get('file'):
                        errors.append('视频秒数引用需要真实截帧和抽帧回执')
                    else:
                        proof_path=_file(root,ref['frame_proof']);proof=json.loads(proof_path.read_bytes())
                        hashes[ref['frame_proof']]=_sha(proof_path)
                        matches=[f for f in proof.get('frames') or [] if f.get('path')==Path(ref['file']).name and abs(f.get('actual',-10)-ref['time'])<=.5]
                        if (proof.get('source_sha256')!=hashes.get(source['path']) or not matches
                                or proof.get('outputs',{}).get(Path(ref['file']).name)!=hashes.get(ref['file'])):
                            errors.append('视频截帧、秒数或来源指纹不一致')
            if group in {'states','assembly','connection'}:
                if not row.get('image'):errors.append(group+' 缺真实画面或待补原因')
                else:hashes[row['image']]=_sha(_file(root,row['image']))
                if row.get('image'):
                    from PIL import Image
                    with Image.open(_file(root,row['image'])) as image:image.verify()
    if errors:raise ValueError('；'.join(errors))
    return hashes


def _evidence(row):
    return '；'.join(str(r['source'])+(f' @{r["time"]}s' if 'time' in r else '')+
                    (' / '+str(r['file']) if r.get('file') else '') for r in row.get('evidence') or []) or row.get('missing_reason','待核')


def _table(headers,rows):
    clean=lambda value:str(value or '—').replace('|','／').replace('\n',' ')
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'---|'*len(headers)]+
                     ['| '+' | '.join(clean(c) for c in row)+' |' for row in rows])


def _render(root,data,resolutions=None):
    path=Path(__file__).resolve().parents[1]/'skills/github-tabler-html-report/assets/salon_report.py'
    spec=importlib.util.spec_from_file_location('_product_manual_report',path)
    renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
    level=data['complexity'];identity=data['identity'];confirmed=resolutions is not None;resolutions=resolutions or {}
    report=renderer.Report(identity['name']+' · 产品使用说明书',stage='S3')
    heading=report.section('identity','身份卡')
    heading.kpis([('复杂度',level,None,'blue'),('确认','已记录用户确认' if confirmed else '待确认',None,'warn')])
    md=[f'# {identity["name"]} · 产品使用说明书',f'复杂度：{level}；来源冲突需明确确认，待补画面不用于操作生成。','']
    rows=[['使用人群',identity['audience']],['包含','、'.join(identity['includes']) or '无'],['不包含','、'.join(identity['excludes']) or '无']]
    heading.table(['项目','内容'],rows);md.append(_table(['项目','内容'],rows))
    groups=[('scenes','场景表',['谁','在哪','何时','用前→用后','搭配','证据'],
             lambda r:[r.get('who'),r.get('where'),r.get('when'),str(r.get('before',''))+' → '+str(r.get('after','')),'、'.join(r.get('with') or []),_evidence(r)])]
    if level!='L0':
        groups=[('structure','结构表',['部件','位置','如何活动','证据'],lambda r:[r.get('name'),r.get('position'),r.get('movement'),_evidence(r)]),
                ('states','状态表',['状态','说明','证据'],lambda r:[r.get('name'),r.get('description') or r.get('missing_reason'),_evidence(r)]),
                ('actions','动作表',['动作','从→到','方向与手法','易错点','证据'],lambda r:[r.get('name'),str(r.get('from',''))+' → '+str(r.get('to','')),r.get('motion'),r.get('common_mistakes'),_evidence(r)])]+groups
    for key in ('assembly','connection'):
        if data.get(key) and level in {'L2','L3'}:
            groups.append((key,'安装/组装' if key=='assembly' else '连接与指示状态',['步骤','操作','工具','耗时','证据'],
                lambda r:[r.get('name'),r.get('motion'),r.get('tools'),r.get('duration'),_evidence(r)]))
    for key,label,headers,fields in groups:
        section=report.section(key,label);rows=[fields(r) for r in data.get(key) or []]
        section.table(headers,rows);md.extend(['','## '+label,_table(headers,rows)])
        for row in data.get(key) or []:
            if row.get('image'):
                image=_file(root,row['image']);mime='image/png' if image.suffix.lower()=='.png' else 'image/jpeg'
                encoded=base64.b64encode(image.read_bytes()).decode('ascii')
                section.raw('<figure><img style="max-width:100%;height:auto" alt="'+html.escape(str(row.get('name') or ''),quote=True)+'" src="data:'+mime+';base64,'+encoded+'"><figcaption>'+html.escape(_evidence(row))+'</figcaption></figure>')
                md.append('真实画面：['+str(row.get('name') or '状态')+']('+image.as_posix()+') · '+_evidence(row))
    if data.get('conflicts'):
        section=report.section('conflicts','冲突与待核：请逐项选择')
        rows=[[r['id'],r['topic'],' / '.join(r.get('alternatives') or []),resolutions.get(r['id'],'等待用户明确选择')] for r in data['conflicts']]
        section.table(['ID','问题','来源差异','处理'],rows);md.extend(['','## 冲突与待核',_table(['ID','问题','来源差异','处理'],rows)])
    return '\n'.join(md)+'\n',report.render()


def write_manual(root,data):
    root=Path(root).resolve();data=deepcopy(data);hashes=validate(root,data)
    data.update(schema_version=1,complexity=complexity(data),source_sha256=hashes)
    folder=root/FOLDER;folder.mkdir(parents=True,exist_ok=True)
    paths={'md':folder/'产品使用说明书.md','html':folder/'产品使用说明书.html',
           'data':folder/'产品使用说明书.yaml','shooting':folder/'拍摄清单.md'}
    md,page=_render(root,data);paths['md'].write_text(md,encoding='utf8');paths['html'].write_text(page,encoding='utf8')
    paths['data'].write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf8')
    missing=[(r.get('name') or r.get('id'),r['missing_reason']) for key in ('states','actions','assembly','connection') for r in data.get(key) or [] if r.get('missing_reason')]
    shooting='# 共用拍摄清单\n\n1080p 以上，固定俯拍、浅色干净桌面。每个动作放慢，拍清起始状态、单个动作、结束状态；带好必要道具。\n\n'
    shooting+=_table(['需要补拍','缺口与影响'],missing or [['无新增缺口','仍按实际素材验收']])+'\n'
    if paths['shooting'].exists():
        prior=paths['shooting'].read_text(encoding='utf8')
        inventory=re.search(r'<!-- r03-inventory-start -->.*?<!-- r03-inventory-end -->',prior,re.S)
        if inventory:shooting+='\n'+inventory.group()+'\n'
    paths['shooting'].write_text(shooting,encoding='utf8')
    proof=folder/'说明书确认.json'
    if proof.exists():proof.unlink()  # Regeneration always invalidates the old approval.
    return {key:str(value) for key,value in paths.items()}


def _current(root):
    root=Path(root).resolve();folder=root/FOLDER
    data=yaml.safe_load((folder/'产品使用说明书.yaml').read_bytes())
    for path,expected in data['source_sha256'].items():
        if _sha(_file(root,path))!=expected:raise ValueError('说明书来源已变化，请重新生成并确认：'+path)
    validate(root,data)
    return root,folder,data


def confirm(root,decision,*,resolutions):
    root,folder,data=_current(root)
    unresolved=[r['id'] for r in data.get('conflicts') or [] if str(resolutions.get(r['id']) or '').strip().lower() in {'','继续','好','ok','下一步'}]
    if unresolved:raise ValueError('来源冲突需要逐项明确处理：'+'、'.join(unresolved))
    if decision.get('source')!='user' or str(decision.get('message') or '').strip().lower() in {'','继续','好','ok','下一步'}:
        raise ValueError('说明书需要用户明确确认')
    # The recorded user resolutions are visible; they never fabricate a source.
    md,page=_render(root,data,resolutions)
    (folder/'产品使用说明书.md').write_text(md,encoding='utf8');(folder/'产品使用说明书.html').write_text(page,encoding='utf8')
    # The shared shooting list is an evolving to-do list, not approved facts.
    files={p.name:_sha(p) for p in folder.iterdir() if p.name in {'产品使用说明书.md','产品使用说明书.html','产品使用说明书.yaml'}}
    proof={'schema_version':1,'decision':decision,'resolutions':resolutions,'data_digest':_digest(data),'files_sha256':files}
    (folder/'说明书确认.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return proof


def read_confirmed(root,*,required_states=(),required_actions=()):
    root,folder,data=_current(root);path=folder/'说明书确认.json'
    if not path.exists():raise ValueError('产品说明书尚未确认')
    proof=json.loads(path.read_bytes())
    if proof.get('data_digest')!=_digest(data):raise ValueError('说明书内容指纹已变化，需要重新确认')
    for name,expected in proof['files_sha256'].items():
        if _sha(_file(folder,name))!=expected:raise ValueError('说明书文件已变化，需要重新确认')
    for key,required in (('states',required_states),('actions',required_actions)):
        values={r['id']:r for r in data.get(key) or []}
        for identity in required:
            row=values.get(identity)
            if not row or row.get('missing_reason') or row.get('status')=='hypothesis':
                raise ValueError('所需真实状态/动作仍待补，不得用于操作画面：'+identity)
    return {**data, 'confirmed_resolutions': proof.get('resolutions') or {}}


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('root',type=Path)
    ap.add_argument('action',choices=['build','check','confirm']);ap.add_argument('--input',type=Path)
    args=ap.parse_args()
    try:
        data=yaml.safe_load(args.input.read_bytes()) if args.input else {}
        result=write_manual(args.root,data) if args.action=='build' else confirm(args.root,data.get('decision') or {},resolutions=data.get('resolutions') or {}) if args.action=='confirm' else read_confirmed(args.root)
        if args.action=='check':result={'status':'confirmed','complexity':result['complexity'],'report':str(args.root/FOLDER/'产品使用说明书.html')}
        print(json.dumps(result,ensure_ascii=False));return 0
    except (OSError,ValueError,KeyError) as error:ap.exit(2,str(error)+'\n')


if __name__=='__main__':raise SystemExit(main())
