"""Verify local KB receipts before citing them; never infer business approval.

The library identity is the recorded MCP server plus its canonical library name.
The saved response is a local receipt, not a fresh call to the knowledge service.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

LIBRARIES = {
    'selection_knowledge_base': ('选品库', 'KC-'),
    'promotion_knowledge_base': ('产品推广库', 'PK-'),
}
BASE = Path('S1_选品板块/知识库依据/S1')


def _inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f'证据路径超出案例目录：{relative}（要写成相对产品根的路径，不能用 .. 或别的目录）')
    return path


def _payload(raw):
    if not isinstance(raw, dict) or raw.get('isError'):
        raise ValueError('原始回包不是成功对象')
    if isinstance(raw.get('structuredContent'), dict):
        return raw['structuredContent']
    if 'content' in raw:
        parts = [json.loads(x['text']) for x in raw['content']
                 if isinstance(x, dict) and x.get('type') == 'text']
        if len(parts) != 1 or not isinstance(parts[0], dict):
            raise ValueError('原始回包正文不唯一')
        return parts[0]
    return raw


def verify_reference(root, manifest_path, card_id, *, server, usage, _cache=None):
    """Return a receipt with verified=False for every failure (no silent fallback).

    usage is report-specific prose supplied by the caller, not copied from a
    shared call-level usage field. Verification proves identity and integrity,
    not the semantic truth of usage; the exact card statement is returned too.
    """
    root = Path(root)
    cache = {} if _cache is None else _cache
    result = dict(server=server, library=LIBRARIES.get(server, ('未知库', ''))[0],
                  card_id=card_id, usage=usage, manifest_path=str(manifest_path),
                  verified=False, verification_scope='本地回包身份与完整性', errors=[])
    try:
        if server not in LIBRARIES:
            raise ValueError('未识别的知识库')
        library, prefix = LIBRARIES[server]
        if not isinstance(card_id, str) or not re.fullmatch(re.escape(prefix) + r'\d{4,}', card_id):
            raise ValueError('卡号与知识库不匹配')
        if not isinstance(usage, str) or not usage.strip():
            raise ValueError('缺少本报告的具体引用用途')
        manifest = yaml.safe_load(_inside(root, manifest_path).read_text(encoding='utf-8-sig'))
        if not isinstance(manifest, dict):
            raise ValueError('证据清单格式错误')
        calls = [c for c in manifest.get('calls', []) if isinstance(c, dict)
                 and c.get('server') == server and card_id in c.get('card_ids', [])]
        if not calls:
            raise ValueError('清单未登记该库的这张卡')
        if len(calls) != 1:
            raise ValueError('同一引用存在重复回包登记，须明确来源')
        call = calls[0]
        if call.get('library') != library:
            raise ValueError('登记库名与服务标识不匹配')
        if call.get('status') != 'ok':
            raise ValueError('知识库调用未成功')
        if not call.get('response_path'):
            raise ValueError('缺少原始回包路径')
        source = _inside(root, call['response_path'])
        key = (source, call.get('response_selector'))
        if key not in cache:
            raw_key = ('raw', source)
            if raw_key not in cache:
                data = source.read_bytes()
                cache[raw_key] = (data, json.loads(data.decode('utf-8-sig')))
            data, document = cache[raw_key]
            selector = call.get('response_selector')
            if selector:
                if not isinstance(selector, str) or not selector.startswith('/'):
                    raise ValueError('response_selector 必须是 JSON Pointer')
                for token in selector[1:].split('/'):
                    token = token.replace('~1', '/').replace('~0', '~')
                    document = document[int(token)] if isinstance(document, list) else document[token]
                data = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')
            cache[key] = (hashlib.sha256(data).hexdigest(), document)
        digest, document = cache[key]
        result.update(response_path=call['response_path'], response_sha256=digest,
                      expected_sha256=call.get('response_sha256'))
        if digest != call.get('response_sha256'):
            raise ValueError('原始回包哈希变化或缺失，须重新核验')
        payload = _payload(document)
        # Only full returned cards count. by_type, prose mentions and citation
        # lists cannot establish that a card was actually returned.
        cards = [c for key in ('rules', 'cards') for c in payload.get(key, [])
                 if isinstance(c, dict) and c.get('id') == card_id]
        if len(cards) != 1:
            raise ValueError('原始回包没有唯一的卡片正文')
        card = cards[0]
        if not card.get('title') or not card.get('statement'):
            raise ValueError('卡片标题或正文缺失')
        result.update(title=card['title'], statement=card['statement'],
                      authority=card.get('authority'), card_status=card.get('status'), verified=True)
    except OSError as exc:  # 报告给用户看：只写案例内相对路径，不带本机绝对路径和系统报错原文
        name = getattr(exc, 'filename', None)
        try:
            name = Path(name).resolve().relative_to(Path(root).resolve()).as_posix() if name else ''
        except ValueError:
            name = Path(name).name
        result['errors'].append(('读不到文件：' + name) if name else '读不到知识库依据文件')
    except (ValueError, TypeError, KeyError, IndexError, yaml.YAMLError) as exc:
        result['errors'].append(str(exc))
    return result


def verify_references(root, references):
    """Deduplicate identical requested citations, preserving distinct uses."""
    output, seen, cache = [], set(), {}
    for ref in references:
        key = tuple(ref.get(k) for k in ('manifest_path', 'card_id', 'server', 'usage'))
        if key in seen:
            continue
        seen.add(key)
        output.append(verify_reference(root, **ref, _cache=cache))
    return output


def stage_references():
    """Specific applications reviewed against the original S1 card statements."""
    specs = [
        ('09', 'KC-0179', '综合报告的覆盖与缺项：缺失数据保持待核，来源冲突分别展示，低覆盖不据此给出精确份额。'),
        ('09', 'KC-0060', '综合报告的后续动作：市场、货源与权利边界完成后，仍须人工决定是否推进关键词及后续阶段。'),
        ('06', 'KC-0058', '货源与询价章节：采购前归档具体货源链接、尺寸材质、定制起订量、包装和检测资料，缺项仅进入拿样核对。'),
        ('06', 'KC-0041', '供应能力章节：起订量、交期、打样及质量记录分别待厂家确认，店铺声明不能代替检查。'),
        ('07', 'KC-0028', '成本与市场口径：跨工具冲突分别保留来源，必要时用保守情景，禁止将不同口径取平均。'),
        ('08', 'KC-0042', '权利核查章节：检索受限或未命中仍保留待核，初筛不能视作专利或外观权利放行。'),
    ]
    return [dict(manifest_path=(BASE / step / 'kb_evidence.yaml').as_posix(),
                 card_id=card, server='selection_knowledge_base', usage=usage)
            for step, card, usage in specs]


def _cell(value):
    return str(value).replace('|', '／').replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;')


def build_knowledge_sections(root, references=None):
    """Return sections keyed by stable ID for s1_stage_report's shared model.

    Replace old knowledge-only sections with this result. Do not append this
    next to old unverified citations or modify historical keyword lists.
    """
    receipts = verify_references(root, stage_references() if references is None else references)
    verified = [r for r in receipts if r['verified']]
    pending = [r for r in receipts if not r['verified']]
    lines = ['以下仅核对本地留存回包的库名、卡号和文件完整性；引用用途是本报告的方法应用，不代表产品结论已获知识库验证。', '']
    if verified:
        lines += [f'<details><summary>已核对 {len(verified)} 张知识卡（点开查看用途与回包）</summary>', '',
                  '| 库名与卡号 | 本报告引用用途 | 原始回包 | SHA-256 |', '| --- | --- | --- | --- |']
        for row in verified:
            lines.append('| ' + ' | '.join(_cell(x) for x in (
                row['library'] + ' ' + row['card_id'], row['usage'],
                row['response_path'], row['response_sha256'])) + ' |')
        lines += ['', '</details>']
    else:
        lines.append('本轮尚无通过原始回包核验的知识库引用。')
    if pending:
        lines += ['', '待核引用（不作为已核对依据）：', '']
        lines += ['- ' + _cell(r['library'] + ' ' + str(r['card_id'])) + '：' + _cell('；'.join(r['errors'])) for r in pending]
    return {'knowledge-evidence': {'title': '知识库依据与引用核验', 'body': '\n'.join(lines), 'receipts': receipts}}
