"""Roadmap-owned navigation entries backed by immutable native step receipts; no business acceptance."""
from pathlib import Path
import argparse,json,hashlib,os,re
import yaml

def safe(root,relative):
 root=Path(root).resolve();path=(root/relative).resolve();path.relative_to(root);return path

def role_path(home,stage,role):
 home=Path(home);path=home/'templates/stage_roadmap.yaml'
 if not path.is_file():path=home.parents[1]/'release-overrides/stage_roadmap.yaml'
 return yaml.safe_load(path.read_bytes())['stages'][stage]['artifact_paths'][role]

def _records(state,stage,events=False):
 flow=state.get(stage.lower()+'_execution') or {};rows=list(flow.get('events') or []) if events else [];rows+=list(flow.get('records') or [])
 for key,task in (flow.get('optional_tasks') or {}).items():
  if task.get('events'):
   event=task['events'][-1];receipt=event.get('receipt') or {};rows.append({'step':key,**receipt,'artifacts':event.get('artifacts',task.get('artifacts',{}))})
 return {row.get('step'):row for row in rows}

def _receipt(root,record):
 if record.get('receipt_ref'):
  from workflow_checks import load_record_receipt
  return load_record_receipt(root,record)
 return record.get('receipt') if isinstance(record.get('receipt'),dict) else record


def nav_report(step,stage):
 relative=step.get('nav_receipt') or next((a for a in step.get('artifacts') or [] if '/步骤/' in a),None)
 if not relative:return None
 name=step.get('name') or step['control']['id']
 if isinstance(name,dict):name=next(iter(name.values()))
 name=re.sub(r'[<>:"/\\|?*+\s]+','',str(name))
 return str(Path(relative).with_name(f"{int(step['n']):02}_{name}.md")).replace('\\','/')

def write_receipts(root,roadmap,state,stage):
 if stage not in {'S1','S2','S3','S4'}:return []
 records=_records(state,stage,events=True);written=[]
 for step in roadmap['stages'][stage]['steps']:
  record=records.get(step['control']['id']);relative=nav_report(step,stage)
  if not record or not relative:continue
  from r01_case_store import step_report_catalog
  catalog=step_report_catalog(root,record)
  reference=record.get('receipt_ref') or {}
  if reference.get('path'):
   sealed=json.loads(safe(root,reference['path']).read_bytes())
   if sealed.get('business_snapshot') and catalog:
    continue  # The native step already owns its physical guide and catalogue.
  receipt=_receipt(root,record);path=safe(root,relative)
  lines=['# '+stage+' · '+str(step['n'])+' '+str(step.get('name') or step['control']['id']),'',str(receipt.get('summary') or record.get('summary') or '历史回执：结论请看下列原生报告。'),'','- 本步状态：'+{'complete':'已完成','deferred':'待补（仅限有本人回执的可后补项）','skipped':'已跳过','waiting_external':'等待回填','blocked':'需要补齐'}.get(record.get('outcome'),str(record.get('outcome') or '待核'))]
  skills=receipt.get('skills_used') or []
  if skills:lines.append('- 实际技能：'+'、'.join(str(x) for x in skills))
  for name,data in (receipt.get('mcp') or {}).items():
   if isinstance(data,dict):lines.append('- 数据源 '+name+'：'+{'called':'已调用','direct_called':'已直连调用','not_available':'未接通','available_not_used':'本步未使用','blocked':'调用受阻'}.get(data.get('status'),str(data.get('status') or '待核')))
  for item in receipt.get('kb_cards') or []:
   lines.append('- 知识依据：'+str(item.get('id') or item.get('card_id') or item.get('path') or item) if isinstance(item,dict) else '- 知识依据：'+str(item))
  lines+=['','## 本步原生文件','']
  entries=([(row['label'],row['path']) for row in catalog] if catalog else list((record.get('artifacts') or {}).items()))
  for role,item in entries:
   relative_file=item.get('path') if isinstance(item,dict) else item
   if not relative_file:continue
   native=safe(root,relative_file)
   if native==path:continue
   lines.append('- ['+str(role)+'](<'+os.path.relpath(native,path.parent).replace('\\','/')+'>)')
  lines+=['','本页仅整理本步实际回执，不替代文件/内容验收，不代表已上传后台。','']
  path.parent.mkdir(parents=True,exist_ok=True);path.write_text('\n'.join(lines),encoding='utf8');written.append(relative)
 return written

def audit(root,roadmap,state,stage):
 errors=[];rows=[];records=_records(state,stage);flow=state.get(stage.lower()+'_execution') or {}
 for step in roadmap['stages'][stage]['steps']:
  identity=step['control']['id'];record=records.get(identity);choice=(flow.get('optional_decisions') or {}).get(identity,{})
  if step.get('optional') and (choice.get('outcome') in {'skipped','not_requested','not_run'} or not record and not (flow.get('optional_tasks') or {}).get(identity)):
   rows.append({'n':step['n'],'status':'已跳过' if choice.get('outcome')=='skipped' else '本轮未请求'});continue
  if record and record.get('outcome')=='skipped' and step.get('skippable'):
   rows.append({'n':step['n'],'status':'已跳过'});continue
  declared=step.get('artifacts') or []
  if not declared:errors.append(stage+'.'+str(step['n'])+' 无产物声明')
  if not record:errors.append(stage+'.'+str(step['n'])+' 缺本步真实回执')
  old_nav=step.get('nav_receipt') or next((a for a in declared if '/步骤/' in a),None)
  from r01_case_store import step_report_catalog
  catalog=step_report_catalog(root,record or {})
  paths=[a for a in declared if a!=old_nav]
  if catalog:paths.extend(row['path'] for row in catalog)
  elif old_nav:paths.append(nav_report(step,stage))
  for item in (record or {}).get('artifacts',{}).values():
   value=item.get('path') if isinstance(item,dict) else item
   if value:paths.append(value)
  for relative in dict.fromkeys(paths):
   try:
    path=safe(root,relative)
    if not path.exists() or path.is_file() and not path.stat().st_size:errors.append(stage+'.'+str(step['n'])+' MISS '+str(relative))
   except (ValueError,TypeError):errors.append(stage+'.'+str(step['n'])+' 越界/无效路径 '+str(relative))
  rows.append({'n':step['n'],'paths':paths})
 return {'ok':not errors,'errors':errors,'rows':rows}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--home',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--root',type=Path,required=True);p.add_argument('--stage',choices=['S1','S2','S3','S4'],required=True);p.add_argument('--backfill',action='store_true');a=p.parse_args()
 from s1_roadmap import resolve_roadmap
 state=yaml.safe_load((a.root/'workflow_state.yaml').read_bytes()) or {};route=resolve_roadmap(a.home,a.root,state)
 if a.backfill:write_receipts(a.root,route,state,a.stage)
 result=audit(a.root,route,state,a.stage);print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
