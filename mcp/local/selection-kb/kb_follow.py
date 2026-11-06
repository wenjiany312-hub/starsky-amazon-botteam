"""长连接知识库只跟随数据目录，不热更新已导入的服务代码。

每次工具调用重新核对小型 current.json 与插件清单；不缓存失败或仅凭 mtime
跳过检查。候选 Store 完成预读后再次检查指针，确认没有发生并发升级/回滚才
交给调用方。所有状态均限于本进程；不改指针、不操作其他进程。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

PLUGIN = 'starsky-amazon-agent'
KINDS = ('selection-kb', 'ads-kb', 'promotion-kb')


def _layout(kb):
    """只识别完整安装布局，版本来自实际解析路径而非宽松子串。"""
    resolved = Path(kb).resolve()
    parts = resolved.parts
    # 完整后缀有八段：marketplace/versions/V/plugins/PLUGIN/mcp/KIND/kb。
    if (len(parts) < 9 or parts[-8:-6] != ('marketplace', 'versions')
            or parts[-5:-2] != ('plugins', PLUGIN, 'mcp')):
        return None
    if parts[-1] != 'kb' or parts[-2] not in KINDS:
        return None
    return resolved.parents[7], parts[-6], parts[-2]


def _version(value):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or value in ('.', '..') or any(c in value for c in '/\\\x00')):
        raise ValueError('current.json 缺少有效的插件版本')
    return value


def inspect(kb):
    """核对数据路径和已安装指针；失败也保留本次实际服务版本。"""
    info = {'state': 'not_applicable', 'serving_version': None, 'current_version': None}
    try:
        layout = _layout(kb)
        if not layout:
            return info
        root, serving, kind = layout
        info.update(state='current', serving_version=serving)
        raw = (root / 'current.json').read_bytes()
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise ValueError('current.json 必须是对象')
        current = _version(state.get('plugin_version'))
        info['current_version'] = current
        source_value = state.get('source')
        if not isinstance(source_value, str) or not source_value:
            raise ValueError('current.json 缺少安装路径')
        source = Path(source_value)
        expected = root / 'marketplace/versions' / current / 'plugins' / PLUGIN
        # 不对 expected 先 resolve，否则指向旧版的目录软链也会被当成新版。
        if not source.is_absolute() or source.resolve() != expected or expected.resolve() != expected:
            raise ValueError('current.json 的实际安装路径与版本目录不一致')
        manifest_path = source / '.codex-plugin/plugin.json'
        if manifest_path.resolve() != expected / '.codex-plugin/plugin.json':
            raise ValueError('插件清单实际路径与版本目录不一致')
        manifest_raw = manifest_path.read_bytes()
        manifest = json.loads(manifest_raw)
        if (not isinstance(manifest, dict) or manifest.get('name') != PLUGIN
                or manifest.get('version') != current):
            raise ValueError('已安装插件清单与 current.json 的版本不一致')
        target = expected / 'mcp' / kind / 'kb'
        if target.resolve() != target:
            raise ValueError('知识库实际路径与声明版本不一致')
        if (not (target.parent / 'server.py').is_file() or not target.is_dir()
                or (target.parent / 'server.py').resolve() != target.parent / 'server.py'):
            raise ValueError('已安装版本里的知识库资料不完整')
        for name in ('kb_manifest.yaml', 'learning_plan.yaml'):
            if not (target / name).is_file() or (target / name).resolve() != target / name:
                raise ValueError('知识库缺少或实际路径不符：' + name)
        if not (target / 'cards').is_dir() or (target / 'cards').resolve() != target / 'cards':
            raise ValueError('知识库缺少 cards 目录或其实际路径不符')
        if (root / 'current.json').read_bytes() != raw:
            raise ValueError('核对时安装指针发生变化，下次调用会重试')
        info.update(target=str(target), revision=hashlib.sha256(raw + b'\0' + manifest_raw).hexdigest())
        if Path(kb).resolve() != target:
            info['state'] = 'behind'
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        info.update(state='unverified', error=str(error)[:300])
    return info


class Follower:
    def __init__(self, code_path=None, server_version=None):
        self.last = {'state': 'not_applicable'}
        self.switched = None
        self.code_path = str(Path(code_path).resolve()) if code_path else None
        self.server_version = server_version
        layout = _layout(Path(self.code_path).parent / 'kb') if self.code_path else None
        # 启动时固定，绝不从切换后的 Store 或 current.json 推断进程代码版本。
        self.code_version = None
        self.code_error = None
        if layout:
            try:
                source = Path(self.code_path).parents[2]
                manifest = json.loads((source / '.codex-plugin/plugin.json').read_bytes())
                if (not isinstance(manifest, dict) or manifest.get('name') != PLUGIN
                        or manifest.get('version') != layout[1]):
                    raise ValueError('启动代码目录与插件清单版本不一致')
                self.code_version = layout[1]
            except (OSError, ValueError, TypeError) as error:
                self.code_error = str(error)[:300]
        self.data_path = None

    def apply(self, store, make_store):
        """候选数据可读且指针仍一致才提交；失败保持旧 Store，下次调用再试。"""
        self.switched = None  # 只表示本次调用的切换，不沿用历史成功状态。
        self.data_path = str(store.kb)
        info = inspect(store.kb)
        self.last = info
        if info['state'] != 'behind':
            return store
        try:
            fresh = make_store(Path(info['target']))
            if Path(fresh.kb).resolve() != Path(info['target']):
                raise ValueError('候选 Store 实际路径与目标路径不一致')
            # Store 构造只检查文件存在；status 预读能发现清单/计划/卡片加载故障。
            validate = getattr(fresh, 'validate_for_follow', fresh.status)
            validate()
            after = inspect(store.kb)
            if (after['state'] != 'behind' or after.get('revision') != info['revision']
                    or after.get('target') != info['target']):
                self.last = {**after, 'state': 'unverified',
                             'error': '载入期间安装指针或清单发生变化，保留当前数据，下次调用会重试'}
                return store
        except (Exception, SystemExit) as error:  # 不吞掉 KeyboardInterrupt / GeneratorExit。
            self.last = {**info, 'state': 'unverified', 'error': '目标知识库载入失败：' + str(error)[:200]}
            return store
        self.switched = {'from': info['serving_version'], 'to': info['current_version'],
                         'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        self.data_path = str(fresh.kb)
        self.last = {**info, 'state': 'switched', 'serving_version': info['current_version']}
        return fresh

    def describe(self):
        info = self.last
        current = info.get('current_version')
        code_state = ('unknown' if not self.code_version or not current or info['state'] == 'unverified'
                      else 'current' if self.code_version == current else 'stale')
        out = {'follow': info['state'], 'serving_version': info.get('serving_version'),
               'data_version': info.get('serving_version'), 'data_path': self.data_path,
               'current_version': current, 'code_version': self.code_version, 'code_path': self.code_path,
               'server_version': self.server_version, 'code_state': code_state, 'pid': os.getpid(),
               'restart_required': code_state == 'stale', 'follow_scope': 'knowledge_data_only'}
        if self.switched:
            out['switched'] = dict(self.switched)
        if info.get('error'):
            out['error'] = info['error']
        if self.code_error:
            out['code_error'] = self.code_error
        notes = ['跟随只切换知识库数据；服务代码和已导入模块仍是本进程启动时的版本。']
        if info['state'] in ('behind', 'unverified'):
            notes.append('本次未切换，保留最近一次成功使用的数据；下次工具调用会重新核对并重试。')
        if code_state != 'current':
            notes.append('如需新服务代码，请新开聊天尝试重连，必要时在其他任务结束后重启宿主，再用当前聊天原生 kb_status 回读；新聊天不保证创建新进程。')
        out['note'] = ' '.join(notes)
        return out
