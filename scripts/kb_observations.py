"""Plan and verify actual-result observations; planning never writes the knowledge cards."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import yaml
import kb_plan
from verify_kb_evidence import card_exists,response_failed,reports,read_object


def input_path(root,stage,step):
    return str(Path(kb_plan.evidence_path(stage,step,root)).with_name('observation_input.yaml')).replace('\\','/')


def receipt_path(root,stage,step):
    return str(Path(kb_plan.evidence_path(stage,step,root)).with_name('observation_receipt.yaml')).replace('\\','/')


def plan(root,home,stage,step):
    policy=kb_plan.plan_step(root,home,stage,step).get('observations')
    if not policy: return {'status':'none','calls':[],'notice':''}
    path=kb_plan.inside(root,input_path(root,stage,step))
    data=read_object(path) if path.is_file() else {}
    if data.get('actual_result') is not True:
        reason=data.get('reason') or '本轮没有真实结果可与卡片结论比较'
        return {'status':'not_applicable','calls':[],'notice':reason+'；尚不回写知识库缺口表。'}
    if not data.get('run_id') or not data.get('product_id'): raise ValueError('观察缺实际产品或运行标识')
    rows=data.get('observations') or []
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        raise ValueError('实际观察必须是对象列表')
    if Counter(r.get('server') for r in rows)!=Counter(policy['servers']):
        raise ValueError('实际观察需逐库各一次；无实际可比结果请标 actual_result:false 并说明')
    calls=[];sources=[]
    for row in rows:
        server=row['server'];cid=row.get('card_id')
        if not cid or not card_exists(home,server,cid): raise ValueError('观察引用卡不存在于对应库')
        if row.get('comparison') not in ('一致','相悖') or not str(row.get('observation') or '').strip():
            raise ValueError('观察必须由本步模型说明真实结果与卡片的一致或相悖，不能自动编造')
        raw=kb_plan.inside(root,row['source_path'])
        if hashlib.sha256(raw.read_bytes()).hexdigest()!=row.get('source_sha256'): raise ValueError('观察实际来源已变化')
        sources.append({'path':row['source_path'],'sha256':row['source_sha256']})
        args={'topic':row['topic'],'observation':row['comparison']+'：'+row['observation'],
              'run_id':data['run_id'],'product_id':data['product_id'],'card_id':cid,'metrics':row.get('metrics') or {}}
        kb_plan.validate_args(kb_plan.schemas(home,server)['kb_record_observation'],args)
        calls.append({'server':server,'library':kb_plan.LIBRARIES[server][1],'tool':'kb_record_observation','args':args})
    notice='将把本轮实测与卡片结论的一致 / 相悖写回知识库缺口表（不改卡）。'
    return {'status':'pending','calls':calls,'sources':sources,'notice':notice,
            'input_path':input_path(root,stage,step),'input_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'receipt_path':receipt_path(root,stage,step),
            'retry_rule':'写前先 kb_list_gaps：按 run_id + card_id + product_id 核对；已有相同观察只回读，不重复追加。'}


def payload(value):
    if isinstance(value,dict) and 'gaps' not in value and 'entry' not in value:
        if isinstance(value.get('result'),dict): return payload(value['result'])
        if isinstance(value.get('structuredContent'),dict): return payload(value['structuredContent'])
        for item in value.get('content',[]):
            if item.get('type')=='text':
                try: return payload(json.loads(item['text']))
                except (ValueError,KeyError): pass
    return value


def verify(root,home,stage,step,receipt=None):
    try:
        p=plan(root,home,stage,step)
        if p['status'] in ('none','not_applicable'): return {'status':'pass','observation_status':p['status'],'notice':p['notice'],'errors':[]}
        path=kb_plan.inside(root,p['receipt_path']);doc=read_object(path)
        if doc.get('notice_delivered') is not True: raise ValueError('观察回写前未记录用户可见提示')
        report=kb_plan.inside(root,doc['report_path'])
        delivered=reports(root,home,stage,step,receipt)
        if report not in delivered: raise ValueError('观察提示未绑定本步业务报告')
        if any(p['notice'] not in item.read_text(encoding='utf8') for item in delivered):
            raise ValueError('观察提示未出现在实际报告')
        rows=doc.get('calls') or []
        def key(c): return json.dumps({k:c[k] for k in ('server','library','tool','args')},sort_keys=True,ensure_ascii=False)
        if Counter(map(key,rows))!=Counter(map(key,p['calls'])): raise ValueError('观察回执与实际计划不符')
        unavailable=[];proofs={}
        for i,row in enumerate(rows):
            ts=datetime.fromisoformat(row['ts'])
            if ts.tzinfo is None: raise ValueError('观察时间戳缺时区')
            if row.get('status')=='error':
                if not row.get('error'): raise ValueError('观察失败未保留原因')
                unavailable.append(str(row['error']));continue
            if row.get('status')!='ok': raise ValueError('观察调用缺实际结果')
            responses=[]
            for field in ('response','readback'):
                raw=kb_plan.inside(root,row[field+'_path'])
                if hashlib.sha256(raw.read_bytes()).hexdigest()!=row[field+'_sha256']: raise ValueError('观察原始响应或回读已变化')
                response=json.loads(raw.read_text(encoding='utf8'))
                if response_failed(response): raise ValueError('错误响应不能声称回写成功')
                responses.append(payload(response));proofs[f'kb_observation_{i}_{field}']=row[field+'_path']
            expected=row['args']
            mode=row.get('execution_mode','write')
            if mode=='readback_only':
                if row.get('actual_tool')!='kb_list_gaps': raise ValueError('重试必须明确记录只回读工具')
                kb_plan.validate_args(kb_plan.schemas(home,row['server'])['kb_list_gaps'],row['actual_args'])
                if not any(all(g.get(k)==v for k,v in expected.items()) for g in responses[0].get('gaps',[])):
                    raise ValueError('重试未回读到已有的相同观察')
            elif mode=='write':
                entry=responses[0].get('entry')
                if not entry or any(entry.get(k)!=v for k,v in expected.items()): raise ValueError('观察写入内容与计划不符')
            else: raise ValueError('未知观察执行方式')
            if not any(all(g.get(k)==v for k,v in expected.items()) for g in responses[1].get('gaps',[])):
                raise ValueError('缺口表未回读到本条观察')
        if unavailable:
            for item in delivered:
                text=item.read_text(encoding='utf8')
                if '回写未完成' not in text or any(x not in text for x in unavailable): raise ValueError('回写降级缺用户可见原因')
        proofs['kb_observation_input']=p['input_path'];proofs['kb_observation_receipt']=p['receipt_path']
        proofs.update({f'kb_observation_source_{i}':s['path'] for i,s in enumerate(p['sources'])})
        proofs.update({f'kb_observation_report_{i}':str(item.relative_to(Path(root).resolve())).replace('\\','/') for i,item in enumerate(delivered)})
        return {'status':'pass','observation_status':'unavailable' if unavailable else 'recorded','notice':p['notice'],
                'failure_reasons':unavailable,'errors':[],'artifacts':proofs}
    except (OSError,ValueError,KeyError,TypeError,yaml.YAMLError) as error:
        return {'status':'fail','observation_status':'pending','errors':[str(error)]}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True)
    p.add_argument('--home',default=str(Path(__file__).resolve().parents[1]));p.add_argument('--stage',required=True)
    p.add_argument('--step',type=int,required=True);p.add_argument('--verify',action='store_true')
    p.add_argument('--receipt',help='本步实际业务回执路径；有实测回写时核对必交报告')
    a=p.parse_args()
    receipt=read_object(a.receipt) if a.receipt else None
    result=verify(a.root,a.home,a.stage,a.step,receipt) if a.verify else plan(a.root,a.home,a.stage,a.step)
    print(json.dumps(result,ensure_ascii=False,indent=2));return int(result['status']=='fail')

if __name__=='__main__': raise SystemExit(main())
