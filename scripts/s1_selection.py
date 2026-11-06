"""Produce the C2 selection contract from the agent's real step-two input.

Weights are the documented condition_select D1-D5 policy, never market facts.
Unknown scores/reasons and original files remain unchanged; no total or ranked
recommendation is manufactured while any dimension is unknown.
"""
from copy import deepcopy
import argparse
import hashlib
import json
import math
from pathlib import Path

import yaml

from s1_research_gates import AXES, evaluate_scores, artifact_document

OUTPUT='S1_选品板块/jingpu-product-selection/00_product_selection.yaml'
WEIGHTS={'market_capacity':.25,'competition':.25,'trend':.20,'differentiation':.20,'margin_barrier':.10}


def selection_source(root,receipt=None):
    if receipt is None:
        state=root/'workflow_state.yaml'
        data=yaml.safe_load(state.read_bytes()) or {} if state.is_file() else {}
        execution=data.get('s1_execution') or {}
        if execution.get('roadmap_version')!='r01.m2':return None
        records=execution.get('records') or []
        record=next((r for r in reversed(records) if r.get('step')=='condition_select'),None)
        if record is None:return None
        receipt=record
    item=(receipt.get('artifacts') or {}).get('selection_yaml')
    if item is None:return None
    item={'path':item} if isinstance(item,str) else item
    path=(root/item['path']).resolve()
    if not path.is_relative_to(root):raise ValueError('selection source escapes product root')
    raw=path.read_bytes();doc=artifact_document(root,receipt,'selection_yaml')
    digest=hashlib.sha256(raw).hexdigest()
    if item.get('sha256'):
        actual=hashlib.sha256(json.dumps(doc,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() if item.get('selector') else digest
        if actual!=item['sha256']:raise ValueError('selection source hash mismatch')
    if not isinstance(doc,dict):raise ValueError('selection input must be a mapping')
    return path,doc,{'path':path.relative_to(root).as_posix(),'sha256':digest,
                     **({'selector':item['selector']} if item.get('selector') else {})}


def selection_document(doc,source):
    candidates=doc.get('candidates') or doc.get('directions') or []
    result=evaluate_scores(candidates)
    if result['errors']:raise ValueError('; '.join(result['errors']))
    weights=deepcopy(doc.get('scoring_weights',WEIGHTS))
    if (not isinstance(weights,dict) or set(weights)!=set(AXES) or
        any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in weights.values()) or
        abs(sum(weights.values())-1)>1e-6):raise ValueError('five-axis weights must total 1')
    rows=deepcopy(candidates);pending=False
    for row in rows:
        unknown=any(row['scores'][axis].get('score') is None for axis in AXES)
        total=None if unknown else round(sum(row['scores'][a]['score']*weights[a] for a in AXES),4)
        supplied=row.get('weighted_score')
        if supplied is not None and (type(supplied) not in (int,float) or not math.isfinite(supplied) or total is None or abs(supplied-total)>.01):
            raise ValueError('source weighted_score conflicts with actual scores; cannot replace it silently')
        pending=pending or unknown
        row.update(product_direction=row.get('product_direction') or row.get('name'),
                   candidate_id=row.get('candidate_id') or row.get('n') or row.get('product_direction') or row.get('name'),weighted_score=total,rank=None)
    if not pending:
        rows.sort(key=lambda r:-r['weighted_score'])
        for rank,row in enumerate(rows,1):row['rank']=rank
    out=deepcopy(doc)
    out.update(scoring_weights=weights,candidates=rows,projection_source=source,
               scoring_weights_origin='input' if 'scoring_weights' in doc else 'condition_select documented D1-D5 policy',
               scoring_status='pending_evidence' if pending else 'recomputed_from_supplied_scores')
    return out


def projection_digest(doc):
    return hashlib.sha256(json.dumps({k:v for k,v in doc.items() if k!='projection_seal'},
        ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def validate_canonical(doc,provenance):
    if not isinstance(doc.get('candidates'),list) or not doc['candidates']:raise ValueError('canonical selection requires candidates')
    if 'scoring_weights' not in doc:raise ValueError('canonical selection requires scoring_weights')
    expected=selection_document(doc,provenance)
    rows=doc.get('candidates') or doc.get('directions') or []
    actual={r.get('candidate_id',r.get('n',r.get('product_direction',r.get('name')))):r for r in rows}
    for row in expected['candidates']:
        key=row.get('candidate_id',row.get('n',row.get('product_direction',row.get('name'))))
        original=actual[key]
        for field in ('weighted_score','rank'):
            if original.get(field)!=row[field]:raise ValueError('canonical selection requires consistent '+field)
    if expected['scoring_status']!='pending_evidence' and [r.get('rank') for r in rows]!=list(range(1,len(rows)+1)):
        raise ValueError('canonical selection order must follow complete ranks')


def sealed_projection(root,target,doc,provenance):
    if (not isinstance(doc,dict) or doc.get('projection_seal')!=projection_digest(doc) or
        (doc.get('projection_source') or {}).get('path')!=provenance['path']):return False
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    folder=root/'00_总控/starsky-fullchain-workflow/运行数据/流程控制/S1/receipts'
    for path in folder.glob('*.json'):
        item=(json.loads(path.read_bytes()).get('business_files') or {}).get(OUTPUT,{})
        if item.get('sha256')==digest:return True
    return False


def ensure_selection(root,receipt=None,*,revise=False):
    root=Path(root).resolve();source=selection_source(root,receipt)
    if source is None:return None
    path,doc,provenance=source;target=root/OUTPUT
    if path==target:
        # A canonical input is authoritative; validation must not rewrite it.
        validate_canonical(doc,provenance)
        return target
    output=selection_document(doc,provenance)
    output['projection_seal']=projection_digest(output)
    raw=yaml.safe_dump(output,allow_unicode=True,sort_keys=False).encode('utf8')
    if target.is_file() and target.read_bytes()!=raw:
        if not revise or not sealed_projection(root,target,yaml.safe_load(target.read_bytes()),provenance):
            raise ValueError('existing canonical selection conflicts; requires a sealed Agent projection and native revision')
        previous=target.read_bytes()
        archive=root/'_history/S1/02/selection'/(hashlib.sha256(previous).hexdigest()+'.yaml')
        if archive.exists() and archive.read_bytes()!=previous:raise ValueError('selection history conflict')
        archive.parent.mkdir(parents=True,exist_ok=True);archive.write_bytes(previous)
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists() or target.read_bytes()!=raw:target.write_bytes(raw)
    return target


def validate_selection(root,receipt):
    root=Path(root).resolve();source=selection_source(root,receipt)
    if source is None:raise ValueError('missing selection_yaml')
    path,doc,provenance=source;target=root/OUTPUT
    if not target.is_file():
        raise ValueError('缺流程标准选品文件；由Agent运行 python scripts/s1_selection.py --root <产品根> --input <selection_yaml路径>，无需用户补评分')
    if path==target:
        validate_canonical(doc,provenance)
        return
    expected=selection_document(doc,provenance)
    expected['projection_seal']=projection_digest(expected)
    if yaml.safe_load(target.read_bytes())!=expected:
        raise ValueError('canonical selection does not match step-two source')


def run(root,relative=None):
    from r01_native import execute_native
    receipt={'artifacts':{'selection_yaml':relative}} if relative else None
    def produce(view):
        target=ensure_selection(view,receipt,revise=True)
        if target is None:raise ValueError('缺真实第2步selection_yaml引用；由Agent指定已落盘输入，不让用户补评分')
        return {'selection':str(target)}
    handled=execute_native(root,produce,step=2,receipt_fields=receipt)
    if handled is not NotImplemented:return handled
    return produce(root)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--input',help='Agent已生成的selection_yaml；省略时读取真实第2步回执引用，不扫描猜测文件')
    a=p.parse_args();print(json.dumps(run(a.root,a.input),ensure_ascii=False))


if __name__=='__main__':main()
