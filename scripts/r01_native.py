"""Compact S1 native execution; original algorithms run in a complete case view.

Only persistent product files are compacted. Temporary native IO is reported,
not claimed to disappear. Original media remains independent evidence.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml


def store_module():
    path=Path(__file__).with_name('r01_case_store.py')
    key='_starsky_case_store_'+hashlib.sha256(str(path).encode()).hexdigest()[:16]
    if key not in sys.modules:
        spec=importlib.util.spec_from_file_location(key,path)
        mod=importlib.util.module_from_spec(spec);sys.modules[key]=mod
        try:spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key,None);raise
    return sys.modules[key]


def enabled(root):
    root=Path(root).expanduser().resolve()
    physical=os.environ.get('STARSKY_R01_VIEW_ROOT')
    if physical and root==Path(physical).resolve():return False
    state=root/'workflow_state.yaml'
    data=yaml.safe_load(state.read_text(encoding='utf-8-sig')) if state.is_file() else {}
    if ((data or {}).get('s1_execution') or {}).get('compact_case') is True:return True
    folder=root/'00_总控/starsky-fullchain-workflow/运行数据/流程控制/S1/receipts'
    for path in folder.glob('*.json'):
        value=json.loads(path.read_text(encoding='utf-8-sig'))
        if value.get('business_files'):return True
    return False


def find_case(path):
    path=Path(path).expanduser().resolve()
    for root in (path,*path.parents):
        if enabled(root):return root
    return None


def _display_result(value, view_root, real_root, report_path):
    """Project UI paths only; sealed native bytes are never rewritten."""
    if isinstance(value,dict):
        result={k:_display_result(v,view_root,real_root,report_path) for k,v in value.items()}
        return result
    if isinstance(value,list):return [_display_result(v,view_root,real_root,report_path) for v in value]
    if isinstance(value,tuple):return tuple(_display_result(v,view_root,real_root,report_path) for v in value)
    if isinstance(value,Path):
        return Path(str(value).replace(str(view_root),str(real_root)))
    if isinstance(value,str):
        if value.lstrip().startswith(('{','[')):
            try:
                parsed=json.loads(value)
            except ValueError:
                pass
            else:
                return json.dumps(_display_result(parsed,view_root,real_root,report_path),ensure_ascii=False)
        return value.replace(str(Path(view_root).resolve()),str(real_root)).replace(str(view_root),str(real_root))
    return value


def execute_native(root, callback, *, step, report_paths=(), overrides=None, receipt_fields=None, callback_view=False):
    """Return NotImplemented for legacy/nested calls; otherwise commit two files."""
    root=Path(root).expanduser().resolve()
    if not enabled(root):return NotImplemented
    state_path=root/'workflow_state.yaml'
    state=yaml.safe_load(state_path.read_text(encoding='utf-8-sig')) if state_path.is_file() else {}
    cursor=((state or {}).get('s1_execution') or {}).get('cursor')
    if isinstance(cursor,int) and not isinstance(cursor,bool) and 1<=cursor<=8:
        # CT/cost may be recomputed together in step6; never create a second
        # step's report just because a native generator has a nominal owner.
        step=cursor
    store=store_module().CompactCase(root)
    with store.materialize(overrides=overrides or {}) as view:
        native_root=view.root.resolve()
        before_md={p.relative_to(native_root).as_posix():p.read_bytes() for p in (native_root/'S1_选品板块').rglob('*.md') if '运行数据' not in p.parts and '历史' not in p.parts}
        result=callback(view if callback_view else native_root)
        if step in (2,3,4):
            previous_path=list(sys.path)
            try:
                sys.path.insert(0,str(Path(__file__).parent))
                from s1_selection import ensure_selection
                supplied=receipt_fields if step==2 and (receipt_fields or {}).get('artifacts',{}).get('selection_yaml') else None
                ensure_selection(native_root,supplied,revise=True)
            finally:sys.path[:]=previous_path
        if step in (3, 4):
            previous_path = list(sys.path)
            try:
                sys.path.insert(0, str(Path(__file__).parent))
                from s1_market_facts import write_market_facts
                from s1_step_refresh import write_market_reports
                # R09: numbers are recomputed from saved replies; the AI narrative
                # (market_narrative.yaml) is read back, never overwritten.
                write_market_reports(native_root, write_market_facts(native_root), step)
            finally:
                sys.path[:] = previous_path
        if step == 5:
            helper = Path(__file__).parents[1]/'skills/alibaba-supplier-evaluation/scripts/screen_suppliers.py'
            previous_path = list(sys.path)
            try:
                sys.path.insert(0, str(Path(__file__).parent))
                spec = importlib.util.spec_from_file_location('_r05_supplier_report', helper)
                supplier = importlib.util.module_from_spec(spec);spec.loader.exec_module(supplier)
                supplier.write_evaluation(native_root)
            finally:
                sys.path[:] = previous_path
        # The native generator owns content; this final presentation pass only
        # applies to newly generated or revised narrative Markdown. Original
        # source bytes are preserved in the store's final source snapshots.
        previous_path = list(sys.path)
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from s1_readability import reader_projection, readability_issues, strip_source_appendix, SOURCE_APPENDIX
        finally:
            sys.path[:] = previous_path
        for path in (native_root/'S1_选品板块').rglob('*.md'):
            if '运行数据' in path.parts or '历史' in path.parts:continue
            raw=path.read_bytes();relative=path.relative_to(native_root).as_posix()
            if before_md.get(relative)==raw:continue
            text=raw.decode('utf-8-sig')
            if SOURCE_APPENDIX in text:text=strip_source_appendix(text)
            if not readability_issues(text):
                if text!=raw.decode('utf-8-sig'):path.write_text(text,encoding='utf8')
                continue
            rendered=reader_projection(text)
            if rendered!=text:
                # R06.1 PL-05: the pre-layout wording is kept beside the report (one file, overwritten each
                # time) instead of a full-text appendix inside it, which duplicated every body and doubled on
                # each revision. 运行数据 is never listed as a report.
                original=path.parent/'运行数据'/'排版前原文'/path.name
                original.parent.mkdir(parents=True,exist_ok=True)
                original.write_text(text,encoding='utf8')
            path.write_text(rendered,encoding='utf8')
        paths=[p for p in report_paths if (view.root/p).is_file()]
        candidates=[result] if isinstance(result,Path) else ([result.get('md'),result.get('html')] if isinstance(result,dict) else [])
        for candidate in candidates:
            if not candidate:continue
            candidate=Path(candidate)
            if not candidate.is_absolute():candidate=native_root/candidate
            if candidate.is_relative_to(native_root) and candidate.is_file() and candidate.suffix.lower() in {'.md','.html'}:
                relative=candidate.relative_to(native_root).as_posix()
                if relative not in paths:paths.append(relative)
        metadata={'native_result':json.loads(json.dumps(result,ensure_ascii=False,default=str))}
        prepared=view.collect(step=step,report_paths=paths,metadata=metadata,receipt_fields=receipt_fields)
        for archive in (native_root/'_history/S1/02/selection').glob('*.yaml'):
            raw=archive.read_bytes()
            if archive.stem!=hashlib.sha256(raw).hexdigest():raise ValueError('selection history hash mismatch')
            destination=root/archive.relative_to(native_root)
            if destination.is_file() and destination.read_bytes()!=raw:raise ValueError('selection history conflict')
            if not destination.exists():prepared.outputs[archive.relative_to(native_root).as_posix()]=raw
        committed=store.commit_step(prepared)
        report=Path(prepared.report_path)
        if not report.is_absolute():report=root/report
        projected=_display_result(result,view.root,root,report)
        if isinstance(projected,dict):
            projected.update(compact=True,temporary_io=prepared.temporary_io,
                             report=str(report),receipt=committed.get('receipt_path'))
        return projected


def _inside(root,value):
    path=(root/str(value)).resolve()
    if not path.is_relative_to(root) or path==root:raise ValueError('输入路径超出产品根')
    return path


def execute_request(root,step,request,*,home):
    """Stage inputs/raw calls and native commands from one stdin JSON request."""
    root=Path(root).resolve();home=Path(home).resolve()
    overrides={}
    for relative,item in request.get('inputs',{}).items():
        path=_inside(root,relative)
        if not isinstance(item,dict):raise ValueError('输入必须声明 text / json / yaml / bytes_base64')
        formats=[key for key in ('text','json','yaml','bytes_base64') if key in item]
        if len(formats)!=1:raise ValueError('每个输入只允许一种格式')
        key=formats[0]
        if key=='text':raw=str(item[key]).encode('utf8')
        elif key=='json':raw=json.dumps(item[key],ensure_ascii=False,indent=2).encode('utf8')
        elif key=='yaml':raw=yaml.safe_dump(item[key],allow_unicode=True,sort_keys=False).encode('utf8')
        else:raw=base64.b64decode(item[key],validate=True)
        overrides[path.relative_to(root).as_posix()]=raw
    fields=request.get('receipt') or {}
    evidence=fields.get('kb_evidence')
    if isinstance(evidence,dict):
        # Legacy native enrichment reads this logical YAML before stamping its
        # Markdown hash. It exists only in the view and is packed at commit.
        evidence=dict(evidence)
        libraries = {'selection_knowledge_base': '选品库', 'ads_knowledge_base': '广告库',
                     'promotion_knowledge_base': '产品推广库'}
        evidence['calls'] = [{**row, 'library': row.get('library') or libraries[row['server']]}
                             if isinstance(row, dict) and row.get('server') in libraries else row
                             for row in evidence.get('calls', [])]
        evidence.setdefault('verification_status','pending_stage')
        fields = {**fields, 'kb_evidence': evidence}
        relative=f'S1_选品板块/知识库依据/S1/{int(step):02d}/kb_evidence.yaml'
        if relative not in overrides:
            overrides[relative]=yaml.safe_dump(evidence,allow_unicode=True,sort_keys=False).encode('utf8')
    commands=request.get('commands',[])
    approved=[]
    for row in commands:
        script=(home/row['script']).resolve()
        if not script.is_relative_to(home) or not script.is_file() or script.suffix!='.py':
            raise ValueError('原生命令必须是当前插件内真实Python脚本')
        if 'args' in row and not all(isinstance(x,str) for x in row['args']):raise ValueError('命令参数必须为文本列表')
        approved.append((script,row.get('args',[])))
    def run(view):
        view_root=view.root.resolve()
        results=[]
        for script,args in approved:
            actual=[str(view_root) if token=='{root}' else token.replace(str(root),str(view_root)) for token in args]
            done=view.run_native(script,actual,cwd=view_root,
                capture_output=True,encoding='utf8',errors='replace',timeout=180,
                env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'})
            results.append({'script':script.relative_to(home).as_posix(),'exit_code':done.returncode,
                            'stdout':done.stdout,'stderr':done.stderr})
            if done.returncode:break
        return {'ok':all(not r['exit_code'] for r in results),'commands':results}
    # 3.0.25: {"label","path"} objects crashed with "PosixPath / dict"; accept them, reject anything else plainly.
    reports=[]
    for item in request.get('reports',[]) or []:
        path=item.get('path') if isinstance(item,dict) else item
        if not isinstance(path,str) or not path:
            raise ValueError('reports 每项写报告相对产品根的路径字符串（或 {"path": "..."}），收到：'+json.dumps(item,ensure_ascii=False)[:120])
        reports.append(path)
    return execute_native(root,run,step=step,report_paths=reports,overrides=overrides,
                          receipt_fields=fields,callback_view=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['execute','read','export'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--step',type=int)
    parser.add_argument('--file')
    parser.add_argument('--destination',type=Path)
    args=parser.parse_args(argv)
    try:
        if args.action=='execute':
            if args.step is None or not 1<=args.step<=9:raise ValueError('需要真实步骤1–9')
            request=json.load(sys.stdin)
            result=execute_request(args.root,args.step,request,home=Path(__file__).resolve().parents[1])
            if result is NotImplemented:raise ValueError('产品未启用紧凑记录；先按正式流程init，不自动迁移旧案')
        else:
            if not args.file:raise ValueError('需要 --file 逻辑路径')
            store=store_module().CompactCase(args.root)
            if args.action=='read':
                sys.stdout.write(store.read_bytes(args.file).decode('utf-8-sig'));return 0
            if args.destination is None:raise ValueError('导出需要 --destination')
            path=store.export_file(args.file,args.destination)
            result={'ok':True,'exported':str(path),'note':'显式按需导出不计入默认步骤交付'}
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        return 0 if result.get('ok',True) else 1
    except (ValueError,OSError,KeyError,TypeError) as error:
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False));return 1


if __name__=='__main__':raise SystemExit(main())
