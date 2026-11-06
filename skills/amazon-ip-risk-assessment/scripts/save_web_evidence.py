#!/usr/bin/env python3
"""登记一次 IP/合规网页检索的证据（R06.1 AS-01 第 4 批）。

3.0.22 的案例里，第 8 步助手要自己把 USPTO、专利页的截图和文本复制进媒体目录、算哈希、写证据文件，
再手改合规尺子输入表的检索行。现在每查完一项执行一次：

    python save_web_evidence.py --root "<产品根>" --search design_patent \
        --url https://patents.google.com/patent/USD915070S1/en --query "yarn bag design" \
        --result "D915070 外观相近，需样品对比" --file 截图.png --file 页面文本.txt
    查不了、留给用户：--status user_todo --reason "需要登录 USPTO 账号" --tried "Sorftime 专利检索无结果" --tried "浏览器打开 tmsearch 要登录"
    （R09 S1-R15：留给用户前要按 04A 里每项的 channels 逐个试过，--tried 每个试过的渠道写一条：用了什么、卡在哪）

检索编号必须是合规尺子规则里的必做项（uspto_trademark / design_patent / utility_patent / image_reverse / schedule_a）。
它会：把文件复制到 S1_选品板块/amazon-ip-risk-assessment/媒体/08_权利核查/ 并记哈希；在 运行数据/08_web_evidence.json
追加一条记录；把 04A_ruler_inputs.yaml 里对应检索行标成已查（日期、结论、证据链接、截图、证据记录路径）。
结论是检索者写的判断，原样保存；本工具不判断有没有侵权。之后照常运行 compliance_ruler.py --run。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('web_evidence_ruler', HERE / 'compliance_ruler.py')
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)
MEDIA = cr.OUT / '媒体' / '08_权利核查'
INDEX = cr.OUT / '运行数据' / '08_web_evidence.json'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _record(view, search, url, query, result, files, status, reason, now, tried=()):
    view = Path(view)
    rules = cr.load_yaml(cr.RULES)
    required = {row['id']: row for row in rules['required_searches']}
    if search not in required:
        raise ValueError(f"检索编号「{search}」不是必做项；可用：{'、'.join(required)}")
    inputs_path = view / cr.OUT / cr.INPUTS
    if not inputs_path.is_file():  # 首次登记时按规则生成输入表骨架
        inputs_path.parent.mkdir(parents=True, exist_ok=True)
        inputs_path.write_text(yaml.safe_dump(cr.skeleton(view, rules), allow_unicode=True, sort_keys=False), encoding='utf-8')
    doc = yaml.safe_load(inputs_path.read_text(encoding='utf-8-sig')) or {}
    stored = []
    media = view / MEDIA
    for source in files:
        source = Path(source)
        if not source.is_file():
            raise ValueError(f'找不到证据文件：{source}')
        media.mkdir(parents=True, exist_ok=True)
        target = media / f'{search}_{source.name}'
        if target.exists() and _sha(target) != _sha(source):
            target = media / f'{search}_{_sha(source)[:8]}_{source.name}'
        shutil.copyfile(source, target)
        stored.append({'path': target.relative_to(view).as_posix(), 'sha256': _sha(target)})
    index_path = view / INDEX
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.is_file() else {'schema_version': 1, 'entries': []}
    entry = {'id': f"{search}-{sum(e['search'] == search for e in index['entries']) + 1}", 'search': search,
             'title': required[search]['title'], 'status': status, 'url': url, 'query': query,
             'captured_at': now.isoformat(timespec='seconds'), 'result': result, 'reason': reason, 'tried': list(tried),
             'files': stored}
    index['entries'].append(entry)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    rows = doc.setdefault('searches', [])
    row = next((r for r in rows if r.get('id') == search), None)
    if row is None:
        row = {'id': search, 'title': required[search]['title']}
        rows.append(row)
    if status == 'searched':
        row.update(status='searched', date=now.date().isoformat(), result=result, reason='')
    else:
        row.update(status='user_todo', reason=reason, tried=list(tried))
    row['evidence_record'] = INDEX.as_posix()
    if url:
        row['evidence_urls'] = list(dict.fromkeys([*(row.get('evidence_urls') or []), url]))
    if query:
        row['queries'] = list(dict.fromkeys([*(row.get('queries') or []), query]))
    if stored:
        row['screenshots'] = list(dict.fromkeys([*(row.get('screenshots') or []), *(f['path'] for f in stored)]))
    inputs_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return {'entry': entry['id'], 'search': search, 'status': status, 'files': len(stored), 'index': INDEX.as_posix()}


def save(root, search, *, url=None, query=None, result=None, files=(), status='searched', reason=None, tried=(), now=None):
    if status not in ('searched', 'user_todo'):
        raise ValueError('status 只能是 searched 或 user_todo')
    if status == 'searched' and not str(result or '').strip():
        raise ValueError('标为已查需要写结论（--result）：查到了什么、对本品意味着什么')
    if status == 'searched' and not (url or files):
        raise ValueError('已查需要至少一个证据：网址（--url）或截图/页面文本（--file）')
    if status == 'user_todo' and not str(reason or '').strip():
        raise ValueError('留给用户的检索需要写原因（--reason）')
    tried = [str(t).strip() for t in tried or () if str(t).strip()]
    if status == 'user_todo' and not tried:
        raise ValueError('留给用户前要先按 04A_ruler_inputs.yaml 里本项的 channels 逐个试；每个试过的渠道写一条 --tried'
                         '（用了什么、卡在哪）。只有确需登录或付费库的才留给用户')
    now = now or datetime.now(timezone.utc)
    root = Path(root).resolve()
    scripts = HERE.parents[2] / 'scripts'
    previous = list(sys.path)
    try:
        sys.path.insert(0, str(scripts))
        from case_mutation import mutate
        return mutate(root, 'S1', cr_step(root), lambda view: _record(view, search, url, query, (result or '').strip(),
                                                                       list(files), status, (reason or '').strip(), now, tried),
                      operation='web_evidence')
    finally:
        sys.path[:] = previous


def cr_step(root):
    """IP 与合规在新版九步里是第 8 步。"""
    return 8


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--search', required=True, help='必做检索编号')
    parser.add_argument('--url')
    parser.add_argument('--query')
    parser.add_argument('--result', help='检索结论（原样保存）')
    parser.add_argument('--file', action='append', default=[], help='截图或页面文本，可多次')
    parser.add_argument('--status', default='searched', choices=['searched', 'user_todo'])
    parser.add_argument('--reason', help='user_todo 的原因')
    parser.add_argument('--tried', action='append', default=[], help='user_todo 前试过的渠道，每个一条（用了什么、卡在哪）')
    options = parser.parse_args(argv)
    try:
        result = save(options.root, options.search, url=options.url, query=options.query, result=options.result,
                      files=options.file, status=options.status, reason=options.reason, tried=options.tried)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({'ok': True, **result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
