"""Deterministic S1 progression and a persistent outbox for visible step cards.

This CLI prepares and checks reply text; it cannot inspect the client's rendered
conversation or change the user's model. Business evidence stays in Skill files.
"""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

import yaml
try:
    import step_evidence_window as evidence_windows
except ImportError:
    _window_path = Path(__file__).resolve().with_name('step_evidence_window.py')
    if not _window_path.is_file():
        _window_path = Path(__file__).resolve().parents[1] / 'source/starsky-amazon-agent/scripts/step_evidence_window.py'
    _window_spec = importlib.util.spec_from_file_location('starsky_step_evidence_window', _window_path)
    evidence_windows = importlib.util.module_from_spec(_window_spec)
    _window_spec.loader.exec_module(evidence_windows)
try:
    from host_support import (visibility_boundary, visible_card, require_separate_invocation,
                              latest_user_message, visible_cards_in_reply)
except ImportError:
    # Source-tree controllers share the exact installer module; built plugins
    # receive this file beside their controllers through prepare_s1_flow.
    _host_path = Path(__file__).resolve().with_name('host_support.py')
    if not _host_path.is_file():
        _host_path = Path(__file__).resolve().parents[1] / 'installer/host_support.py'
    _spec = importlib.util.spec_from_file_location('starsky_host_support', _host_path)
    _hosts = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_hosts)
    visibility_boundary, visible_card, require_separate_invocation = _hosts.visibility_boundary, _hosts.visible_card, _hosts.require_separate_invocation
    latest_user_message, visible_cards_in_reply = _hosts.latest_user_message, _hosts.visible_cards_in_reply

# The user's own words (read from the host transcript) that allow several steps in one turn;
# an explicit "stop after this card" in the same message always wins.
CONTINUOUS_RUN = re.compile(r'连续跑|连跑|连续(执行|推进|做|完成)|一口气|自动(继续|推进)|直接进(入)?下一步|(不用|别|不要|无需)(再)?(每步)?(等我|让我)?(重复)?(确认|回复)'
                            r'|依次.{0,16}步|continuous', re.I)
STOP_AFTER_CARD = re.compile(r'出卡后停|做完(这|本)步(就)?停|本轮只做|只做第\s*\d+\s*步|每步(都)?停')


def step_guide(home, n):
    """templates/s1_m2_execution.md「按九步执行」里第 n 步那一段（含其下的缩进段落）；找不到返回空串。"""
    try:
        text = (Path(home) / 'templates/s1_m2_execution.md').read_text(encoding='utf-8-sig')
    except OSError:
        return ''
    section = text.split('## 按九步执行', 1)[-1].split('\n## ', 1)[0]
    found = re.search(rf'(?ms)^{n}\. (.*?)(?=^\d\. |^独立01|\Z)', section)
    return found.group(1).strip() if found else ''


