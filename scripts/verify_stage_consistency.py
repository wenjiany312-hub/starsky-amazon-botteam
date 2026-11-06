"""Verify current S2 route against actual renderers and generated user examples."""
import argparse
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import re
import sys

import yaml


def _load(home, name):
    path=Path(home)/'scripts'/(name+'.py')
    spec=importlib.util.spec_from_file_location('_consistency_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def observe(home, root, docs_folder=None, *, stage="S2"):
    home,root=Path(home),Path(root)
    old=sys.path[:]
    sys.path.insert(0,str(home/'scripts'))
    try:
        route=yaml.safe_load((home/'templates/stage_roadmap.yaml').read_bytes())['stages'][stage]
        policy=yaml.safe_load((home/'templates/model_routing.yaml').read_bytes())
        nav=_load(home,'nav_float');web=_load(home,'render_nav_dashboard')
        ui=_load(home,'chat_card_v3');display=_load(home,'model_display').ModelDisplay(policy)
        cards=[ui.render_card({'outcome':'blocked','summary':'一致性演示；不是业务通过','needs_user':'按修改清单改 2 处'},
            stage=stage,number=s['n'],total=len(route['steps']),title=s['name'],model=display.recommendation(s),
            next_model='',next_text='继续当前步骤',root=root,
            acceptance={'accepted':False,'status':'needs_changes','count':2,'issues':['演示待核项']}) for s in route['steps']]
        folder=Path(docs_folder or home/('docs/r02-examples' if stage=='S2' else 'docs/r03-examples'))
        detail=nav.stage_detail(root,home,stage)
        return {'nav':nav.snapshot(root,home),'detail':detail,'tk_rows':nav.format_rows(detail),
                'html':web.build_html(home,root,stage),'cards':cards,
                'docs':(folder/'steps.md').read_text(encoding='utf8'),
                'docs_manifest':json.loads((folder/'manifest.json').read_text(encoding='utf8'))}
    finally:
        sys.path[:]=old


def verify(home, root, *, docs_folder=None, observations=None, stage="S2"):
    home=Path(home);errors=[]
    try:
        route=yaml.safe_load((home/'templates/stage_roadmap.yaml').read_bytes())['stages'][stage]
        policy=yaml.safe_load((home/'templates/model_routing.yaml').read_bytes())
        steps=route['steps'];total=route['total_steps']
        if total != len(steps) or [s['n'] for s in steps] != list(range(1,total+1)):
            errors.append('roadmap: noncontinuous count')
        data=observations if observations is not None else observe(home,root,docs_folder,stage=stage)
        display=_load(home,'model_display').ModelDisplay(policy)
        roles=policy.get('by_step',{}).get(stage,{})
        if len(roles)!=total:errors.append('models: count differs')
        navstage=next((row for row in data['nav']['stages'] if row['stage']==stage),{})
        if navstage.get('total')!=total or data['nav'].get('stage')==stage and data['nav'].get('total')!=total:errors.append('nav: total differs')
        if data['detail']['total']!=total or len(data['detail']['steps'])!=total:errors.append('detail: total differs')
        articles=re.findall(rf"<article[^>]+id=['\"]{stage}-(\d+)['\"][^>]*>(.*?)</article>",data['html'],re.S)
        if len(articles)!=total:errors.append('html: step count differs')
        if len(data['cards'])!=total:errors.append('cards: step count differs')
        docs=data['docs'];docrows=[line for line in docs.splitlines() if re.match(r'^\| \d+\.',line)]
        if len(docrows)!=total:errors.append('docs: step count differs')
        for index,step in enumerate(steps):
            n=step['n'];name=step['name'];role=roles.get(str(n),{})
            model=display.task(display.recommendation(step))
            nav_model=nav_label=__import__('model_display').navigation_label(model)
            if data['nav'].get('stage')==stage and data['nav'].get('cursor')==n:
                if data['nav'].get('step')!=name or data['nav'].get('model')!=nav_model:
                    errors.append(f'nav: step {n} name/model differs')
            if role.get('task_id')!=step['control']['id'] or role.get('name')!=name:errors.append(f'models: step {n} identity differs')
            row=next((r for r in data['detail']['steps'] if r['n']==n),{})
            if row.get('name')!=name or row.get('model')!=nav_model:errors.append(f'detail: step {n} name/model differs')
            tk_text='\n'.join(str(value) for item in data['tk_rows'] for value in item)
            if name not in tk_text or nav_model not in tk_text:errors.append(f'tk_rows: step {n} name/model differs')
            article=next((text for number,text in articles if int(number)==n),'')
            if html.escape(name,quote=True) not in article or html.escape(nav_model,quote=True) not in article:errors.append(f'html: step {n} name/model differs')
            card=data['cards'][index] if index<len(data['cards']) else ''
            if f'{stage} · {n}/{total} {name}' not in card or display.recommendation(step) not in card:errors.append(f'cards: step {n} name/model differs')
            docline=next((line for line in docrows if line.startswith(f'| {n}. ')),'')
            if name not in docline or display.recommendation(step) not in docline:errors.append(f'docs: step {n} name/model differs')
            for artifact in step.get('artifacts') or []:
                if artifact.endswith(('.md','.html')) and artifact not in docline:
                    errors.append(f'docs: step {n} artifact differs')
            for output in row.get('outputs') or []:
                if output.get('path') and html.escape(Path(output['path']).as_uri(),quote=True) not in article:
                    errors.append(f'html: step {n} artifact link differs')
        manifest=data['docs_manifest']
        if manifest.get('route_steps')!=total:errors.append('docs: manifest count differs')
        if manifest.get('route_artifacts')!={str(s['n']):s.get('artifacts',[]) for s in steps}:errors.append('docs: artifact contract differs')
        if manifest.get('outputs_sha256',{}).get('steps.md')!=hashlib.sha256(docs.encode()).hexdigest():errors.append('docs: table fingerprint differs')
        for relative,expected in manifest.get('source_sha256',{}).items():
            prefix=next((p for p in ('payload/','source/starsky-amazon-agent/') if relative.startswith(p)),None)
            if prefix:
                source=home/relative[len(prefix):]
                if source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest()!=expected:
                    errors.append('docs: stale source '+relative)
        report_contract = _load(home, 'report_contract_probe').verify(home)
        errors.extend(report_contract['errors'])
        if (home/'scripts/user_docs_probe.py').is_file():
            errors.extend(_load(home, 'user_docs_probe').verify(home)['errors'])
        return {'ok':not errors,'errors':errors,'surfaces':data,'report_contract':report_contract}
    except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
        return {'ok':False,'errors':errors+['consistency: '+str(exc)]}


def verify_golden_cases(home,cases):
    import stage_artifacts
    from s1_roadmap import resolve_roadmap
    errors=[];results={}
    if set(cases)!={'S1','S2','S3','S4'}:errors.append('N2需要S1–S4四个黄金案例，不能只验证已通过的一部分')
    for stage,root in cases.items():
        root=Path(root)
        try:
            state=yaml.safe_load((root/'workflow_state.yaml').read_bytes()) or {};roadmap=resolve_roadmap(home,root,state)
            result=stage_artifacts.audit(root,roadmap,state,stage);results[stage]=result;errors.extend(result['errors'])
        except (OSError,ValueError,KeyError,TypeError) as error:errors.append(stage+': '+str(error))
    return {'ok':not errors,'errors':errors,'cases':results}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--home',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--docs',type=Path);ap.add_argument('--stage',choices=['S1','S2','S3','S4'],default='S2');ap.add_argument('--goldens',type=Path)
    args=ap.parse_args(argv);result=verify(args.home,args.root,docs_folder=args.docs,stage=args.stage)
    if args.goldens:
        result['goldens']=verify_golden_cases(args.home,json.loads(args.goldens.read_text(encoding='utf8')));result['errors']+=result['goldens']['errors'];result['ok']=not result['errors']
    print(json.dumps({k:v for k,v in result.items() if k!='surfaces'},ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