def s1_scope_only(root, state=None, *, inputs=None):
    """Explicit current input scope takes precedence over legacy workflow/handoffs.

    No mutation: old stage cursors and historical transfer files remain evidence.
    A handoff or sample_ready decision never grants wider execution scope.
    """
    root = Path(root)
    if state is None:
        path = root / 'workflow_state.yaml'
        state = yaml.safe_load(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    state = state or {}
    # A user's explicit "继续 S2" is recorded in workflow_state only; the sealed S1 decision inputs stay
    # byte-identical so the step 9 receipt still matches them (S1-R24).
    if any('S2' in (row or {}).get('stages', []) and (row or {}).get('source') == 'user'
           for row in state.get('scope_expansions') or []):
        return False
    path = root / 's1_decision_inputs.yaml'
    if inputs is None:
        inputs = yaml.safe_load(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    for source in (inputs or {}, state, state.get('entry') or {}, state.get('s1_execution') or {}):
        for key in ('execution_scope', 'task_scope', 'scope'):
            value = source.get(key)
            if isinstance(value, dict):
                value = value.get('value')
            if value is not None:
                normalized = str(value).strip().lower().replace('-', '_').replace(' ', '')
                if normalized in {'s1', 's1_only', 'only_s1', '仅s1', '只做s1'}:
                    return True
                if normalized in {'full_chain', 's1_s5', 'all_stages', 's1_s2'}:
                    return False
                raise ValueError('未知执行范围：' + str(value))
    return False


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def business_receipt(value):
    if value.get('schema_version') == 3 and isinstance(value.get('receipt'), dict):
        original = business_receipt(value['receipt'])
        generated = {'schema_version', 'receipt', 'machine_fields', 'card', 'knowledge', 'validation', 'ack',
                     'receipt_versions', 'receipt_ref', 'raw_ref', 'evidence_windows'}
        return {**original, **{key: item for key, item in value.items() if key not in generated}}
    return value


def receipt_digest(value):
    return digest({key: item for key, item in value.items()
                   if key not in {'receipt_versions', 'business_history', 'business_decisions', 's1_stage_data', 'evidence_windows'}})


def compact_receipt(value):
    fields = {'step', 'outcome', 'summary', 'skills_used', 'skills_called', 'mcp', 'mcp_calls', 'kb_cards',
              'kb_evidence', 'tools_run', 'decision', 'user_reply', 'artifacts', 'needs_user', 'skip_reason', 'key_numbers', 'human_report', 'reports'}
    small = {key: item for key, item in value.items() if key in fields}
    # Response bytes stay at the envelope/source selectors, never in state.
    for key in ('mcp_calls',):
        if isinstance(small.get(key), list):
            small[key] = [{k: v for k, v in row.items() if k not in {'response', 'raw_response', 'payload', 'content', 'data'}}
                          if isinstance(row, dict) else row for row in small[key]]
    if isinstance(small.get('kb_evidence'), dict):
        evidence = dict(small['kb_evidence'])
        evidence.pop('responses', None); evidence.pop('raw', None)
        if isinstance(evidence.get('calls'), list):
            evidence['calls'] = [{k: v for k, v in row.items() if k not in {'response', 'raw_response', 'payload', 'content', 'data'}}
                                 if isinstance(row, dict) else row for row in evidence['calls']]
        small['kb_evidence'] = evidence
    return small


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')).hexdigest()


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = text if isinstance(text, bytes) else text.encode('utf8')
    if path.exists() and path.read_bytes() == expected:
        return
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        if isinstance(text, bytes):
            tmp.write_bytes(text)
        else:
            tmp.write_text(text, encoding='utf8', newline='\n')
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def card_with_current_root(card, state, root):
    """R06.1 AS-03: a card generated before the product folder was renamed shows the folder's current path."""
    for alias in (state or {}).get('root_aliases') or []:
        if isinstance(alias, str) and alias and alias != str(root):
            card = card.replace(alias, str(root))
    return card


def store_receipt_version(versions, receipt_id, version, events):
    """A recorded receipt version is sealed. 3.0.25 step 6: an earlier record wrote its version into the receipt
    file, then failed before the event was saved; native reruns moved the revision on and every retry was
    refused forever. A version no recorded event refers to is an orphan of that unfinished record: replace it."""
    if receipt_id in versions and versions[receipt_id] != version:
        if any(receipt_id in (old.get('receipt_id'), old.get('stored_receipt_id')) for old in events):
            raise ValueError('已封存的回执版本不可覆盖：这份回执已登记过。要更新本步结果，先用 revise 重开本步再 record。')
    versions[receipt_id] = version


WINDOW_STEPS_HINT = 4  # S1-R21: steps recorded in one window before the card suggests a new window


class Flow:
    def __init__(self, root, home, stage='S1'):
        self.root, self.home = Path(root).resolve(), Path(home).resolve()
        self.require_current_plugin()
        self.ui_modules = {}
        for name in ('chat_card_v3', 'stage_acceptance_summary'):
            spec = importlib.util.spec_from_file_location('starsky_ui_' + name, self.home / 'scripts' / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.ui_modules[name] = module
        self.stage, self.state_key = stage, stage.lower() + '_execution'
        self.path = self.root / 'workflow_state.yaml'
        self.output = self.root / '00_总控/starsky-fullchain-workflow/运行数据/流程控制' / stage
        spec = importlib.util.spec_from_file_location('starsky_s1_roadmap', self.home / 'scripts/s1_roadmap.py')
        self.roadmap_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.roadmap_module)
        roadmap = self.roadmap_module.resolve_roadmap(self.home, self.root)
        self.roadmap_stage = roadmap['stages'][stage]
        self.steps = roadmap['stages'][stage]['steps']
        self.total = len(self.steps)
        self.delivery_step = int(self.roadmap_stage.get("delivery_step", self.total))
        if [s['n'] for s in self.steps] != list(range(1, self.total + 1)):
            raise ValueError(self.stage + ' 步骤编号必须连续')
        self.models = yaml.safe_load((self.home / 'templates/model_routing.yaml').read_text(encoding='utf-8-sig'))
        display_spec = importlib.util.spec_from_file_location(
            'starsky_step_model_display', self.home / 'scripts/model_display.py')
        display_module = importlib.util.module_from_spec(display_spec)
        display_spec.loader.exec_module(display_module)
        self.model_display = display_module.ModelDisplay(self.models)
        self.validator = self.home / ('skills/starsky-fullchain-workflow/scripts/verify_s1_acceptance.py' if stage == 'S1' else 'scripts/workflow_checks.py')
        self.accepted_statuses = self.ui_modules['stage_acceptance_summary'].ACCEPTED_STATUSES

    def require_current_plugin(self):
        # Load from this business tree, including when controllers are imported.
        path=self.home/'scripts/plugin_freshness.py'
        if path.is_file():
            spec=importlib.util.spec_from_file_location('starsky_runtime_identity',path)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        else:
            from plugin_freshness import guard_plugin
            guard_plugin(self.home)
            return None
        module.guard_plugin(self.home)
        module.guard_plugin(Path(__file__).resolve().parents[1])
        return module

    def bind_plugin_identity(self, state):
        module=self.require_current_plugin()
        return module.bind_workflow_identity(self.home,state) if module else ''

    def state(self):
        return (yaml.safe_load(self.path.read_text(encoding='utf-8-sig')) or {}) if self.path.exists() else {}

    def save(self, state):
        # Reader brevity applies only after the card, never to report production.
        for owner in [state, *(state.get('s' + str(n) + '_execution') or {} for n in range(1, 6))]:
            if isinstance(owner.get('reader_preferences'), dict):
                owner['reader_preferences']['scope'] = 'chat_reply'
        atomic_write(self.path, yaml.safe_dump(state, allow_unicode=True, sort_keys=False))

    def pending_reply_card(self, state, flow):
        """Recover a lost outbox pointer without claiming the client saw a card."""
        events = flow.get('events') or flow.get('records') or []
        last = events[-1] if events else None
        pending = flow.get('pending_card')
        proof = (last or {}).get('reply_ack') or flow.get('reply_ack') or {}
        if (not pending and last and proof.get('evidence') not in {'native_session_final', 'host_user_turn', 'continuous_final'}
                and last.get('card_path') and not getattr(self, '_recovering', False)):
            pending = flow['pending_card'] = last['card_path']
            self.save(state)
        if not pending:
            return None
        event = next((r for r in reversed(events) if r.get('card_path') == pending), None)
        return self.current_card(event['card'] if event and event.get('card') else self._read_card(pending), state)

    def current_card(self, card, state=None):
        return card_with_current_root(card, state if state is not None else self.state(), self.root)

    def require_reply_ack(self, state=None, flow=None):
        if state is None:
            state, flow = self._execution()
        self.settle_continuous(state, flow)
        card = self.pending_reply_card(state, flow)
        if card:
            if self.continue_in_batch(state, flow):
                return
            raise ValueError(card.strip() + '\n\n先把上一步完整步骤卡原样补发为一条正式回复；下一轮直接 begin，会自动核对这张卡。'
                             '\n（用户说「连续跑」时可以一轮做完多步，本轮最后的正式回复按顺序贴出每张卡。）')

    def continue_in_batch(self, state, flow):
        """Defer this card to the end of the turn when the user's own latest message asked to keep going."""
        try:
            message = latest_user_message(self.root)
        except (OSError, ValueError):
            message = None
        if not message or not CONTINUOUS_RUN.search(message['text']) or STOP_AFTER_CARD.search(message['text']):
            return False
        batch = flow.get('continuous_run')
        if batch and batch.get('user_index') != message['index']:
            return False  # an older run that settle_continuous could not close; never extend it silently
        batch = flow.setdefault('continuous_run', {'user_index': message['index'], 'session_id': message['session_id'],
                                                   'message': message['text'][:200], 'cards': []})
        pending = flow.get('pending_card')
        if pending and pending not in batch['cards']:
            batch['cards'].append(pending)
        flow['pending_card'] = None
        self.save(state)
        return True

    def settle_continuous(self, state, flow, *, force=False):
        """On the next user turn, every card from a continuous run must be in the final reply(ies)."""
        batch = flow.get('continuous_run')
        if not batch or not batch.get('cards'):
            return None
        if not force:
            try:
                message = latest_user_message(self.root)
            except (OSError, ValueError):
                message = None
            if message and message['index'] == batch['user_index']:
                return None  # still the same turn; the cards are shown together at its end
        events = {e.get('card_path'): e for e in flow.get('events', [])}
        # A revision, reopen or rebuild may drop steps whose cards were queued here; their cards
        # left with the revision backup, so only the cards of steps that still exist are owed.
        batch['cards'] = [p for p in batch['cards'] if p in events]
        if not batch['cards']:
            flow.pop('continuous_run', None)
            self.save(state)
            return None
        cards = [self.current_card(events[p].get('card') or self._read_card(p), state) for p in batch['cards']]
        boundary = (events[batch['cards'][0]] or {}).get('visibility_boundary') or {}
        proof, missing = visible_cards_in_reply(self.root, cards, boundary)
        if missing:
            numbers = '、'.join(str(events[p]['n']) for p, c in zip(batch['cards'], cards) if c in missing)
            raise ValueError(f'连续跑的第 {numbers} 步步骤卡还没在正式回复里出现；先把这些卡按顺序原样补发成一条正式回复，'
                             '下一轮再继续。\n\n' + '\n\n'.join(card.strip() for card in missing))
        for path, card in zip(batch['cards'], cards):
            events[path]['reply_ack'] = dict(proof, receipt_id=events[path]['receipt_id'],
                                             card_sha256=hashlib.sha256(card.encode('utf8')).hexdigest())
        flow.pop('continuous_run', None)
        flow['pending_card'] = None
        flow['reply_evidence'] = proof['evidence']
        if flow.get('delivery_status') == 'awaiting_card':
            flow['delivery_status'] = 'complete'
            state['next_step'] = {'step_id': 's1_followup', 'instruction': '报告已交付；有后补资料时更新第 6–9 步。未经授权不进入 S2。'}
        self.save(state)
        return '已核对连续跑的 ' + str(len(cards)) + ' 张步骤卡。'

    def revision_help(self, number):
        state,flow=self._execution()
        prefix=f'& "{sys.executable}" "{self.home / "scripts/workflow_flow.py"}" "{self.root}"'
        common=f' --stage {self.stage}'
        lines=[]
        pending=flow.get('pending_card')
        if pending:
            path=(self.root/pending).resolve()
            lines += ['先将待发卡单独作为最终回复展示：'+str(path),prefix+' ack'+common]
        else:lines.append('当前没有待发卡，无需空做ack；若卡片指针丢失，先运行 cards 检查。')
        lines += [prefix+' revise'+common+f' --from-step {number} --user-message "用本次真实资料变更原因替换此文字"',
                  '按修订范围重新生成受影响步骤的真实回执；不改旧哈希、不自动重绑。',
                  prefix+' record'+common+f' --receipt "{self.output / "receipts" / (str(number).zfill(2)+".json")}"']
        return '\n'.join(lines)

    def ack_previous_card(self, state, flow):
        """Acknowledge the previous card when it is already in a final reply of this session (begin, continue-s2)."""
        auto_ack = None
        try:
            self.require_reply_ack(state, flow)
        except ValueError as refused:
            if not self.pending_reply_card(state, flow):
                raise
            try:
                auto_ack = self.ack_current()
            except ValueError as unseen:
                # Not shown yet: keep the original refusal (it carries the full card to repost) and say why.
                raise ValueError(str(refused) + '\n\n自动核对未通过：' + str(unseen)) from None
            state, flow = self._execution()
            self.require_reply_ack(state, flow)
        return state, flow, auto_ack

    def begin(self):
        """Mandatory preflight before any business Skill/tool for the next step."""
        self.require_current_plugin()
        state, flow = self._execution()
        # R09 (user decision 2026-10-23): begin acknowledges the previous card itself. The proof is unchanged:
        # the card must already be in a final reply of this host session (visible_card / continuous settle),
        # which can only exist after an earlier turn ended. It saves one ~1-minute tool round trip per step.
        # Continuous runs keep their own end-of-turn settlement: only a card the old check would refuse is acked here.
        state, flow, auto_ack = self.ack_previous_card(state, flow)
        events = flow.get('events') or flow.get('records') or []
        # A continuous run is settled only on a later user turn, which itself proves the separate invocation.
        if (not auto_ack and events and events[-1].get('reply_ack')
                and events[-1]['reply_ack'].get('evidence') != 'continuous_final'):
            require_separate_invocation(events[-1]['reply_ack'], self.root)
        if not flow.get('continuous_run'):
            flow['visibility_boundary'] = visibility_boundary(self.root)
        window_notice = evidence_windows.begin_window(flow)
        self.save(state)
        try:
            pack = self.step_pack()
        except (KeyError, ValueError, OSError, TypeError) as error:  # a missing pack never blocks the step itself
            pack = f'本步工作包暂时生成失败（{error}），按 templates/s1_m2_execution.md 本步段落执行。'
        head = f"{self.stage} 第 {flow['cursor']} / 共 {self.total} 步可执行；本步完成后先完整出卡再核对回复。"
        if flow.get('continuous_run'):
            head = (f"{self.stage} 第 {flow['cursor']} / 共 {self.total} 步可执行（连续跑）：record 后直接 begin 下一步；"
                    '本轮最后的正式回复按顺序原样贴出本轮每张步骤卡，遇到要用户拍板的步骤就停。')
        if auto_ack:
            head = '已核对上一步步骤卡（' + str(auto_ack).strip().split('\n')[0][:60] + '）。' + head
        return head + ('\n' + window_notice if window_notice else '') + '\n\n' + pack

    def init(self, path):
        self.require_current_plugin()
        state = self.state()
        self.bind_plugin_identity(state)
        if self.state_key in state:
            self.save(state)
            return self.status(roadmap=True)
        if state.get('completed_steps') or state.get('current_step') not in (None, '', 'intake_interview', 'data_intake'):
            raise ValueError('旧任务需要按现有文件恢复；不得清空历史后从第 1 步重跑。读取 templates/s1_execution.md 的恢复规则。')
        if path not in ('condition_first', 'supply_first'):
            raise ValueError('未知 S1 路径')
        state[self.state_key] = {'schema_version': 1, 'path': path, 'cursor': 1, 'records': [],
                                 'run_mode': state.get('run_mode', 'production'),
                                 'events': [], 'pending_card': None, 'delivery_status': 'pending',
                                 'compact_case': True}
        evidence_windows.start_round(state[self.state_key], origin='init')
        if hasattr(self, 'roadmap_module'):
            state[self.state_key].update(self.roadmap_module.execution_snapshot(self.roadmap_stage))
        if state[self.state_key].get('roadmap_version')=='r01.m2':
            state[self.state_key]['category_comparison_contract']='r05.adjacent.v1'
            state[self.state_key]['market_report_contract']='r09.narrative.v1'  # R09: 必填判断才放行第4步
        self._position(state)
        self.save(state)
        if self.stage == 'S1':
            self.refresh_nav_dashboard()
        return self.status(roadmap=True)

    def _position(self, state):
        flow = state[self.state_key]
        step = self.steps[flow['cursor'] - 1]
        state['current_step'] = step['control']['id']
        state['next_step'] = {'step_id': step['control']['id'], 'step_number': step['n'],
                              'instruction': self.name(step, flow['path']), 'skill': step['skill']}

    def file(self, value):
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('产物缺失、为空或超出产品目录：' + str(value))
        overrides = getattr(self, '_business_overrides', {})
        relative = path.relative_to(self.root).as_posix()
        if relative in overrides:
            present = bool(overrides[relative])
        else:
            store = self.case_store()
            present = store.exists(path) and bool(store.read_bytes(path))
        if not present:
            raise ValueError('产物缺失、为空或超出产品目录：' + str(value))
        return path

    def case_store(self):
        helper = getattr(self, '_case_store_module', None)
        if helper is None:
            paths_before = list(sys.path)
            try:
                sys.path.insert(0, str(self.home / 'scripts'))
                spec = importlib.util.spec_from_file_location('starsky_flow_case_store', self.home / 'scripts/r01_case_store.py')
                helper = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = helper
                spec.loader.exec_module(helper)
                self._case_store_module = helper
            finally:
                sys.path[:] = paths_before
        return helper.CompactCase(self.root)

    def read_bytes(self, value):
        path = self.file(value)
        overrides = getattr(self, '_business_overrides', {})
        relative = path.relative_to(self.root).as_posix()
        return overrides[relative] if relative in overrides else self.case_store().read_bytes(path)

    def record_receipt(self, record):
        previous_paths = list(sys.path)
        try:
            sys.path.insert(0, str(self.home / 'scripts'))
            spec = importlib.util.spec_from_file_location('starsky_receipt_reader', self.home / 'scripts/workflow_checks.py')
            helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
            return helper.load_record_receipt(self.root, record)
        finally:
            sys.path[:] = previous_paths

    def name(self, step, path):
        return step.get('supply_first_name', step['name']) if path == 'supply_first' else step['name']

    def model(self, step):
        return self.model_display.recommendation(step, strict=True)

    def model_guidance(self, step):
        return self.model_display.guidance(self.stage, step['n'])

    def stable_main(self):
        return self.model_display.stable_main()

    def next_model_instruction(self, step=None):
        recommendation = self.model(step) if step else self.next_model()
        return self.model_display.next(recommendation, within_stage=step is not None)

    def next_stage(self):
        if self.stage == 'S1' and self.s1_only():
            return None
        return None if self.stage == 'S5' else 'S' + str(int(self.stage[1:]) + 1)

    def s1_only(self):
        path = self.root / 's1_decision_inputs.yaml'
        exists = ('s1_decision_inputs.yaml' in getattr(self, '_business_overrides', {}) or self.case_store().exists(path))
        inputs = yaml.safe_load(self.read_bytes(path).decode('utf-8-sig')) if exists else {}
        return s1_scope_only(self.root, self.state(), inputs=inputs)

    def decision_is_current(self):
        inputs = self.root / 's1_decision_inputs.yaml'
        if not self.case_store().exists(inputs):
            return not bool(self.state().get('s1_decision'))
        compact = (self.state().get('s1_decision') or {}).get('compact_receipt')
        script = self.home / 'scripts' / ('s1_stage_report.py' if compact else 's1_decision_report.py')
        if not script.exists(): return False
        paths_before = list(sys.path)
        try:
            sys.path.insert(0, str(self.home / 'scripts'))
            spec = importlib.util.spec_from_file_location('s1_decision_current', script)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            return module.validate_compact_output_set(self.root, compact) if compact else module.validate_output_set(self.root)
        except (ValueError, OSError, KeyError, TypeError):
            return False
        finally:
            sys.path[:] = paths_before

    def generate_decision(self, state, knowledge=None, *, rebuild=False):
        # The recovery renderer must be able to inspect an invalid generated
        # envelope before normal indexed reads; all source seals remain checked.
        if rebuild:
            return self._rebuild_decision(state, knowledge)
        if self.stage != 'S1' or not ('s1_decision_inputs.yaml' in getattr(self, '_business_overrides', {}) or self.case_store().exists('s1_decision_inputs.yaml')):
            return {}
        receipt_path = (self.output / 'receipts/09.json').relative_to(self.root).as_posix()
        paths_before = list(sys.path)
        try:
            sys.path.insert(0, str(self.home / 'scripts'))
            spec = importlib.util.spec_from_file_location('s1_compact_producer', self.home / 'scripts/s1_stage_report.py')
            producer = importlib.util.module_from_spec(spec); spec.loader.exec_module(producer)
            with self.case_store().materialize(overrides=getattr(self, '_business_overrides', {})) as view:
                produced = producer.run(view.root, compact=True, receipt_path=receipt_path,
                                        knowledge_verification=knowledge)
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise ValueError('S1 决策报告生成失败：' + str(error)) from error
        finally:
            sys.path[:] = paths_before
        document = producer.load(self.file(receipt_path))
        model = document['s1_stage_data']['decision_model']
        state['s1_decision'] = {k: model.get(k) for k in ('decision', 'input_sha256', 'generated_at', 'blockers')}
        state['s1_decision'].update(purchase_authorized=False, compact_receipt=receipt_path)
        return {'decision_yaml': {'path': receipt_path, 'selector': '/s1_stage_data'},
                'report_md': str(Path(produced['md']).relative_to(self.root).as_posix()),
                'report_html': str(Path(produced['html']).relative_to(self.root).as_posix())}

    def _rebuild_decision(self, state, knowledge=None):
        receipt = self.output / 'receipts/09.json'
        before = list(sys.path)
        try:
            sys.path.insert(0, str(self.home / 'scripts'))
            spec = importlib.util.spec_from_file_location('s1_rebuild_producer', self.home / 'scripts/s1_stage_report.py')
            producer = importlib.util.module_from_spec(spec); spec.loader.exec_module(producer)
            produced = producer.rebuild_compact(self.root, receipt, knowledge_verification=knowledge)
            document = producer.load(receipt)
            model = document['s1_stage_data']['decision_model']
            state['s1_decision'] = {k: model.get(k) for k in ('decision', 'input_sha256', 'generated_at', 'blockers')}
            state['s1_decision'].update(purchase_authorized=False, compact_receipt=receipt.relative_to(self.root).as_posix())
            return {'decision_yaml': {'path': receipt.relative_to(self.root).as_posix(), 'selector': '/s1_stage_data'},
                    'report_md': Path(produced['md']).relative_to(self.root).as_posix(),
                    'report_html': Path(produced['html']).relative_to(self.root).as_posix()}
        finally:
            sys.path[:] = before

    def next_model(self):
        if self.stage == 'S1' and self.s1_only():
            return '仅 S1，本轮结束；有新证据再复核'
        target = self.next_stage()
        return self.model_display.first_step(target or 'S5')

    def rebuild(self, number):
        """Regenerate delivery through the registered renderer, never grant ack."""
        if self.stage != 'S1' or number != 9:
            raise ValueError('rebuild 目前只支持 S1 第 9 步')
        self.require_current_plugin()
        state, flow = self._execution()
        self.require_reply_ack(state, flow)
        if flow['cursor'] < 9 and flow.get('delivery_status') != 'complete':
            raise ValueError(f"S1 当前停在第 {flow['cursor']} 步，第 9 步还没轮到；"
                             f"先按顺序完成第 {flow['cursor']}–8 步（begin → record），再 rebuild 第 9 步")
        if flow['cursor'] != 9 or flow.get('delivery_status') == 'complete':
            raise ValueError('第 9 步已交付；要重做请先用 reopen --stage S1 --steps 9 --reason "实际修订原因" 重开第 9 步')
        before = list(sys.path)
        try:
            sys.path.insert(0, str(self.home / 'scripts'))
            from sync_s1_decision_reports import rollback_transaction
            with rollback_transaction(self.root):
                # Current evidence is re-read by knowledge_check at record; this
                # build is preparation, not business acceptance or a reply card.
                artifacts = self.generate_decision(state, rebuild=True)
                state.setdefault('stage_acceptance', {}).setdefault('S1', {}).update(status='pending')
                self.save(state)
                return json.dumps({'status': 'prepared', 'artifacts': artifacts,
                                   'next': 'record 第9步；正式出卡，下一轮 begin 或 ack 会自动核对'}, ensure_ascii=False)
        finally:
            sys.path[:] = before

    def stage_purpose(self, stage=None):
        return {
            'S2': '文案板块（词库 + Listing）',
            'S3': '视觉板块（主副图 / A+ / 视频）',
            'S4': '广告板块（策略 / 架构 / Bulk）',
            'S5': '运营板块（巡检）',
        }.get(stage or self.next_stage() or 'S5', '')

    def switch_steps(self):
        if self.stage == 'S1' and self.s1_only():
            return '仅 S1：交付报告与拿样研究判断，本轮结束。sample_ready 仅表示可考虑拿样，不是采购授权；后补资料时复核。'
        target = self.next_stage()
        if not target:
            return '核对待审核动作；后台执行另按实际授权。本窗口可结束本轮。'
        model_step = (self.next_model_instruction() if self.stable_main() else '左下角模型先选 '
                      + self.next_model() + '（写 Listing / 出方案时再按卡片切；单一模型忽略）')
        return ('你现在只做三步：①同一产品项目新开一个 Codex 窗口；②' + model_step + '；③把下方口令整段粘进新窗口。'
                '已授权后补的厂家核实项不必现在补。这是换窗继续流程，不是采购批准。本窗口停止。')

    def _execution(self):
        state = self.state()
        if self.stage != 'S1' and self.s1_only():
            raise ValueError('本轮仅 S1；历史阶段状态不能授权继续后续阶段')
        if self.state_key not in state:
            raise ValueError('未初始化或旧任务待恢复，请先执行 status')
        flow = state[self.state_key]
        expected = self.steps[flow['cursor'] - 1]['control']['id']
        if state.get('current_step') != expected:
            raise ValueError('流程状态被其他脚本改写；先恢复一致性，不得猜测下一步')
        return state, flow

    def artifact_hash(self, item):
        value = item.get('path') if isinstance(item, dict) else item
        path = self.file(value)
        selector = item.get('selector') if isinstance(item, dict) else None
        raw = self.read_bytes(path)
        if selector is not None:
            if not isinstance(selector, str) or not selector.startswith('/'):
                raise ValueError('selector must be a JSON Pointer beginning with /')
            value = json.loads(raw.decode('utf-8-sig'))
            try:
                for token in selector[1:].split('/'):
                    token = token.replace('~1', '/').replace('~0', '~')
                    if isinstance(value, list):
                        if not token.isdecimal(): raise ValueError('invalid JSON Pointer array index')
                        value = value[int(token)]
                    else:
                        value = value[token]
            except (KeyError, IndexError, TypeError) as error:
                raise ValueError('JSON Pointer does not select an existing value') from error
            raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')
        return hashlib.sha256(raw).hexdigest()

    def _artifacts(self, receipt, step):
        artifacts = dict(receipt.get('artifacts', {}))
        if receipt.get('human_report'):
            artifacts['human_report'] = receipt['human_report']
        if receipt['outcome'] not in {'blocked', 'skipped', 'waiting_external'}:
            missing = set(step['control']['required_artifacts']) - artifacts.keys()
            if missing:
                raise ValueError('缺本步必需产物：' + ', '.join(sorted(missing)))
        empty = sorted(role for role, value in artifacts.items()
                       if not (value.get('path') if isinstance(value, dict) else value))
        if empty:
            raise ValueError('回执 artifacts 里这些产物还没填路径：' + ', '.join(empty) +
                             '；填本步实际生成的文件（相对产品根），或用 draft 重新生成回执草稿')
        captured = {}
        for role, value in artifacts.items():
            path = self.file(value.get('path') if isinstance(value, dict) else value)
            stable = self.output / 'receipts' / f"{step['n']:02}.json"
            if path == stable and not (isinstance(value, dict) and value.get('selector')):
                raise ValueError('流程回执禁止整文件自引用；请指定独立数据 selector')
            captured[role] = {'path': path.relative_to(self.root).as_posix(), 'sha256': self.artifact_hash(value)}
            if isinstance(value, dict) and value.get('selector') is not None:
                captured[role]['selector'] = value['selector']
        return captured

    def _decision(self, receipt, step, flow):
        n, outcome = step['n'], receipt['outcome']
        if self.stage != 'S1':
            return
        if outcome == 'blocked':
            if not receipt.get('needs_user'):
                raise ValueError('阻塞卡必须说明具体动作或待修复事项')
            return
        if flow.get('roadmap_version') == 'r01.m2':
            paths_before = list(sys.path)
            try:
                sys.path.insert(0, str(self.home / 'scripts'))
                spec = importlib.util.spec_from_file_location('starsky_m2_research', self.home / 'scripts/s1_research_gates.py')
                gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)
                with self.case_store().materialize(overrides=getattr(self, '_business_overrides', {})) as view:
                    checked = gate.check_m2_step(view.root, n, receipt, flow)
                if checked['status'] != 'pass': raise ValueError('; '.join(checked['errors']))
                if n == 3:
                    flow['validated_positioning'] = checked['checks']['validated_positioning']
                    flow['demand_evidence_status'] = checked['checks']['demand']['status']
                    if checked['checks'].get('demand_exception'): flow['demand_exception'] = checked['checks']['demand_exception']
            finally:
                sys.path[:] = paths_before
            return
        decision = receipt.get('decision', {})
        message = decision.get('message', '').strip()
        generic = message.lower() in {'', '继续', '下一步', '好', 'ok', '确认', '已完成'}
        if n == 4 and (decision.get('source') != 'user' or not decision.get('selection') or generic):
            raise ValueError('选款需要具体 SKU / 编号及用户选择依据；泛化继续不算拍板')
        if outcome == 'deferred':
            explicit = re.search(r'deferred|后补|后.{0,12}补|暂.{0,8}(跳过|不填|不回填)|先.{0,8}跳过', message, re.I)
            if n != 6 or decision.get('source') != 'user' or not explicit:
                raise ValueError('仅供应商回填可后补；需保留用户明确后补的原话及授权')
        supplier = next((r for r in flow['records'] if r['n'] == 6), {})
        if n == 7 and supplier.get('outcome') in {'deferred', 'partial'} and outcome != 'partial':
            raise ValueError('供应商缺项仍在，成本步骤必须标 partial；不能写成事实完整')

    def business_check(self, receipt, step):
        if self.stage == 'S1' and (self.state().get(self.state_key) or {}).get('roadmap_version') == 'r01.m2':
            spec = importlib.util.spec_from_file_location('starsky_m2_business_checks', self.home / 'scripts/workflow_checks.py')
            checks = importlib.util.module_from_spec(spec); spec.loader.exec_module(checks)
            return checks.check_step(self.home, self.root, self.stage, step['n'], receipt,
                                     knowledge_verification=getattr(self, '_knowledge_for_check', None))
        return {'status': 'pass', 'errors': []}

    def knowledge_check(self, receipt, step):
        paths_before = list(sys.path)
        try:
            sys.path.insert(0, str(self.home / 'scripts'))
            spec = importlib.util.spec_from_file_location('step_knowledge_gate', self.home / 'scripts/verify_kb_evidence.py')
            gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)
            with self.case_store().materialize(overrides=getattr(self, '_business_overrides', {})) as view:
                if step['n'] == self.total:
                    state, flow = self._execution()
                    records = [{**row, 'receipt': self.record_receipt(row)} for row in flow.get('records', [])]
                    records.append({'n':step['n'], 'receipt':receipt})
                    checked = gate.verify_stage(view.root,self.home,self.stage,records)
                else:
                    checked = gate.record_references(view.root,self.home,self.stage,step['n'],receipt)
                return json.loads(json.dumps(checked, ensure_ascii=False).replace(
                    str(view.root).replace('\\', '\\\\'), str(self.root).replace('\\', '\\\\')).replace(
                        Path(view.root).as_posix(), self.root.as_posix()))
        finally:
            sys.path[:] = paths_before

    def knowledge_card(self, number, checked):
        # The Flow API may be loaded by file path, without the delivery folder on sys.path.
        paths_before = list(sys.path)
        try:
            sys.path.insert(0, str(self.home / 'scripts'))
            spec = importlib.util.spec_from_file_location('step_knowledge_ui', self.home / 'scripts/kb_ui.py')
            ui = importlib.util.module_from_spec(spec); spec.loader.exec_module(ui)
            return ui.card_lines(self.root, self.home, self.stage, number, checked)
        finally:
            sys.path[:] = paths_before

    def record(self, receipt):
        self.require_current_plugin()
        identity_state=self.state()
        notice=self.bind_plugin_identity(identity_state)
        if notice:
            # The plugin was updated mid-case: keep progress, record the new version and carry on.
            identity_state['plugin_version_notice']=notice
            self.save(identity_state)
        if not isinstance(receipt, dict):
            raise ValueError('回执必须是 JSON 对象')
        # A stable JSON is both producer input and the visible outbox. Reading
        # it again must not turn generated card/ack metadata into a new event.
        receipt = business_receipt(receipt)
        state, flow = self._execution()
        # Keep idempotent reads available; a new result cannot trigger business
        # or persistence work while the preceding reply lacks acknowledgement.
        receipt_id = receipt_digest(receipt)
        if not any(receipt_id in (r['receipt_id'], r.get('stored_receipt_id')) for r in flow.get('events', [])):
            self.require_reply_ack(state, flow)
        # Uncommitted inline native results are checked in memory. Neither the
        # product root nor its workflow state moves into the disposable view.
        self.case_store()
        previous = getattr(self, '_business_overrides', {})
        try:
            self._business_overrides = self._case_store_module.decode_business_files(receipt.get('business_files', {}), self.root)
            for relative, raw in self._business_overrides.items():
                path = (self.root / relative).resolve()
                if not path.is_relative_to(self.root):
                    raise ValueError('physical source conflict: path outside product root')
                before = receipt['business_files'][relative].get('actual_before_hash')
                if path.is_file():
                    actual = hashlib.sha256(path.read_bytes()).hexdigest()
                    if actual not in (before, hashlib.sha256(raw).hexdigest()):
                        raise ValueError('physical source conflict: ' + relative)
                elif before is not None:
                    raise ValueError('physical source conflict: previously present file is missing: ' + relative)
            proof = receipt.get('report_proof')
            if proof:
                report = (self.root / proof['path']).resolve()
                human = receipt.get('human_report')
                human = human.get('path') if isinstance(human, dict) else human
                if human and report != (self.root / human).resolve():
                    raise ValueError('report proof hash mismatch')
                self._case_store_module.verify_report_proof(receipt, self.root)
            import_paths=list(sys.path)
            try:
                sys.path.insert(0,str(self.home/'scripts'))
                from sync_s1_decision_reports import rollback_transaction
                with rollback_transaction(self.root):
                    self._case_store_module.validate_receipt_seals(receipt)
                    card=self._record(receipt)
                    if self.stage in {'S1','S2','S3','S4'}:
                        from stage_artifacts import write_receipts
                        write_receipts(self.root,{'stages':{self.stage:{'steps':self.steps}}},self.state(),self.stage)
            finally:sys.path[:]=import_paths
            return card
        finally:
            self._business_overrides = previous

    def _record(self, receipt):
        if not isinstance(receipt, dict):
            raise ValueError('回执必须是 JSON 对象')
        import stage_deferred
        step = next((s for s in self.steps if s['control']['id'] == receipt.get('step')), None)
        error = stage_deferred.receipt_error(step, receipt) if step else None
        if error:
            receipt = {**receipt, 'outcome': 'blocked', 'summary': error, 'needs_user': error}
        state, flow = self._execution()
        receipt_id = receipt_digest(receipt)
        previous = next((r for r in flow['events'] if not r.get('superseded') and receipt_id in (r['receipt_id'], r.get('stored_receipt_id'))), None)
        if previous:
            if previous.get('receipt_ref'):
                self.record_receipt(previous)
            if self.stage == 'S1' and previous['n'] == self.total and not self.decision_is_current():
                raise ValueError('决策证据已变化，旧判断失效；修订末步并重新生成报告，不能复用旧回执')
            for item in previous['artifacts'].values():
                if self.artifact_hash(item) != item['sha256']:
                    raise ValueError('已记录产物发生变化，需要修订重验，不能重复使用旧回执')
            return self.present_existing_card(previous, previous.get('card') or self._read_card(previous['card_path']))
        self.require_reply_ack(state, flow)
        matched = next((s for s in self.steps if s['control']['id'] == receipt.get('step')), None)
        if matched is None:
            raise ValueError('未知步骤 ID；只接受当前阶段路线图')
        if matched['n'] != flow['cursor'] or flow['delivery_status'] == 'complete':
            raise ValueError('步骤顺序不符；不能跳步或重复推进')
        outcome = receipt.get('outcome')
        if outcome not in matched['control']['allowed_outcomes']:
            raise ValueError('本步不支持该状态：' + str(outcome))
        if not receipt.get('summary') or not receipt.get('skills_used'):
            raise ValueError('需要本步结论及实际执行的 Skill / 脚本')
        self._decision(receipt, matched, flow)
        knowledge = None
        if outcome not in {'blocked', 'waiting_external'}:
            knowledge = self.knowledge_check(receipt, matched)
            if knowledge['status'] != 'pass' and matched['n'] != self.delivery_step:
                raise ValueError('知识库检查未通过：' + '; '.join(knowledge['errors']))
            # References remain in the receipt; raw response hashing is deferred to stage close.
            if knowledge.get('refs'):
                receipt = {**receipt, 'kb_cards': knowledge['refs']}
        if self.stage == 'S1' and matched['n'] == self.delivery_step and outcome not in {'blocked', 'waiting_external'}:
            extra = self.generate_decision(state, knowledge)
            if extra:
                receipt = {**receipt, 'artifacts': {**receipt.get('artifacts', {}), **extra}}
                # The renderer just committed a complete new seal. Never merge
                # only its model pointer into the caller's cached business_files.
                produced = json.loads(self.file(extra['decision_yaml']['path']).read_bytes().decode('utf8'))
                receipt.update({key: produced[key] for key in ('human_report', 'report_proof', 's1_stage_data',
                    'business_files', 'business_snapshot', 'restored_disk_files', 'reports') if key in produced})
                self._case_store_module.validate_receipt_seals(receipt)
                self._business_overrides = self._case_store_module.decode_business_files(receipt.get('business_files', {}), self.root)
        artifacts = self._artifacts(receipt, matched)
        if outcome not in {'blocked', 'waiting_external'} and matched['n'] != self.delivery_step:
            self._knowledge_for_check = knowledge
            try:
                result = self.business_check(receipt, matched)
            finally:
                self._knowledge_for_check = None
            if result['status'] != 'pass':
                # Report-contract gaps (format, stale facts) are the assistant's to fix. Recording them as a
                # blocked event made users reply an extra round and left the step unable to be resubmitted.
                if self.stage == 'S1' and result.get('checks', {}).get('early_report_contract', {}).get('errors'):
                    raise ValueError('本步还没提交，先修好以下缺项（助手可直接修，不需要用户确认），'
                                     '修好后用同一条 record 重交：' + '; '.join(result['errors']))
                raise ValueError('业务检查未通过：' + '; '.join(result['errors']))
        mcp = receipt.get('mcp', {})
        for name, data in mcp.items():
            if not isinstance(data, dict) or data.get('status') not in {'called', 'direct_called', 'available_not_used', 'blocked', 'not_available'}:
                raise ValueError('MCP 状态不合法：' + name)
            if data['status'] in {'called', 'direct_called'}:
                if not data.get('evidence'):
                    raise ValueError('声称调用 MCP 必须有来源证据文件：' + name)
                for evidence in data['evidence']:
                    self.file(evidence)
        event = {'n': matched['n'], 'step': receipt['step'], 'outcome': outcome,
                 'receipt_id': receipt_id, 'artifacts': artifacts, 'decision': receipt.get('decision'),
                 'summary': receipt['summary'], 'receipt': compact_receipt(receipt)}
        event['visibility_boundary'] = flow.pop('visibility_boundary', None) or visibility_boundary(self.root)
        if knowledge:
            event['knowledge'] = knowledge
        if matched['n'] == self.delivery_step and outcome not in {'blocked', 'waiting_external'}:
            # Delivery cannot be suppressed just because the business validator fails.
            for role, suffix in [('report_md', '.md'), ('report_html', '.html')]:
                if self.file(artifacts[role]['path']).suffix.lower() != suffix:
                    raise ValueError('综合报告必须提供真实 MD 和 HTML')
            if self.stage == 'S1':
                with self.case_store().materialize(overrides=getattr(self, '_business_overrides', {})) as view:
                    args = [sys.executable, '-B', str(self.validator), str(view.root)]
                    compact = (state.get('s1_decision') or {}).get('compact_receipt')
                    if compact: args += ['--receipt', str(view.map_path(self.file(compact)))]
                    checked = subprocess.run(args, capture_output=True, text=True, encoding='utf8', errors='replace',
                                             timeout=60, env={**os.environ, 'PYTHONUTF8': '1'})
                    checked.stdout = checked.stdout.replace(str(view.root), str(self.root))
                    checked.stderr = checked.stderr.replace(str(view.root), str(self.root))
                    checked.stdout = checked.stdout.replace(Path(view.root).as_posix(), self.root.as_posix())
                    checked.stderr = checked.stderr.replace(Path(view.root).as_posix(), self.root.as_posix())
                exit_code, output = checked.returncode, checked.stdout + checked.stderr
                details = {}
            else:
                self._knowledge_for_check = knowledge
                try:
                    details = self.business_check(receipt, matched)
                finally:
                    self._knowledge_for_check = None
                exit_code = 0 if details['status'] == 'pass' else 1
                output = json.dumps(details, ensure_ascii=False)
            knowledge_errors = ['知识库：' + str(error) for error in (knowledge or {}).get('errors', [])]
            if knowledge_errors:
                # Stage close always delivers the report and warning card; failed knowledge can never grant acceptance.
                errors = list(details.get('errors', [])) if details else re.findall(r'^\s*✗\s*(.+)$', output, re.M)
                if exit_code and not errors:
                    errors.append('阶段验收未通过；完整输出保留在本步回执')
                details = {**details, 'errors': list(dict.fromkeys(errors + knowledge_errors))}
                output += '\n' + '\n'.join(knowledge_errors)
                exit_code = exit_code or 1
            event['validator_exit_code'] = exit_code
            flow['validation'] = {'exit_code': exit_code, 'output': output,
                                  'knowledge_stage_seal': (knowledge or {}).get('response_hashes', []),
                                  **({'errors': details.get('errors', []), 'checks': details.get('checks', {})} if details else {}),
                                  'report_hashes': {k: v['sha256'] for k, v in artifacts.items()},
                                  'validator_sha256': hashlib.sha256(self.validator.read_bytes()).hexdigest()}
            flow['delivery_status'] = 'awaiting_card'
            acceptance_node = state.setdefault('stage_acceptance', {}).setdefault(self.stage, {})
            for key in ('count', 'blocking_count', 'issues', 'errors'):
                acceptance_node.pop(key, None)
            acceptance_node.update(status='pending', notes='综合报告已生成；业务验收与用户 go 决策单独处理，不因流程推进自动放行。')
        if outcome not in {'blocked', 'waiting_external'}:
            flow['records'].append(event)
            completed = state.setdefault('completed_steps', [])
            if receipt['step'] not in completed:
                completed.append(receipt['step'])
            if matched['n'] < self.delivery_step:
                queue = flow.get('recompute_queue')
                if queue and queue[0] == matched['n']:
                    queue.pop(0); flow['cursor'] = queue[0] if queue else matched['n'] + 1
                else: flow['cursor'] += 1
            elif flow.get('recompute_queue') == [self.delivery_step]:
                flow.pop('recompute_queue', None)
            if self.stage == 'S1' and matched['n'] == (5 if flow.get('roadmap_version') == 'r01.m2' else 6):
                state['supplier_data_status'] = outcome if outcome != 'complete' else 'confirmed'
            self._position(state)
        state['blocked_reason'] = receipt.get('needs_user') if outcome == 'blocked' else None
        self.update_deferred_acceptance(state, flow, receipt, matched)
        acceptance = self.ui_modules['stage_acceptance_summary'].acceptance_summary(self.root, state, self.stage)
        card = self.card(receipt, matched, flow, acceptance=acceptance)
        card_path = self.output / 'receipts' / f"{matched['n']:02}.json"
        machine_fields = {'本步 Skill': receipt.get('skills_called',receipt.get('skills_used',[])),
                          '本步 MCP': receipt.get('mcp_calls',[]), '模型建议': self.model(matched),
                          '下步模型': self.model(self.steps[matched['n']]) if matched['n'] < self.total else None,
                          '本步结论': receipt['summary'], '已生成文件': receipt.get('artifacts',{}),
                          '下一步': matched['n']+1 if matched['n'] < self.total else None,
                          '需要你做': receipt.get('needs_user')}
        previous_data = json.loads(card_path.read_text(encoding='utf8')) if card_path.exists() else {}
        previous_data.update(receipt)
        self._case_store_module.validate_receipt_seals(previous_data)
        decision = receipt.get('decision') or {}
        raw_ref = None
        if previous_data.get('business_files'):
            if decision.get('source') == 'user':
                previous_data = self._case_store_module.seal_receipt_decision(previous_data, receipt_id, self.root)
                selector = '/business_decisions/' + receipt_id
                bound = previous_data['business_decisions'][receipt_id]
                raw_ref = {'kind': 'decision', 'status': 'versioned'}
            else:
                selector = '/business_files'; bound = previous_data['business_files']
                snapshot = previous_data['business_snapshot']
                raw_ref = {'kind': 'step_final', 'status': 'current', 'revision': snapshot['revision'],
                           'case_sequence': snapshot.get('case_sequence')}
            raw_ref.update(path=card_path.relative_to(self.root).as_posix(), selector=selector, sha256=canonical_hash(bound))
        small = compact_receipt(receipt)
        versions = previous_data.setdefault('receipt_versions', {})
        version = {'receipt': small, 'raw_ref': raw_ref}
        store_receipt_version(versions, receipt_id, version, flow.get('events', []))
        event['receipt'] = small
        event['receipt_ref'] = {'path': card_path.relative_to(self.root).as_posix(),
                                'selector': '/receipt_versions/' + receipt_id + '/receipt', 'sha256': canonical_hash(small)}
        if raw_ref: event['raw_ref'] = raw_ref
        for older in flow['events']:
            old_ref = older.get('raw_ref') or {}
            if (old_ref.get('kind') == 'step_final' and old_ref.get('path') == event['receipt_ref']['path']
                    and raw_ref and old_ref.get('sha256') != raw_ref.get('sha256')):
                old_ref['status'] = 'superseded'
        previous_data.update(schema_version=3, receipt=small, machine_fields=machine_fields, card=card,
                             knowledge=knowledge)
        event['stored_receipt_id'] = receipt_digest(business_receipt(previous_data))
        if matched['n'] == self.delivery_step and flow.get('validation'):
            previous_data['validation'] = flow['validation']
        seal = evidence_windows.seal_event(flow, event)
        previous_data.setdefault('evidence_windows', {})[seal['window_id']] = seal
        if not getattr(self, '_recovering', False):
            evidence_windows.advance_window(flow, event)
        atomic_write(card_path, json.dumps(previous_data,ensure_ascii=False,indent=2))
        event['card_path'] = card_path.relative_to(self.root).as_posix()
        event['card'] = card
        flow['events'].append(event)
        flow['pending_card'] = event['card_path']
        self.save(state)
        self._write_visible_cards()
        return card

    def card(self, receipt, step, flow, *, acceptance=None):
        before=list(sys.path)
        try:
            sys.path.insert(0,str(self.home/'scripts'))
            return self._card(receipt,step,flow,acceptance=acceptance)
        finally:sys.path[:]=before

    def _card(self, receipt, step, flow, *, acceptance=None):
        render_card = self.ui_modules['chat_card_v3'].render_card
        n = step['n']
        next_n = (flow.get('recompute_queue') or [n + 1])[0]
        following = self.steps[next_n - 1] if n < self.total and next_n <= self.total else None
        next_text = (f"{next_n}/{self.total} {self.name(following, flow['path'])}"
                     if following else '本阶段结束' if self.s1_only() or self.stage == 'S5'
                     else self.next_stage() if hasattr(self, 'next_stage') else '本阶段结束')
        if self.stage == 'S3' and n == self.delivery_step:
            following = None
            next_text = 'S4；有需要可做品牌故事 / 旗舰店'
        if receipt.get('outcome') == 'blocked':
            following = None
            next_text = f'补齐本步缺项后重试 {n}/{self.total}'
        if receipt.get('outcome') == 'waiting_external':
            following = None
            next_text = step.get('pause') or '放回回填文件夹后回复「已回填」'
        current = self.model_display.task(self.model(step))
        next_model = self.model_display.task(self.model(following)) if following else ''
        human_report = receipt.get('human_report')
        if human_report:
            value = human_report.get('path') if isinstance(human_report, dict) else human_report
            path = (self.root / value).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise ValueError('本步完整报告缺失或越界：' + str(value))
            visible = {'human_report': value}
            if n == self.delivery_step:
                visible.update({role: item for role, item in receipt.get('artifacts', {}).items()
                                if role == 'report_html' and (self.root / (item.get('path') if isinstance(item, dict) else item)).is_file()})
        else:
            visible = {role: item for role, item in receipt.get('artifacts', {}).items()
                       if (self.root / (item.get('path') if isinstance(item, dict) else item)).is_file()}
        shown = {**receipt, 'artifacts': visible}
        if self.stage == 'S1' and n in (6,7):
            try:
                from s1_cost_present import card_fields
                cost = yaml.safe_load(self.read_bytes('S1_选品板块/amazon-market-research/05_cost_model.yaml')) or {}
                shown.update(card_fields(cost.get('unified_cost') or {}, n))
            except (OSError,ValueError,TypeError,AttributeError):
                pass
        if self.stage == 'S1' and n == self.delivery_step:
            try:
                from s1_stage_report import load
                from s1_report_v2 import decision_headline
                model = load(self.root/'S1_选品板块/_阶段总览/s1_stage_report.json')
                decision = (model.get('stage_model',{}).get('report_v2') or {}).get('decision_summary')
                if decision:shown['summary']='研究结论：'+decision_headline(decision)
            except (OSError,ValueError,TypeError,AttributeError):
                pass
        from r01_case_store import step_report_catalog
        stable = self.output / 'receipts' / f'{n:02}.json'
        if stable.is_file():
            catalog_record = {'receipt_ref': {'path': stable.relative_to(self.root).as_posix()}}
            catalog = step_report_catalog(self.root, catalog_record)
        else:
            catalog = step_report_catalog(self.root, receipt)
        if catalog:
            shown['reports'] = catalog
        rendered = render_card(shown, stage=self.stage, number=n, total=self.total,
                           title=self.name(step, flow['path']), model=current,
                           next_model=next_model, next_text=next_text, root=self.root, delivery_step=self.delivery_step,
                           acceptance=(acceptance if acceptance is not None else self.acceptance_summary()) if n == self.delivery_step else None)
        if self.stage == 'S3' and flow.get('roadmap_version') == 'r03.s3':
            if n in (6,8):
                from nav_float import visual_channels
                channels=visual_channels(self.home,self.state())
                if any(item['id']=='C' for item in channels):
                    rendered+='\n出图渠道：'+' / '.join(item['id']+' '+item['label'] for item in channels)+'\n'
            if n == self.delivery_step:
                rendered+='\n建议补进 Q&A 的用法问题见报告；品牌故事 / 旗舰店有需要可再做。\n'
        if self.stage == 'S1' and n == 1 and flow.get('roadmap_version') == 'r01.m2':
            rows = rendered.splitlines()
            folder = f'📁 产品文件夹：[{self.root.name}](<{self.root}>)'
            if len(rows) < 10: rows.insert(2, folder)
            else:
                index = next((i for i, line in enumerate(rows) if line.startswith('📦')), 1)
                rows[index] += ' · ' + folder
            rendered = '\n'.join(rows) + '\n'
        if self.stage in ('S1', 'S2', 'S3', 'S4') and n == self.delivery_step:
            summary = acceptance if acceptance is not None else self.acceptance_summary()
            if summary.get('accepted') and self.stage == 'S1' and self.s1_only():
                rendered += '\n本轮范围只到 S1；要继续就回「继续 S2」，我生成交接口令，你新开窗口粘贴。\n'
            elif not summary.get('accepted'):
                rendered += '\n两条路：① 补齐缺项后重新验收；② ' + self.continue_s2_hint() + '\n'
        return rendered

    def ip_red(self):
        """True while the S1 compliance ruler still shows a red light (hard block for S2)."""
        ruler_path = self.root / 'S1_选品板块/amazon-ip-risk-assessment/04A_compliance_ruler.yaml'
        ruler = yaml.safe_load(ruler_path.read_bytes()) if ruler_path.is_file() else {}
        cards = (ruler or {}).get('summary_card') or []
        cards = cards.values() if isinstance(cards, dict) else cards
        return any(row.get('light') == 'red' for row in cards if isinstance(row, dict))

    def continue_s2_hint(self):
        """The way forward from a delivered stage that did not pass acceptance (S1–S4)."""
        target = 'S' + str(int(self.stage[1:]) + 1)
        if self.stage == 'S1' and self.ip_red():
            return '侵权红灯未解除，暂时不能进 S2；先处理 IP 风险。'
        hold = 'S1 的 Hold 和待核项' if self.stage == 'S1' else f'{self.stage} 的待核项'
        extra = '（S2 合规扫描未通过时不能带过去，要先整改）' if self.stage == 'S2' else ''
        return (f'要先进 {target} 也可以：回「继续 {target}」，我生成带缺口的交接口令，你新开窗口粘贴；'
                f'{hold}原样带过去{extra}，不代表采购、上架或广告放行。')

    def update_deferred_acceptance(self, state, flow, receipt, step):
        """StageFlow applies the shared policy before generating the visible card."""

    def acceptance_summary(self):
        acceptance_summary = self.ui_modules['stage_acceptance_summary'].acceptance_summary
        return acceptance_summary(self.root, self.state(), self.stage)

    def _read_card(self, path):
        path = self.file(path)
        if path.suffix == '.json':
            return json.loads(path.read_text(encoding='utf8'))['card']
        return path.read_text(encoding='utf8')

    def visible_cards_path(self):
        return self.output / (self.stage + '_VISIBLE_CARDS.md')

    def present_existing_card(self, event, text):
        repeated = bool((event or {}).get('reply_ack'))
        if not repeated and (event or {}).get('visibility_boundary'):
            try:
                visible_card(self.root, text, event['visibility_boundary'])
                repeated = True
            except ValueError:
                pass
        # A suffix preserves the shared host's first-block exact card check.
        return text.rstrip() + ('\n补发：本步卡已正式展示，本次仅恢复查看。\n' if repeated else '\n')

    def visible_cards(self):
        state, flow = self._execution()
        pending = self.pending_reply_card(state, flow)
        if pending:
            event = next((r for r in reversed(flow.get('events', [])) if self.current_card(r.get('card', ''), state).strip() == pending.strip()), None)
            return self.present_existing_card(event, pending)
        path = flow.get('pending_card')
        records = flow.get('records') or []
        if not path and records:
            path = records[-1].get('card_path')
        if not path:
            return f'【{self.stage}】还没有已落盘的步骤卡。'
        text = self._read_card(path).strip()
        event = next((r for r in reversed(flow.get('events', [])) if r.get('card_path') == path), None)
        if event and event.get('card'):
            text = event['card'].strip()
        if self.stage == 'S1' and state.get('s1_decision') and not self.decision_is_current():
            text = '决策证据已变化，旧拿样判断失效；须重新生成决策报告。\n\n' + text
        step_n = records[-1]['n'] if records else 1
        header = (f'### 🧭 {self.stage} · 当前步骤卡｜停在第 {step_n} / 共 {self.total} 步\n'
                  f'> 最终回复只贴下面这一张：总结本步、接下一步。历史步骤卡已经出过，禁止整段重贴。')
        return self.present_existing_card(event, text)

    def _write_visible_cards(self):
        try:
            self.refresh_nav_dashboard()
        except Exception:
            pass

    @staticmethod
    def _short_name(name):
        text = re.sub(r'\s+', '', str(name or ''))
        text = text.split('→')[0].strip() or text
        return text[:6] or '—'

    @staticmethod
    def _short_skill(skill):
        text = str(skill or '')
        if '→' in text:
            text = text.split('→')[-1]
        text = re.split(r'[（(\s]', text.strip())[0].strip() or text.strip()
        if 'starsky-fullchain-workflow' in text:
            return '总控'
        text = re.sub(r'^amazon-', '', text)
        return text[:18] or '—'

    def nav_bar(self):
        state = self.state()
        if self.state_key not in state:
            return f'🧭【{self.stage}】尚未初始化，先 init 再导航。回“导航”看整表。'
        flow = state[self.state_key]
        cursor = flow.get('cursor', 1)
        try:
            cursor = int(cursor)
        except (TypeError, ValueError):
            cursor = 1
        cursor = max(1, min(cursor, self.total))
        path = flow.get('path', 'condition_first')
        done = {r.get('n') for r in flow.get('records', []) or [] if isinstance(r.get('n'), int)}
        current = self.steps[cursor - 1]
        cells = '●' * len(done & set(range(1, self.total + 1))) + '○' * (self.total - len(done & set(range(1, self.total + 1))))
        cur_text = f"▶{cursor}{self._short_name(self.name(current, path))}({self._short_skill(current.get('skill', ''))}|{'·'.join(current.get('mcp', []) or []) or '无'})"
        if cursor >= self.total:
            acceptance_label = self.ui_modules['stage_acceptance_summary'].acceptance_label
            acceptance = self.acceptance_summary()
            if not acceptance['accepted']:
                next_text = '→⚠️ ' + acceptance_label(acceptance) + '，补齐数据'
                if len(done) >= self.total:
                    cells = '●' * max(0, self.total - 1) + '⚠'
            elif self.stage == 'S1' and self.s1_only():
                next_text = '→仅 S1 本阶段结束'
            elif self.stage == 'S5':
                next_text = '→本轮巡检结束'
            else:
                next_text = '→复制已验收交接口令换窗'
        else:
            following = self.steps[cursor]
            warn = '⚠️' if str(following.get('pause', '') or '').strip() else ''
            next_text = f"→{cursor + 1}{self._short_name(self.name(following, path))}{warn}"
        bar = f'🧭【{self.stage} {cursor}/{self.total}】{cells} {cur_text} {next_text}｜回“导航”看全表'
        return bar[:140]

    def nav_table(self):
        state, flow = self._execution()
        acceptance_label = self.ui_modules['stage_acceptance_summary'].acceptance_label
        acceptance = self.acceptance_summary()
        only_s1 = self.stage == 'S1' and self.s1_only()
        path = flow.get('path', 'condition_first')
        done = {r.get('n') for r in flow.get('records', []) or [] if isinstance(r.get('n'), int)}
        rows = [f"### 🧭 {self.stage} · 路线图 · {path}｜共 {self.total} 步，当前第 {flow['cursor']} 步",
                f"> 进度 {flow['cursor']}/{self.total} ｜ ▶=当前 ✓=已完成 ｜ 回“第N步做什么”看详情",
                '',
                '| # | 步骤 | 做什么 | Skill | MCP | 停顿 |', '|---|---|---|---|---|---|']
        for step in self.steps:
            n = step['n']
            flag = '▶' if n == flow['cursor'] else ('✓' if n in done else '')
            what = str(step.get('supply_first' if path == 'supply_first' and step.get('supply_first') else 'condition_first' if path == 'condition_first' and step.get('condition_first') else 'name') or '')[:20]
            pause = '⚠️ ' + str(step.get('pause', ''))[:18] if str(step.get('pause', '') or '').strip() else ''
            if n == self.delivery_step and not acceptance['accepted']:
                what, pause = '交付报告并完成验收', '⚠️ ' + acceptance_label(acceptance) + '，补齐数据'
                if n in done:
                    flag = '⚠'
            elif only_s1 and n == self.delivery_step:
                what, pause = '交付报告与拿样研究判断', '仅 S1，本阶段结束'
            rows.append(f"| {flag}{n} | {self.name(step, path)} | {what} | {step.get('skill', '')} | {' / '.join(step.get('mcp', []) or []) or '—'} | {pause} |")
        target = self.next_stage()
        if not acceptance['accepted']:
            rows.append('⚠️ ' + acceptance_label(acceptance) + '；补齐数据后重新验收。')
        elif target and not only_s1:
            rows.append(f"本阶段已验收，复制 _NEXT_{self.stage}到{target}.md 口令，在同一个产品项目里新开会话进入 {target}。")
        elif self.stage == 'S1':
            rows.append('仅 S1：交付报告与拿样研究判断；采购授权独立，本轮结束。')
        return '\n'.join(rows)

    def nav_step(self, number):
        try:
            number = int(number)
        except (TypeError, ValueError):
            raise ValueError('需要 1–%d 的步骤编号' % self.total)
        if number < 1 or number > self.total:
            raise ValueError('需要 1–%d 的步骤编号' % self.total)
        state = self.state()
        path = (state.get(self.state_key, {}) or {}).get('path', 'condition_first')
        step = self.steps[number - 1]
        following = self.steps[number] if number < self.total else None
        acceptance = self.acceptance_summary()
        only_s1 = self.stage == 'S1' and self.s1_only()
        next_model = self.next_model_instruction(following) if following else (
            self.next_model_instruction() if acceptance['accepted'] and self.next_stage() and not only_s1 else '')
        pause = str(step.get('pause', '') or '').strip() or '无，我自动进下一步'
        what = str(step.get('supply_first' if path == 'supply_first' and step.get('supply_first') else 'condition_first' if path == 'condition_first' and step.get('condition_first') else 'name') or self.name(step, path))
        if number == self.total and not acceptance['accepted']:
            acceptance_label = self.ui_modules['stage_acceptance_summary'].acceptance_label
            what, pause = '交付报告并完成验收', acceptance_label(acceptance) + '；补齐数据后重新验收。'
        elif only_s1 and number == self.total:
            what, pause = '交付报告与拿样研究判断', '仅 S1，本阶段结束；采购授权独立。'
        if state.get('blocked_reason') and (state.get(self.state_key) or {}).get('cursor') == number:
            pause = str(state['blocked_reason']) + ('；补齐数据后重新验收。' if number == self.total and not acceptance['accepted'] else '')
        artifacts = ';'.join(str(item) for item in step.get('artifacts', []) or []
                             if not ('_NEXT_' in str(item) and (not acceptance['accepted'] or only_s1))) or '—'
        mcp = ' / '.join(step.get('mcp', []) or []) or '无需外部 MCP'
        if step.get('mcp_optional'):
            mcp += '（可选：' + ' / '.join(step['mcp_optional']) + '）'
        return '\n'.join([
            f"### 🧭 【{self.stage} · 第 {number} 步 / 共 {self.total} 步】· {self.name(step, path)}",
            f"> 🛠 {str(step.get('skill', ''))[:40]} ｜ 🔌 {mcp[:40]} ｜ 🤖 {self.model_display.task(self.model(step))}",
            '',
            '| 分项 | 内容 |',
            '|---|---|',
            f"| 📌 做什么 | {what} |",
            f"| 🛠 调用 Skill | {str(step.get('skill', ''))} |",
            f"| 🔌 调用 MCP | {mcp} |",
            f"| 🤖 模型建议 | {self.model_display.task(self.model(step), '官方双模型手动切；单一模型忽略')} |",
            *([f"| 🧭 下步模型 | {next_model} |"] if next_model else []),
            f"| 📁 产物落哪 | {artifacts} |",
            f"| 🙋 需要你做 | {pause} |",
            ''])

    def refresh_nav_dashboard(self):
        script = self.home / 'scripts/render_nav_dashboard.py'
        if not script.is_file():
            return ''
        checked = subprocess.run([sys.executable, '-B', str(script), '--root', str(self.root),
                                  '--home', str(self.home), '--stage', self.stage],
                                 capture_output=True, text=True, encoding='utf8', errors='replace',
                                 timeout=60, env={**os.environ, 'PYTHONUTF8': '1'})
        if checked.returncode != 0:
            return ''
        try:
            return json.loads(checked.stdout or '{}').get('html', '')
        except ValueError:
            return ''

    def ack(self, reply):
        state, flow = self._execution()
        if flow.get('continuous_run', {}).get('cards'):
            return self.settle_continuous(state, flow, force=True)
        card = self.pending_reply_card(state, flow) if not flow.get('pending_card') else None
        # The user asked to keep going this turn: an ack now would demand a final reply first and stall every step.
        # Defer the card into the run; the next user turn checks every deferred card was shown in order.
        batch = getattr(self, 'continue_in_batch', None)
        if flow.get('pending_card') and batch and batch(state, flow):
            return '你要求连续执行：本步卡已并入本轮连续跑，直接 begin 下一步；本轮最后一条正式回复按顺序贴出全部步骤卡。'
        pending = flow['pending_card']
        if not pending:
            return '没有待核对步骤卡。'
        card = card or self._read_card(pending)
        event = next((r for r in reversed(flow.get('events', [])) if r.get('card_path') == pending), None)
        if event and event.get('card'):
            card = event['card']
        normalized = reply.replace('\r\n', '\n')
        # The card as generated, or the same card with a renamed folder's current path (R06.1 AS-03).
        current = card
        for alias in (state or {}).get('root_aliases') or []:
            if isinstance(alias, str) and alias and alias != str(self.root):
                current = current.replace(alias, str(self.root))
        card = next((variant for variant in dict.fromkeys((card, current))
                     if normalized.lstrip().startswith(variant.strip())), card)
        if not normalized.lstrip().startswith(card.strip()):
            raise ValueError('步骤卡必须是回复的第一块内容；请完整输出当前步、模型、下一步、报告与动作')
        legacy = Path(pending).suffix == '.md' and all(token in card for token in ('【本步结论】','【模型建议】','【下一步】'))
        if '旧任务恢复卡' in card or legacy:
            pass
        elif not all(token in card for token in ('🎯','🤖','👉')):
            raise ValueError('最终回复必须留住带进度条的步骤卡，不能只用散文摘要')
        acknowledged = event if event is not None else flow
        proof = self.verify_visible_reply(state, acknowledged, card)
        acknowledged['reply_ack'] = dict(proof, receipt_id=event['receipt_id'] if event else None,
                                       card_sha256=hashlib.sha256(card.encode('utf8')).hexdigest())
        flow['pending_card'] = None
        flow['reply_evidence'] = proof['evidence']
        if flow['delivery_status'] == 'awaiting_card':
            flow['delivery_status'] = 'complete'
            state['next_step'] = {'step_id': 's1_followup', 'instruction': '报告已交付；有后补资料时更新第 6–9 步。未经授权不进入 S2。'}
        self.save(state)
        return '已从宿主会话核对本步独立步骤卡；接着运行 begin 开始下一步。'

    def verify_visible_reply(self, state, acknowledged, card):
        boundary = acknowledged.get('visibility_boundary')
        if not boundary:
            acknowledged['visibility_boundary'] = visibility_boundary(self.root)
            self.save(state)
            raise ValueError('本步缺少会话起点，请先补发步骤卡，再核对对话证据')
        try:
            proof = visible_card(self.root, card, boundary)
        except ValueError as exc:
            if '宿主或会话已变化' in str(exc) or boundary.get('kind') == 'unavailable':
                acknowledged['visibility_boundary'] = visibility_boundary(self.root)
                self.save(state)
            raise
        return proof

    def ack_file(self, path):
        path = Path(path).resolve()
        if path.is_relative_to(self.root) and {'receipts', '运行数据'} & set(path.relative_to(self.root).parts):
            raise ValueError('回执或运行数据文件不能充当已展示的对话；请从宿主会话取证')
        return self.ack(path.read_text(encoding='utf-8-sig'))

    def ack_current(self):
        state, flow = self._execution()
        if flow.get('continuous_run', {}).get('cards'):
            return self.settle_continuous(state, flow, force=True)
        return self.ack(self.pending_reply_card(state, flow) or '')

    def steps_in_this_window(self, flow):
        """How many current (not superseded) steps were recorded in the latest host session."""
        events = [e for e in flow.get('events') or [] if not e.get('superseded')]
        sessions = [(e.get('visibility_boundary') or {}).get('session_id') for e in events]
        last = sessions[-1] if sessions else None
        count = 0
        for session in reversed(sessions):
            if not last or session != last:
                break
            count += 1
        return count

    def step_pack(self):
        """本步工作包（R06.1）：本步要调的数据源与知识库（参数已算好）、本步做法、停下条件和收尾命令。
        助手每步只读这一块，不再自己翻五六份说明和插件源码找本步规则（3.0.23 实测每步因此多花 1～2 分钟）。"""
        state, flow = self._execution()
        step = self.steps[flow['cursor'] - 1]
        n, path = step['n'], flow.get('path', 'condition_first')
        py, scripts, root = sys.executable, self.home / 'scripts', self.root
        lines = [f"【{self.stage} · 第 {n}/{self.total} 步：{self.name(step, path)} · 本步工作包】",
                 f'产品根：{root}',
                 '只看本包和这个产品根；不读其他案例、旧会话记录和插件源码。报错就按报错文字补。',
                 # S1-R14: a host refusal once ended the turn with an empty message
                 '任何命令被拒（含宿主权限 / 自动审批拒绝）都不能空着结束：一句话告诉用户被拒的是什么、为什么、需要用户做什么。']
        same = self.steps_in_this_window(flow)
        if same >= WINDOW_STEPS_HINT:
            # S1-R21: the 3.0.28 run did all nine steps in one window and reached ~440k tokens
            lines.append(f'本窗口已连续登记 {same} 步，上下文多半已超过约 30 万 token：本步卡“需要你做”里加一句'
                         '“可以换新窗口续跑：新窗口里说「继续」，会先补发上一步卡再接着做”。')
        stop = step.get(path) or step.get('stop_condition') or step.get('pause')
        if stop:
            # 3.0.23: step 5 was blocked three rounds because this rule read like "wait for the factory".
            lines.insert(1, '放行规则（先读这一行，只有这里写的情况才停下等用户）：' + stop)
        if step.get('pause'):
            # 3.0.23 run 5/6: step 2 read controller source 20+ times to learn how a step that waits for the user is recorded.
            lines.append('本步要用户拍板：研究做完先列出编号选项和推荐，结束本轮等用户回复，此时不要 record；'
                         '收到回复后再 draft / record，decision.message 和 user_reply 都写用户原话，decision.selection 写选中的编号。'
                         '用户只回"继续"不算选择，请他回编号。')
        lines.append('模型建议：' + self.model_display.task(self.model(step)))
        sources = ' / '.join(step.get('mcp') or []) or '无'
        if step.get('mcp_optional'):
            sources += '（可选：' + ' / '.join(step['mcp_optional']) + '）'
        lines.append('数据源：' + sources + ('；工具：' + ' / '.join(step['tools']) if step.get('tools') else ''))
        sys.path.insert(0, str(scripts))
        try:
            import kb_plan
            plan = kb_plan.plan_step(root, self.home, self.stage, n)
        except (ImportError, OSError, KeyError, ValueError) as error:
            plan = {'calls': [], 'warnings': ['知识库计划生成失败：' + str(error)]}
        finally:
            sys.path.pop(0)
        if plan['calls']:
            lines.append('知识库（按顺序调用，参数照抄）：')
            lines += [f"  {i}. {c['server']}.{c['tool']} {json.dumps(c['args'], ensure_ascii=False)}" for i, c in enumerate(plan['calls'], 1)]
        lines += ['  ⚠ ' + w for w in plan.get('warnings') or []]
        guide = step_guide(self.home, n) if self.stage == 'S1' else ''
        if guide:
            lines += ['本步做法：', guide]
        if self.stage == 'S1':
            # S1-R1/R2/R3: one 3.0.28 step wrote 86 throwaway scripts, grepped workflow_state 130 times and copied an old case
            roles = (step.get('control') or {}).get('required_artifacts') or []
            if roles:
                lines.append('本步要交的产物（record 的 artifacts 每个角色一条路径，路径以原生命令输出为准，draft 会预填能找到的）：'
                             + '、'.join(roles))
            lines.append('数据整理（竞品表、评论归类、价格 / 评论分布、市场数字）一律用 s1_market_facts.py / s1_market_report.py 等原生脚本出，'
                         '不现写小脚本读插件产物、不翻 workflow_state.yaml 猜格式、不拿别的案例当模板；原生脚本缺功能就照实告诉用户。')
        closing = []
        if self.stage == 'S1':
            closing.append(f'"{py}" "{scripts / "capture_mcp.py"}" --root "{root}" --step {n}    # 本步全部数据源回包自动入档，并生成知识库登记草稿；problems 里每类原因单独写明，照它说的办（Codex code mode 只认“text(JSON.stringify(r))”原样打印写法）')
        if plan['calls']:
            closing.append(f'在草稿里只填 card_ids / usage，再运行 "{py}" "{scripts / "save_kb_evidence.py"}" --root "{root}" --stage {self.stage} --step {n} --calls <草稿>')
        closing += [f'"{py}" "{scripts / "workflow_flow.py"}" "{root}" draft --stage {self.stage} --receipt <临时文件.json>    # 回执草稿，只补 summary / decision / 产物路径 / tools_run',
                    f'record --stage {self.stage} --receipt 同一文件 → cards → 把 cards 输出的卡**原样**复制为本轮 final 正文第一块，一个字都不改、不缩写、不重写结论（改了字核对不通过，下一轮就得重发一遍）→ 下一轮直接 begin（自动核对上一张卡，不用单独 ack）'
                    + ('；本轮是连续跑：record 后直接 begin 下一步，本轮 final 按顺序原样贴出本轮每张卡' if flow.get('continuous_run') else '')]
        closing.append('record 被拒时：报错里写了“助手可直接修”的，修好后用同一文件重交，不要改状态文件、不要另写报告；'
                       '写了要用户决定的，用一句话告诉用户原因和选项再停')
        lines.append('收尾（按顺序）：')
        lines += [f'  {i}. {c}' for i, c in enumerate(closing, 1)]
        return '\n'.join(lines)

    def receipt_draft(self):
        """本步收尾回执草稿（R06.1）：知识库登记、本步新存的 MCP 回包、Skill 名、必需产物名都预先填好。
        助手只补 summary、decision 和值为 null 的产物路径，再 record；不用读控制器源码猜格式
        （3.0.23 实测：第 1 步光拼回执就翻了十几处源码，花掉两分多钟）。"""
        state, flow = self._execution()
        step = self.steps[flow['cursor'] - 1]
        n = step['n']
        window_notice = None
        try:
            window = evidence_windows.current_window(self.root, self.stage)
        except evidence_windows.WindowUnverifiable as error:
            window, window_notice = None, str(error)
        mcp, calls, cards = {}, {}, []

        def note(server, tool, count, ok, evidence):
            row = calls.setdefault((server, tool), {'server': server, 'tool': tool, 'count': 0, 'ok': True, 'error': ''})
            row['count'] += count
            row['ok'] = row['ok'] and ok
            entry = mcp.setdefault(server, {'status': 'called', 'evidence': []})
            entry['evidence'] += [e for e in evidence if e not in entry['evidence']]

        sys.path.insert(0, str(self.home / 'scripts'))
        try:
            import kb_plan
            relative = kb_plan.evidence_path(self.stage, n, self.root)
        finally:
            sys.path.pop(0)
        evidence = self.root / relative
        if evidence.is_file():
            document = yaml.safe_load(evidence.read_text(encoding='utf-8-sig')) or {}
            raw = evidence.with_name('mcp_responses.json')
            paths = [relative] + ([raw.relative_to(self.root).as_posix()] if raw.is_file() else [])
            for row in document.get('calls') or []:
                note(row['server'], row['tool'], 1, row.get('status') == 'ok', paths)
                for card in row.get('card_ids') or []:
                    ref = {'server': row['server'], 'library': row.get('library', ''), 'card_id': card}
                    if ref not in cards:
                        cards.append(ref)
        folder = self.root / f"{self.stage}_选品板块/amazon-market-research/运行数据" if self.stage == 'S1' else None
        for path in sorted(folder.glob('mcp_calls__*.json')) if folder and folder.is_dir() else []:
            try:
                saved = json.loads(path.read_text(encoding='utf-8')).get('calls') or {}
            except ValueError:
                continue
            for call in saved.values():
                stamp = (call.get('evidence') or {}).get('time_ns')
                if stamp is None:
                    try:
                        parsed = datetime.fromisoformat(str(call.get('saved_at')))
                        stamp = int(parsed.timestamp() * 1_000_000_000) if parsed.tzinfo else None
                    except (ValueError, OverflowError):
                        stamp = None
                if window is None or not evidence_windows.contains(window, {'time_ns': stamp}):
                    continue
                parts = str(call.get('tool', '')).split('__')
                if len(parts) == 3:
                    note(parts[1], parts[2], 1, True, [path.relative_to(self.root).as_posix()])
        known = {p.name for p in (self.home / 'skills').iterdir() if p.is_dir()} if (self.home / 'skills').is_dir() else set()
        skills = [s for s in dict.fromkeys(re.findall(r'[a-z0-9][a-z0-9-]+', step.get('skill', ''))) if s in known]
        draft = {'step': step['control']['id'], 'outcome': 'complete', 'summary': None,
                 'skills_used': skills, 'skills_called': skills,
                 'mcp': mcp, 'mcp_calls': list(calls.values()), 'kb_cards': cards, 'tools_run': [],
                 'artifacts': {role: None for role in step['control']['required_artifacts']},
                 'decision': {'source': 'user', 'message': None}}
        if window_notice:
            draft['capture_notice'] = window_notice
        # 重开过的步骤（R26）：从上一版回执预填结论、产物和跑过的脚本；草稿可以改，正式回执不能改
        previous = self.previous_step_receipt(n)
        if previous:
            draft['summary'] = previous.get('summary') or None
            draft['tools_run'] = list(previous.get('tools_run') or [])
            for role, item in (previous.get('artifacts') or {}).items():
                path = item.get('path') if isinstance(item, dict) else item
                if role in draft['artifacts'] and path and (self.root / str(path)).is_file():
                    draft['artifacts'][role] = path
        return draft

    def previous_step_receipt(self, number):
        """本步被 reopen 前最后一版正式回执；没重开过就是 None。"""
        _state, flow = self._execution()
        for revision in reversed(flow.get('revisions') or []):
            for record in revision.get('previous_records') or []:
                if record.get('n') == number:
                    try:
                        receipt = self.record_receipt(record)
                    except (OSError, ValueError, KeyError):
                        receipt = None
                    return receipt or record.get('receipt') or {k: record.get(k) for k in ('summary', 'artifacts')}
        return None

    def status(self, roadmap=False):
        state = self.state()
        if self.state_key not in state:
            return '旧任务恢复：保留 workflow_state.yaml 与现有报告，按 templates/s1_execution.md 核实实际产物后恢复；新任务用 init。'
        state, flow = self._execution()
        pending = self.pending_reply_card(state, flow)
        if pending:
            events = flow.get('events') or flow.get('records') or []
            number = events[-1].get('n', flow['cursor']) if events else flow['cursor']
            return f'第 {number} 步卡片尚未在对话中出现，请先补发\n\n' + pending.strip() + '\n\n先展示本卡并 ack；核对前不能开始下一步业务。换了新窗口也要在本窗口原样补发一次再继续；用户在一条消息里要“连续跑”时，可以一轮做完多步、最后按顺序贴出每张卡。' + ('\n\n'+state['plugin_version_notice'] if state.get('plugin_version_notice') else '')
        rows = []
        if roadmap:
            rows = [f"【{self.stage} · 路线图 · {flow['path']}】共 {self.total} 步，当前第 {flow['cursor']} 步",
                    '| 步骤 | Skill | MCP | 模型建议 |', '|---|---|---|---|']
            rows += [f"| {'▶ ' if s['n'] == flow['cursor'] else ''}{s['n']} {self.name(s, flow['path'])} | {s['skill']} | {' / '.join(s['mcp']) or '无'} | {self.model_display.task(self.model(s))} |" for s in self.steps]
        if flow['pending_card']:
            rows.append('上次中断时待展示的步骤卡：\n' + self.visible_cards())
        elif flow['delivery_status'] in {'complete', 'awaiting_card'}:
            last = next(r for r in flow['records'] if r['n'] == self.total)
            rows.append(self.visible_cards())
        else:
            step = self.steps[flow['cursor'] - 1]
            following = self.steps[flow['cursor']] if flow['cursor'] < self.total else None
            current_model = self.model_display.task(self.model(step))
            model_line = '🤖 模型：本步 ' + current_model
            rows.append(f"🧭 **{self.stage} · {step['n']}/{self.total} 待执行：{self.name(step, flow['path'])}**")
            hint = self.reuse_hint(flow) if hasattr(self, 'reuse_hint') and flow.get('revisions') else ''
            if hint:
                rows.append('♻️ ' + hint)
            if following:
                next_model = self.model_display.task(self.model(following))
                hint = '下一步沿用' if next_model == current_model else '下一步建议调到 ' + next_model
                rows += [model_line + ' ｜ ' + hint,
                         f"👉 下一步：{following['n']}/{self.total} {self.name(following, flow['path'])}"]
            else:
                acceptance = self.acceptance_summary()
                rows.append(model_line)
                if not acceptance['accepted']:
                    acceptance_label = self.ui_modules['stage_acceptance_summary'].acceptance_label
                    rows.append('⚠️ ' + acceptance_label(acceptance) + '；补齐数据后重新验收。')
                elif (self.stage == 'S1' and self.s1_only()) or self.stage == 'S5':
                    rows.append('👉 下一步：本阶段结束')
                else:
                    rows.append('👉 下一步：复制已验收交接口令进入 ' + str(self.next_stage()))
            if state.get('blocked_reason'):
                rows.append('🙋 你要做：' + str(state['blocked_reason']))
        if state.get('plugin_version_notice'):
            rows.append(state['plugin_version_notice'])
        return '\n'.join(rows)

    def can_handoff(self):
        if self.stage == 'S1' and (self.s1_only() or not self.decision_is_current()):
            return False
        state = self.state(); flow = state.get(self.state_key, {})
        if flow.get('delivery_status') != 'complete' or flow.get('pending_card') or flow.get('validation', {}).get('exit_code') != 0:
            return False
        if state.get('stage_acceptance', {}).get(self.stage, {}).get('status') not in self.accepted_statuses:
            return False
        if any(r['outcome'] != 'complete' for r in flow['records']):
            return False
        try:
            # Later steps may legitimately revise shared facts/profile/report paths.
            for record in flow['records']:
                if record.get('receipt_ref'):
                    self.record_receipt(record)
            latest = {(a['path'], a.get('selector')): a for r in flow['records'] for a in r['artifacts'].values()}
            return len(flow['records']) == self.total and all(self.artifact_hash(a) == a['sha256']
                for a in latest.values()) and hashlib.sha256(self.validator.read_bytes()).hexdigest() == flow['validation']['validator_sha256']
        except (ValueError, OSError, KeyError):
            return False

    def recover(self, receipts, path='condition_first'):
        """Revalidate consecutive legacy evidence; do not invent past visible cards."""
        original = self.path.read_bytes() if self.path.exists() else None
        old = self.state()
        if old.get(self.state_key) or not isinstance(receipts, list) or not receipts:
            raise ValueError('恢复仅用于没有控制器状态的旧任务，需要连续的真实产物回执')
        if path not in {'condition_first', 'supply_first'}:
            raise ValueError('未知 S1 路径')
        if hasattr(self, 'roadmap_module'):
            legacy = self.roadmap_module.resolve_roadmap(self.home, state={'s1_execution': {}})['stages']['S1']
            self.steps = legacy['steps']; self.roadmap_stage = legacy; self.total = len(self.steps)
        self.delivery_step = int(self.roadmap_stage.get("delivery_step", self.total))
        archive = self.output / 'history' / ('legacy-' + uuid.uuid4().hex + '.yaml')
        atomic_write(archive, original or '{}\n')
        old[self.state_key] = {'schema_version': 1, 'path': path, 'cursor': 1, 'records': [],
                               'run_mode': old.get('run_mode', 'production'), 'events': [],
                               'pending_card': None, 'delivery_status': 'pending',
                               'origin': 'legacy_revalidated', 'historical_chat_cards': 'not_verified',
                               'legacy_backup': archive.relative_to(self.root).as_posix()}
        try:
            self._recovering = True
            self._position(old); self.save(old)
            rows = [f'【{self.stage} · 旧任务恢复卡】按实际文件重新核验；历史步骤卡是否展示未验证。',
                    '| 步骤 | 本次核验状态 | 卡片记录 |', '|---|---|---|']
            for receipt in receipts:
                self.record(receipt)
                state, flow = self._execution()
                event = flow['events'][-1]
                event['recovery_audit'] = True
                rows.append(f"| {event['n']} | {event['outcome']} | [本次核验卡](<{self.root / event['card_path']}>) |")
                # Explicit recovery is a batch evidence audit, not past chat acknowledgement.
                flow['pending_card'] = None
                self.save(state)
                if event['outcome'] == 'blocked':
                    break
            rows.append(self.status())
            summary = '\n'.join(rows) + '\n'
            out = self.output / 'cards' / ('recovery-' + uuid.uuid4().hex + '.md')
            atomic_write(out, summary)
            state, flow = self._execution()
            flow['pending_card'] = out.relative_to(self.root).as_posix()
            self.save(state)
            self._write_visible_cards()
            return summary
        except Exception:
            if original is None:
                self.path.unlink(missing_ok=True)
            else:
                atomic_write(self.path, original)
            raise
        finally:
            self._recovering = False

    def reopen_demand(self, message):
        if not str(message).strip() or not re.search(r'重新|新定位|换方向', message):
            raise ValueError('重新验证需求需要用户本次具体定位选择原话')
        state, flow = self._execution()
        if flow.get('roadmap_version') != 'r01.m2': raise ValueError('旧案保留原进度，不能用新需求门自动迁移')
        if flow.get('pending_card'): raise ValueError('先展示并ack当前警示卡，再按真实回复重新验证需求')
        flow.setdefault('revisions', []).append({'message':message,'action':'revalidate_positioning',
            'previous_records':[row for row in flow['records'] if row['n'] >= 3]})
        flow['records']=[row for row in flow['records'] if row['n'] < 3]
        flow['cursor']=3;flow['delivery_status']='pending'
        for event in flow.get('events', []):
            if event['n'] >= 3:
                event['superseded'] = True
        evidence_windows.start_round(flow, origin='reopen_demand')
        for key in ('validated_positioning','validation','demand_exception','recompute_queue'): flow.pop(key,None)
        invalid={step['control']['id'] for step in self.steps[2:]}
        state['completed_steps']=[name for name in state.get('completed_steps',[]) if name not in invalid]
        state['blocked_reason']=None
        state.setdefault('stage_acceptance',{}).setdefault(self.stage,{})['status']='pending'
        self._position(state);self.save(state)
        return self.status()

    def reopen(self, numbers, message):
        """Explicit selective S1 revision, with immutable pre-revision evidence."""
        if self.stage != 'S1' or not numbers or numbers != sorted(set(numbers)) or any(type(n) is not int or not 1 <= n <= 9 for n in numbers):
            raise ValueError('reopen 仅支持 S1 按顺序列出的第1–9步，不得重复或跳序')
        if not str(message).strip() or str(message).strip().lower() in {'继续', '下一步', '好', 'ok', '确认'}:
            raise ValueError('定向重开需要本次实际用户原话 --reason')
        if 9 not in numbers:
            raise ValueError('定向重开必须包含第9步，以重新封存和验收受影响报告')
        self.require_current_plugin()
        state, flow = self._execution()
        self.require_reply_ack(state, flow)
        completed = {r['n'] for r in flow['records']}
        prerequisites = set(range(1, max(numbers))) - set(numbers)
        if not prerequisites <= completed:
            missing = '、'.join(str(n) for n in sorted(prerequisites - completed))
            raise ValueError(f'定向重开不得绕过未完成且未列入队列的前置步骤：第 {missing} 步还没有正式记录。'
                             f"S1 当前停在第 {flow['cursor']} 步，先按顺序 begin → record 完成这些步骤；"
                             'reopen 只用于重做已经交付过的步骤')
        before = list(sys.path)
        try:
            sys.path.insert(0, str(self.home / 'scripts'))
            from sync_s1_decision_reports import rollback_transaction
            with rollback_transaction(self.root):
                history = self.output / 'history' / ('before-reopen-' + uuid.uuid4().hex + '.yaml')
                atomic_write(history, self.path.read_bytes())
                flow.setdefault('revisions', []).append({'action': 'explicit_reopen', 'message': str(message),
                    'affected_steps': numbers, 'backup': history.relative_to(self.root).as_posix(),
                    'previous_records': [r for r in flow['records'] if r['n'] in numbers],
                    'previous_validation': flow.get('validation'),
                    'previous_acceptance': state.get('stage_acceptance', {}).get('S1'),
                    'previous_handoff': state.get('stage_handoffs', {}).get('S1')})
                flow['records'] = [r for r in flow['records'] if r['n'] not in numbers]
                for event in flow['events']:
                    if event['n'] in numbers:
                        event['superseded'] = True
                flow.update(recompute_queue=list(numbers), cursor=numbers[0], delivery_status='pending', pending_card=None)
                evidence_windows.start_round(flow, origin='reopen')
                for key in ('validation', 'visibility_boundary'):
                    flow.pop(key, None)
                invalid = {self.steps[n-1]['control']['id'] for n in numbers}
                state['completed_steps'] = [s for s in state.get('completed_steps', []) if s not in invalid]
                state.setdefault('stage_acceptance', {})['S1'] = {'status': 'pending', 'notes': '用户定向重开，须重新验收；旧记录仅供追溯'}
                state.get('stage_handoffs', {}).pop('S1', None)
                state['blocked_reason'] = None
                self._position(state)
                self.save(state)
                return self.status()
        finally:
            sys.path[:] = before

    def reopen_supplier(self, message):
        if not message.strip() or message.strip().lower() in {'继续', '下一步', '好', 'ok', '确认'}:
            raise ValueError('后补修订需要用户本次实际资料说明，泛化继续不重开')
        state, flow = self._execution()
        if flow['pending_card']:
            raise ValueError('先展示并核对待发步骤卡，再开始后补修订')
        if not any(r['n'] == (5 if flow.get('roadmap_version') == 'r01.m2' else 6) for r in flow['records']):
            raise ValueError('供应商回填步骤尚未完成，继续当前步骤即可')
        if flow.get('roadmap_version') == 'r01.m2':
            affected = {6, 7, 9}
            flow.setdefault('revisions', []).append({'message':message, 'affected_steps':[6,7,9],
                'previous_records':[row for row in flow['records'] if row['n'] in affected]})
            flow['records'] = [row for row in flow['records'] if row['n'] not in affected]
            flow['recompute_queue'] = [6,7,9]; flow['cursor'] = 6; flow['delivery_status'] = 'pending'
            for event in flow.get('events', []):
                if event['n'] in affected:
                    event['superseded'] = True
            evidence_windows.start_round(flow, origin='reopen_supplier')
            flow.pop('validation',None)
            node = state.setdefault('stage_acceptance',{}).setdefault(self.stage,{})
            node.clear();node.update(status='pending',notes='供应商事实已更新，重算成本、推广和综合报告')
            invalidated = {self.steps[n-1]['control']['id'] for n in affected}
            state['completed_steps']=[name for name in state.get('completed_steps',[]) if name not in invalidated]
            state['supplier_data_status']='pending_update'
            self._position(state);self.save(state)
            return self.status()
        history = self.output / 'history' / ('before-supplier-update-' + uuid.uuid4().hex + '.yaml')
        atomic_write(history, self.path.read_bytes())
        flow.setdefault('revisions', []).append({'message': message, 'backup': history.relative_to(self.root).as_posix()})
        flow['records'] = [r for r in flow['records'] if r['n'] < 6]
        for event in flow.get('events', []):
            if event['n'] >= 6:
                event['superseded'] = True
        flow['cursor'] = 6
        flow['delivery_status'] = 'pending'
        evidence_windows.start_round(flow, origin='reopen_supplier')
        flow.pop('validation', None)
        state['supplier_data_status'] = 'pending_update'
        invalidated = {s['control']['id'] for s in self.steps[5:]}
        state['completed_steps'] = [s for s in state.get('completed_steps', []) if s not in invalidated]
        state.setdefault('stage_acceptance', {}).setdefault(self.stage, {})['status'] = 'pending'
        self._position(state); self.save(state)
        return self.status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('action', choices=['init', 'begin', 'status', 'record', 'ack', 'cards', 'nav', 'gate-s2', 'recover', 'reopen-supplier', 'reopen-demand', 'rebuild', 'reopen'])
    parser.add_argument('--stage', choices=['S1'], default='S1')
    parser.add_argument('--steps', help='按顺序列出的定向重开步骤，例如 8,9')
    parser.add_argument('--reason', default='')
    parser.add_argument('--path', choices=['condition_first', 'supply_first'], default='condition_first')
    parser.add_argument('--step', type=int, default=0)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--reply-file', type=Path)
    parser.add_argument('--roadmap', action='store_true')
    parser.add_argument('--user-message', default='')
    args = parser.parse_args()
    try:
        flow = Flow(args.root, Path(__file__).resolve().parents[1])
        if args.action == 'init': result = flow.init(args.path)
        elif args.action == 'begin': result = flow.begin()
        elif args.action == 'status': result = flow.status(args.roadmap)
        elif args.action == 'cards': result = flow.visible_cards()
        elif args.action == 'nav':
            result = flow.nav_bar() + '\n\n' + (flow.nav_step(args.step) if args.step else flow.nav_table())
            dashboard = flow.refresh_nav_dashboard()
            if dashboard:
                result += '\n\n导航页：' + dashboard
        elif args.action == 'record':
            if not args.receipt: raise ValueError('record 需要 --receipt JSON 文件')
            result = flow.record(json.loads(args.receipt.read_text(encoding='utf-8-sig')))
        elif args.action == 'ack':
            result = flow.ack_file(args.reply_file) if args.reply_file else flow.ack_current()
        elif args.action == 'recover':
            if not args.receipt: raise ValueError('recover 需要 --receipt 连续回执 JSON 数组')
            result = flow.recover(json.loads(args.receipt.read_text(encoding='utf-8-sig')), args.path)
        elif args.action == 'reopen-supplier': result = flow.reopen_supplier(args.user_message)
        elif args.action == 'reopen-demand': result = flow.reopen_demand(args.user_message)
        elif args.action == 'rebuild': result = flow.rebuild(args.step)
        elif args.action == 'reopen': result = flow.reopen([int(n.strip()) for n in (args.steps or '').split(',') if n.strip()], args.reason)
        else:
            if not flow.can_handoff(): raise ValueError('S1 报告交付、业务验收或有效证据未通过；不得进入 S2')
            result = 'S1 文件门禁通过；仍按用户授权范围决定是否进入 S2。'
        print(result)
        return 0
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        print('S1 流程检查未通过：' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
