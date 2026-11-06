#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
视频出片 v1.0
====================================================================
把即梦下载回来的分段 mp4 + 透明字幕 PNG，拼成一条 16:9 母版，
再按投放位派生出各自合规的成片。

它替代什么
--------------------------------------------------------------------
替代「在剪映里手工拼一遍，再手工导出三次」这一段。
手工导三次最容易出的错是三份规格不一致，而这些错要到上传时才被平台拒。

⛔ 它不替代剪映：调节奏、配乐、转场、精修仍然在剪映做。
   典型用法是二选一 ——
     A. 用本脚本一把出三个投放位的片子（节奏简单的产品片够用）
     B. 在剪映精修完导出一条 16:9 母版，再用本脚本只做「派生 + 校验」
        （--from-master 母版.mp4）

用法
--------------------------------------------------------------------
    python 视频出片.py 出片配置.yaml
    python 视频出片.py 出片配置.yaml --from-master 剪映导出的母版.mp4
    python 视频出片.py 出片配置.yaml --keep-temp     # 保留中间文件排查用

依赖：ffmpeg / ffprobe（winget install --id Gyan.FFmpeg -e）

退出码：0 全部成片且合规｜1 有成片不合规｜2 配置或依赖问题

作者：坚哥｜跨境者说干货 · 星空的跨境圈子｜Codex亚马逊实战
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import importlib.util
import os
import re
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:                                          # pragma: no cover
    sys.exit("[缺依赖] 请先安装：pip install pyyaml")

SPECS_DEFAULT = (Path(__file__).resolve().parent.parent
                 / "templates" / "video_placement_specs.example.yaml")


def need(tool: str) -> None:
    if shutil.which(tool) is None:
        sys.exit(f"[缺依赖] 找不到 {tool}。Windows 安装：\n"
                 f"      winget install --id Gyan.FFmpeg -e")


def run(cmd: list[str], desc: str) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-12:])
        sys.exit(f"[ffmpeg 失败] {desc}\n{tail}")


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"[读不出视频] {path}")
    return json.loads(r.stdout)


def has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


def duration(path: Path) -> float:
    return float(probe(path).get("format", {}).get("duration", 0) or 0)


def hex_of(tokens: dict | None, token_name: str, fallback: str = "#000000") -> str:
    if tokens:
        v = (tokens.get("palette") or {}).get(token_name)
        if v:
            return v
    return fallback


# ------------------------------------------------------- 各步骤

def normalize(src: Path, dst: Path, W: int, H: int, fps, pad_hex: str,
              vbr: str, abr: str) -> None:
    """统一到母版规格，并保证有音轨。
    即梦各段的分辨率/帧率/有无音轨可能不一致，不统一就没法 concat。"""
    color = "0x" + pad_hex.lstrip("#")
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={color},"
          f"fps={fps},setsar=1,format=yuv420p")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    if has_audio(src):
        amap = ["-map", "0:a:0"]
    else:
        # SBV 要求必须有音轨；缺的段补静音，否则 concat 会掉轨
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        amap = ["-map", "1:a:0"]
    cmd += ["-vf", vf, "-map", "0:v:0"] + amap + [
        "-c:v", "libx264", "-preset", "medium", "-b:v", vbr,
        "-c:a", "aac", "-b:a", abr, "-ar", "44100", "-ac", "2",
        "-shortest", str(dst)]
    run(cmd, f"归一化 {src.name}")


def concat(parts: list[Path], dst: Path, tmp: Path) -> None:
    lst = tmp / "concat_list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(dst)], "拼接分段")


def mix_music(src: Path, music: Path, volume: float, dst: Path,
              vbr: str, abr: str) -> None:
    """垫一条贯穿全片的背景音乐。

    为什么这一步几乎是必须的：AAC 编数字静音几乎不耗码率，
    只要片子里有一段是静音（即梦某些段就没有音轨），
    整片的**平均**音频码率就会被拉到 Amazon 的 96kbps 下限以下 ——
    实测 16s 片子含 6s 静音时，即便 -b:a 192k，平均也只有 94kbps。
    调高码率治不好，垫一条连续音乐才治得好。
    """
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(src), "-stream_loop", "-1", "-i", str(music),
         "-filter_complex",
         f"[1:a]volume={volume}[bed];[0:a][bed]amix=inputs=2:duration=first:"
         f"dropout_transition=0:normalize=0[a]",
         "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", abr, "-ar", "44100",
         str(dst)], "混入背景音乐")


def derive(master: Path, dst: Path, spec: dict, mode: str, pad_hex: str,
           vbr: str, abr: str, subs: list[dict], base: Path) -> None:
    """按投放位派生：先改画幅，**再**叠该画幅专属的字幕层。

    ⛔ 顺序很重要。早期版本是先把字幕烧进母版再缩放，
    结果竖版里的字幕跟着画面一起被缩小，挤在中间那条带里，
    上下补出来的空带全浪费 —— 那就是「横版加黑边」。
    字幕按画幅分别渲染、派生后再叠，竖版才能把补出来的带用起来。
    """
    TW, TH = spec["width"], spec["height"]
    color = "0x" + pad_hex.lstrip("#")
    info = probe(master)
    v = next(s for s in info["streams"] if s.get("codec_type") == "video")
    sw, sh = int(v["width"]), int(v["height"])
    fps = spec["fps_allowed"][-1]
    dur = float(info.get("format", {}).get("duration", 0) or 0)

    if abs((sw / sh) - (TW / TH)) / (TW / TH) <= 0.02:
        reframe = f"scale={TW}:{TH},setsar=1"
    elif mode == "crop":
        reframe = (f"crop=min(iw\\,ih*{TW}/{TH}):min(ih\\,iw*{TH}/{TW}),"
                   f"scale={TW}:{TH},setsar=1")
    else:
        reframe = (f"scale={TW}:{TH}:force_original_aspect_ratio=decrease,"
                   f"pad={TW}:{TH}:(ow-iw)/2:(oh-ih)/2:color={color},setsar=1")

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(master)]
    for s in subs:
        p = base / s["png"]
        if not p.exists():
            sys.exit(f"[字幕图不存在] {s['png']}\n"
                     f"  先跑：python <home>/scripts/叠字渲染器.py "
                     f"{{产品根}}/S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml")
        # ⛔ -loop 1 不能省。单帧 PNG 不 loop 时流很快结束，
        #   overlay 到后半段就无图可画 —— 实测：叠在 1–5s 正常，
        #   叠在 12.5–16s 静默失效，收尾卡直接不出现，且不报任何错。
        cmd += ["-loop", "1", "-i", str(p)]

    chain = [f"[0:v]{reframe}[bg]"]
    cur = "[bg]"
    for i, s in enumerate(subs, start=1):
        out = f"[v{i}]"
        chain.append(f"{cur}[{i}:v]overlay=0:0:"
                     f"enable='between(t,{float(s['start'])},{float(s['end'])})'{out}")
        cur = out
    # ⛔ fps 必须放在所有 overlay **之后**。放前面的话，
    #   -loop 1 的图片输入会把输出帧时序带偏 —— 实测 16.03s 变成 18.07s、
    #   帧率变成 29.335（不在 Amazon 允许列表里），而且不报错。
    chain.append(f"{cur}fps={fps},format=yuv420p[vout]")

    cmd += ["-filter_complex", ";".join(chain), "-map", "[vout]", "-map", "0:a:0",
            "-c:v", "libx264", "-preset", "medium", "-b:v", vbr,
            "-c:a", "aac", "-b:a", abr, "-ar", "44100",
            # 图片输入是无限流，用母版时长显式收尾比 -shortest 更确定
            "-t", f"{dur:.3f}", "-movflags", "+faststart", str(dst)]
    run(cmd, f"派生 {dst.name}")


def subs_for(spec: dict, cfg_subs) -> list[dict]:
    """按投放位画幅挑对应的字幕集。
    支持两种写法：按 canvas 分组的 dict，或旧的扁平 list（当作 16:9）。"""
    if not cfg_subs:
        return []
    if isinstance(cfg_subs, list):
        return cfg_subs if spec["aspect_ratio"] == "16:9" else []
    key = {"16:9": "video_16x9", "9:16": "video_9x16"}.get(spec["aspect_ratio"])
    return cfg_subs.get(key, []) if key else []


# ------------------------------------------------------- 入口


HOME=Path(__file__).resolve().parents[1]


def r03_helper(name):
    path=HOME/'scripts'/(name+'.py');spec=importlib.util.spec_from_file_location('_r03_video_'+name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def r03_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def r03_safe(root,path):
    root=Path(root).resolve();target=(root/path).resolve();target.relative_to(root);return target


def r03_evidence(root):
    """Probe actual operation sources; no operation evidence means display-only, never fabricated motion."""
    root=Path(root).resolve();manual=r03_helper('product_manual');_,_,doc=manual._current(root)
    try:manual.read_confirmed(root);confirmed=True
    except ValueError:confirmed=False
    allowed={ref['source'] for a in doc.get('actions') or [] if a.get('status')!='hypothesis' and not a.get('missing_reason') for ref in a.get('evidence') or [] if 'time' in ref and ref.get('frame_proof')}
    keyframes=r03_helper('video_keyframes');rows=[]
    for source in doc.get('sources') or []:
        if source['id'] not in allowed or source.get('kind') not in {'user','manufacturer','1688'}:continue
        file=r03_safe(root,source['path'])
        if file.suffix.lower() not in {'.mp4','.mov','.avi','.mkv','.webm'}:continue
        cv2,cap,fps,count=keyframes._capture(file)
        try:w=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH));h=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        finally:cap.release()
        crop=keyframes._crop(file);box=keyframes._box(crop);cw,ch=(box[2]-box[0],box[3]-box[1]) if box else (w,h)
        quality=source.get('quality_review') or {};clear=(quality.get('source') in {'user','model_visual'} and bool(quality.get('message')) and quality.get('sha256')==r03_sha(file) and (quality.get('source')!='model_visual' or bool(quality.get('task_id'))))
        high=min(cw,ch)>=1080 and max(cw,ch)>=1920 and clear
        rows.append({'id':source['id'],'kind':source['kind'],'path':source['path'],'sha256':r03_sha(file),'width':w,'height':h,'effective_width':cw,'effective_height':ch,'duration':round(count/fps,3),'crop':crop,'high_quality':bool(high)})
    priority={'user':0,'manufacturer':1,'1688':2};rows.sort(key=lambda r:(priority[r['kind']],not r['high_quality'],r['path']))
    tier='A' if any(r['high_quality'] for r in rows) else 'B' if rows else 'C'
    return {'tier':tier,'sources':rows,'manual_confirmed':confirmed,
        'method':{'A':'实拍剪辑为主，AI 只补开场场景','B':'低清操作证据 + 真实状态多帧；同时提示补拍','C':'静态展示、镜头运动和字幕；不演示未证实动作'}[tier],
        'shooting_list':'00_产品资料/拍摄清单.md','video_blocks_delivery':False}


def r03_validate_package(root,package):
    root=Path(root).resolve();manual=r03_helper('product_manual');doc=manual.read_confirmed(root);evidence=r03_evidence(root)
    if package.get('format')=='defer':return {**evidence,'status':'deferred'}
    actions={a['id']:a for a in doc.get('actions') or []};state={r['id']:r for r in doc.get('states') or []}
    chosen=package.get('closure_method');used=set();operation_sources={s['id'] for s in evidence['sources']}
    for shot in package.get('shots') or []:
        ids=shot.get('action_ids') or []
        operation_words=r'\b(?:open(?:ing)?|clos(?:e|ing)|fold(?:ing)?|unfold|unzip|detach|attach|assembl(?:e|ing)|install|roll\s+up|insert\s+tools)\b|翻盖|拉开|折叠|卷起|拆卸|安装|装入'
        if not ids and re.search(operation_words,str(shot.get('prompt') or ''),re.I):raise ValueError('提示词含操作动作却未绑定真实动作依据；没有证据只做静态展示')
        if evidence['tier']=='C' and (ids or shot.get('shot_type','display')!='display'):raise ValueError('没有真实操作证据，只能做展示类镜头，不能演示动作')
        if shot.get('source_kind')=='ai_generated' and ids:raise ValueError('AI 最终图只用于场景/风格，不作为操作证据')
        manual.read_confirmed(root,required_states=shot.get('state_ids') or [],required_actions=ids)
        for identity in ids:
            action=actions[identity]
            if not any(ref.get('source') in operation_sources and ref.get('frame_proof') for ref in action.get('evidence') or []):raise ValueError('动作缺真实操作视频/截帧依据：'+identity)
            method=action.get('closure_method')
            if method:used.add(method)
            if method and chosen!=method:raise ValueError('一条视频只能使用一种有证据的收法，先明确同一收法')
        for identity in shot.get('state_ids') or []:
            image=state[identity].get('image','')
            if ids:
                image_path=r03_safe(root,image)
                valid=False
                for ref in state[identity].get('evidence') or []:
                    if ref.get('source') not in operation_sources or 'time' not in ref or not ref.get('frame_proof') or not ref.get('file'):continue
                    if image_path!=r03_safe(root,ref['file']):continue
                    proof=json.loads(r03_safe(root,ref['frame_proof']).read_bytes())
                    source=next(r for r in evidence['sources'] if r['id']==ref['source'])
                    if (proof.get('mode')=='export' and proof.get('source_sha256')==source['sha256']
                            and proof.get('outputs',{}).get(image_path.name)==r03_sha(image_path)
                            and any(f.get('path')==image_path.name and abs(f.get('actual',-10)-ref['time'])<=.5 for f in proof.get('frames') or [])):
                        valid=True;break
                if not valid:raise ValueError('操作状态关键帧必须是本品真实操作视频的同一抽帧文件，且来源/秒数/SHA 与回执一致：'+identity)
    if len(used)>1:raise ValueError('折法和卷法不能在一条视频里混用')
    return {**evidence,'status':'package_ready_not_generated','closure_method':chosen}


def r03_budget_key(root,package,method):
    """Prompts are repair content; only evidence or production method creates a new budget."""
    if isinstance(package,(str,Path)):
        candidate=r03_safe(root,package)
        if candidate.is_file():package=yaml.safe_load(candidate.read_bytes())
    if not isinstance(package,dict):return str(package)+':'+method # Read legacy caller/ledger identities.
    root=Path(root).resolve();evidence=r03_evidence(root)
    identity={'tier':method,'format':package.get('format'),'closure_method':package.get('closure_method'),
        'sources':sorted((row['id'],row['sha256']) for row in evidence['sources']),
        'manual_sha256':r03_sha(root/'00_产品资料/说明书确认.json')}
    return hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()+':'+method


def r03_generation_allowed(root,package_sha,method):
    path=Path(root)/'S3_视觉板块/视频_即梦/03_返修包/返修记录.json'
    data=json.loads(path.read_bytes()) if path.exists() else {}
    return (data.get(r03_budget_key(root,package_sha,method)) or {}).get('attempt',0)<2


def r03_attempt(root,package_sha,method,failure_sha,reasons):
    base=Path(root)/'S3_视觉板块/视频_即梦/03_返修包';base.mkdir(parents=True,exist_ok=True);path=base/'返修记录.json'
    data=json.loads(path.read_bytes()) if path.exists() else {};key=r03_budget_key(root,package_sha,method);row=data.get(key,{'attempt':0})
    if row.get('failure_sha')==failure_sha:return row
    if row['attempt']>=2:return {'status':'needs_user','attempt':2,'reasons':reasons,'choices':['补拍','改做展示类','本次跳过']}
    row={'attempt':row['attempt']+1,'failure_sha':failure_sha,'status':'repair_ready','reasons':reasons,'choices':['补拍','改做展示类','本次跳过']}
    data[key]=row;tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8');os.replace(tmp,path)
    return row


def r03_edit_config(root,package_path,out):
    root=Path(root).resolve();package=yaml.safe_load(r03_safe(root,package_path).read_bytes());evidence=r03_validate_package(root,package)
    if not r03_generation_allowed(root,package,evidence['tier']):raise ValueError('已达视频一次生成和一次返修上限：补拍 / 改做展示类 / 本次跳过')
    if evidence['tier']!='A':raise ValueError('只有 A 档默认实拍剪辑；B 按多帧投喂，C 只做展示')
    sources=sorted(evidence['sources'],key=lambda r:(not r['high_quality'],r['kind']!='user'))
    clips=[{'file':str(r03_safe(root,sources[0]['path']))}]
    for clip in package.get('ai_intro_clips') or []:
        if clip.get('purpose')!='intro_scene' or clip.get('action_ids'):raise ValueError('A 档 AI 只允许开场场景，不能替代真实操作')
        file=r03_safe(root,clip['path'])
        if r03_sha(file)!=clip['sha256']:raise ValueError('开场片段已变化')
        clips.insert(0,{'file':str(file)})
    cfg={'r03_product_root':str(root),'r03_video_package':r03_safe(root,package_path).relative_to(root).as_posix(),
        'clips':clips,'master':{'width':1920,'height':1080,'fps':30},'specs':str(SPECS_DEFAULT),'outputs':[{'placement':'listing_main','file':'video__listing_main.mp4'}]}
    out=r03_safe(root,out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(yaml.safe_dump(cfg,allow_unicode=True),encoding='utf8');return {'config':str(out),'tier':'A'}


def r03_content_errors(root,package,video,review,proof_path):
    doc=r03_helper('product_manual').read_confirmed(root);errors=[]
    expected={'video_sha256':r03_sha(video),'package_sha256':r03_sha(package),'manual_confirmation_sha256':r03_sha(Path(root)/'00_产品资料/说明书确认.json'),'sheet_evidence_sha256':r03_sha(proof_path)}
    if review.get('source') not in {'user','model_visual'} or not review.get('message') or (review.get('source')=='model_visual' and not review.get('task_id')):errors.append('缺当前视频实际视觉复核来源')
    if any(review.get(key)!=value for key,value in expected.items()):errors.append('视频/镜头包/说明书/实际抽帧已变化，需重新看当前内容')
    for key in ['order','closure_method','parts','hands','text_watermark','no_invented_operation']:
        if (review.get('checks') or {}).get(key) is not True:errors.append('视频逐项待核：'+key)
    shots=yaml.safe_load(Path(package).read_bytes()).get('shots') or []
    for identity in {a for shot in shots for a in shot.get('action_ids') or []}:
        item=(review.get('actions') or {}).get(identity) or {}
        if item.get('status')!='pass' or not item.get('note'):errors.append('视频动作未逐条对照说明书：'+identity)
    if not isinstance(review.get('has_hands'),bool) or not isinstance(review.get('props_present'),bool):errors.append('视频手部/道具尚未识别')
    if review.get('props_present') and review.get('disclaimer_visible') is not True:errors.append('视频有道具但缺 Tools not included 或不可读')
    scan=r03_helper('compliance_scan').scan_fields({'video_visible_copy':'\n'.join(review.get('visible_text') or [])},series=root)
    if scan['blocked']:errors.append('视频文字命中合规 block，不能交付')
    return errors


def r03_review_return(root,video,package_path,review_path=None,*,placement='listing_main'):
    root=Path(root).resolve();video=r03_safe(root,video);package_path=r03_safe(root,package_path)
    evidence=r03_validate_package(root,yaml.safe_load(package_path.read_bytes()));sha_before=r03_sha(video)
    if not r03_generation_allowed(root,package_path,evidence['tier']):return {'status':'needs_user','choices':['补拍','改做展示类','本次跳过'],'errors':['同一方式已生成一次并返修一次，停止自动重出'],'video_blocks_delivery':False}
    base=root/'S3_视觉板块/视频_即梦';out=base/'02_回填/抽帧对照'/sha_before[:16];out.mkdir(parents=True,exist_ok=True)
    frames=r03_helper('video_keyframes');sheets=frames.sheet(video,out,fps=1,per=24,cols=6);sheet_proof=out/'sheet_evidence.json'
    doc=r03_helper('product_manual').read_confirmed(root)
    def embed(path):
        suffix='jpeg' if path.suffix.lower() in {'.jpg','.jpeg'} else 'png'
        return '<img style="max-width:100%;height:auto" src="data:image/'+suffix+';base64,'+base64.b64encode(path.read_bytes()).decode()+'">'
    originals=''.join('<figure>'+embed(r03_safe(root,r['image']))+'<figcaption>'+html.escape(r.get('name',r['id']))+'</figcaption></figure>' for r in doc.get('states') or [] if r.get('image'))
    action_rows=''.join('<li>'+html.escape(a.get('name',a['id'])+'：'+a.get('motion','')+'；易错：'+a.get('common_mistakes',''))+'</li>' for a in doc.get('actions') or [])
    page=out/'视频抽帧对照.html';page.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>视频逐项对照</title><style>body{font:16px system-ui;padding:20px;max-width:1400px;margin:auto}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}figure{margin:0 0 20px}@media(max-width:700px){.grid{display:block}}</style><h1>视频抽帧与真实状态对照</h1><p>'+html.escape(evidence['tier']+' 档 · '+evidence['method'])+'</p><div class="grid"><section><h2>实际回填视频</h2>'+''.join(embed(p) for p in sheets)+'</section><section><h2>真实状态及动作</h2>'+originals+'<ul>'+action_rows+'</ul></section></div><p>逐条核对顺序、收法、部件、手、文字水印；生成对照表不等于验收通过。</p></html>',encoding='utf8')
    specs=yaml.safe_load(SPECS_DEFAULT.read_bytes())['placements'];validator=r03_helper('视频规格校验')
    if placement not in specs:raise ValueError('未知视频投放位')
    technical,warnings,measurements=validator.check(video,specs[placement])
    review=json.loads(r03_safe(root,review_path).read_bytes()) if review_path else {}
    errors=technical+(r03_content_errors(root,package_path,video,review,sheet_proof) if review else ['待实际查看当前视频与真实状态对照，不能把抽帧完成当通过'])
    result={'status':'pass' if not errors else 'needs_visual_review' if not review else 'needs_revision','tier':evidence['tier'],'html':str(page),'sheets':[str(p) for p in sheets],
        'errors':errors,'warnings':warnings,'measurements':measurements,'video_hand_metadata':'待核，未写入视频人物元数据','hand_status':'视频含手部' if review.get('has_hands') else '未识别' if not review else '未见手部',
        'video_blocks_delivery':False,'platform_limits':'沿用当前投放位登记规格；提交前核实适用规则'}
    if r03_sha(video)!=sha_before:raise ValueError('视频在验收期间变化，拒交')
    if errors and review:result['repair']=r03_attempt(root,package_path,evidence['tier'],sha_before,errors)
    if not errors:
        native=HOME/'skills/_visual_suite_contract/scripts/visual_returns.py';spec=importlib.util.spec_from_file_location('_video_atomic_returns',native);returns=importlib.util.module_from_spec(spec);spec.loader.exec_module(returns)
        candidate=base/'02_回填/待验收'/video.name;candidate.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(video,candidate)
        final=base/'02_回填/最终版'/video.name
        proof={'generator':'视频出片.py','validator_sha256':r03_sha(__file__),'specs_sha256':r03_sha(SPECS_DEFAULT),'video_sha256':sha_before,'package_path':package_path.relative_to(root).as_posix(),'package_sha256':r03_sha(package_path),'manual_confirmation_sha256':r03_sha(root/'00_产品资料/说明书确认.json'),
            'sheet_evidence':sheet_proof.relative_to(root).as_posix(),'sheet_evidence_sha256':r03_sha(sheet_proof),'review_path':r03_safe(root,review_path).relative_to(root).as_posix(),'review_sha256':r03_sha(r03_safe(root,review_path)),'placement':placement}
        row={'slot_id':'video','variant':'shared','device':'video','status':'accepted','output_path':final.relative_to(root).as_posix(),'sha256':sha_before,'source_return_path':video.relative_to(root).as_posix(),'normalization':{'source_sha256':sha_before},'final_audit':proof,'review_ref':proof['review_path']}
        returns.publish_candidate(root,candidate,base/'media_manifest.yaml',row);result['final']=str(final)
    (out/'视频验收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8');return result


def r03_verify_manifest(root,manifest):
    root=Path(root).resolve();rows=yaml.safe_load(r03_safe(root,manifest).read_bytes()).get('items') or [];errors=[]
    if not rows:errors.append('缺原生视频成品验收记录')
    for row in rows:
        try:
            proof=row['final_audit'];video=r03_safe(root,row['output_path']);package=r03_safe(root,proof['package_path']);review=r03_safe(root,proof['review_path']);sheet=r03_safe(root,proof['sheet_evidence'])
            video.relative_to((root/'S3_视觉板块/视频_即梦/02_回填/最终版').resolve())
            if proof.get('generator')!='视频出片.py' or proof['validator_sha256']!=r03_sha(__file__) or proof['specs_sha256']!=r03_sha(SPECS_DEFAULT):raise ValueError('视频验收器或规格已变化')
            if any(proof[key]!=r03_sha(path) for key,path in [('video_sha256',video),('package_sha256',package),('review_sha256',review),('sheet_evidence_sha256',sheet),('manual_confirmation_sha256',root/'00_产品资料/说明书确认.json')]):raise ValueError('视频/资料/复核内容已变化')
            if r03_sha(r03_safe(root,row['source_return_path']))!=row['normalization']['source_sha256']:raise ValueError('视频回填来源变化')
            r03_validate_package(root,yaml.safe_load(package.read_bytes()))
            errors+=r03_content_errors(root,package,video,json.loads(review.read_bytes()),sheet)
            specs=yaml.safe_load(SPECS_DEFAULT.read_bytes())['placements'];technical,_,_=r03_helper('视频规格校验').check(video,specs[proof['placement']]);errors+=technical
        except (OSError,ValueError,KeyError,TypeError) as error:errors.append(str(error))
    return {'status':'fail' if errors else 'pass','errors':errors}


def r03_cli(argv):
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['r03-evidence','r03-review','r03-edit-config']);ap.add_argument('root',type=Path);ap.add_argument('--video',type=Path);ap.add_argument('--package',type=Path);ap.add_argument('--review',type=Path);ap.add_argument('--out',type=Path);ap.add_argument('--placement',default='listing_main');a=ap.parse_args(argv)
    result=r03_evidence(a.root) if a.action=='r03-evidence' else r03_edit_config(a.root,a.package,a.out) if a.action=='r03-edit-config' else r03_review_return(a.root,a.video,a.package,a.review,placement=a.placement)
    print(json.dumps(result,ensure_ascii=False));return 0 if result.get('status') in {None,'pass'} else 2


def main() -> None:
    ap = argparse.ArgumentParser(description="视频出片：分段 + 字幕 → 母版 → 各投放位成片")
    ap.add_argument("config", help="出片配置 YAML")
    ap.add_argument("--from-master", help="跳过拼接，直接用剪映导出的母版派生")
    ap.add_argument("--keep-temp", action="store_true", help="保留中间文件")
    args = ap.parse_args()

    need("ffmpeg"); need("ffprobe")

    cfg_path = Path(args.config).resolve()
    if not cfg_path.exists():
        sys.exit(f"[配置不存在] {cfg_path}")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    base = cfg_path.parent
    if cfg.get('r03_product_root'):
        root=Path(cfg['r03_product_root']).resolve();package=yaml.safe_load(r03_safe(root,cfg['r03_video_package']).read_bytes());r03_validate_package(root,package)

    sp = Path(cfg["specs"]) if cfg.get("specs") else SPECS_DEFAULT
    if not sp.is_absolute():
        sp = (base / sp).resolve()
    if not sp.exists():
        sys.exit(f"[规格文件不存在] {sp}")
    specs = yaml.safe_load(sp.read_text(encoding="utf-8"))

    tokens = None
    if cfg.get("style_tokens"):
        tp = base / cfg["style_tokens"]
        if tp.exists():
            tokens = yaml.safe_load(tp.read_text(encoding="utf-8"))

    m = cfg.get("master", {})
    W, H = int(m.get("width", 1920)), int(m.get("height", 1080))
    fps = m.get("fps", 30)
    # 音频默认给到 192k：AAC 对静音段几乎不耗码率，片子里只要有安静的段落，
    # 平均码率就会被拉低到 Amazon 的 96kbps 下限以下。给足余量。
    vbr, abr = m.get("video_bitrate", "6M"), m.get("audio_bitrate", "192k")

    vd = specs.get("vertical_derivation", {})
    mode = vd.get("mode", "pad")
    pad_hex = hex_of(tokens, vd.get("pad_color_token", "plate_bg"), "#000000")

    outdir = base / cfg.get("output_dir", "video_exports")
    outdir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="出片_"))

    try:
        # ---------- 母版 ----------
        if args.from_master:
            master = Path(args.from_master)
            if not master.is_absolute():
                master = base / master
            if not master.exists():
                sys.exit(f"[母版不存在] {master}")
            print(f"母版：直接用 {master.name}"
                  f"（{duration(master):.1f}s）—— 跳过拼接与字幕\n")
        else:
            clips = [base / c["file"] for c in cfg.get("clips", [])]
            missing = [c for c in clips if not c.exists()]
            if missing:
                sys.exit("[分段缺失]\n" + "\n".join(f"  {c}" for c in missing))
            if not clips:
                sys.exit("[没有分段] 配置里 clips 为空；"
                         "若要用剪映母版请加 --from-master")

            print(f"归一化 {len(clips)} 段 → {W}x{H} @{fps}fps")
            norm, silent = [], []
            for i, c in enumerate(clips):
                if not has_audio(c):
                    silent.append(c.name)
                d = tmp / f"norm_{i:02d}.mp4"
                normalize(c, d, W, H, fps, pad_hex, vbr, abr)
                print(f"  ✓ {c.name}" + ("  （无音轨，已补静音）" if c.name in silent else ""))
                norm.append(d)

            joined = tmp / "concat.mp4"
            concat(norm, joined, tmp)
            print(f"拼接完成 {duration(joined):.1f}s")

            # ---------- 背景音乐 ----------
            mus = cfg.get("music") or {}
            if mus.get("file"):
                mp = base / mus["file"]
                if not mp.exists():
                    sys.exit(f"[背景音乐不存在] {mus['file']}")
                mixed = tmp / "mixed.mp4"
                mix_music(joined, mp, float(mus.get("volume", 0.18)), mixed, vbr, abr)
                joined = mixed
                print(f"背景音乐已垫入（音量 {mus.get('volume', 0.18)}）")
            elif silent:
                print(f"\n  ⚠️  有 {len(silent)} 段没有音轨："
                      f"{', '.join(silent)}")
                print("      这些段会成为数字静音，把整片的**平均**音频码率拉低，")
                print("      很可能低于 Amazon 的 96kbps 下限而被拒。")
                print("      解法：在配置里加 music: 垫一条贯穿全片的背景音乐"
                      "（SBV 本来也要求有音轨）。\n")

            # ⛔ 母版**不烧字幕** —— 字幕在派生之后按各自画幅叠，
            #    否则竖版的字会跟着画面一起被缩小。
            master = joined
            keep = outdir / cfg.get("master_filename", "master__16x9.mp4")
            shutil.copy(master, keep)
            print(f"母版留档（无字幕）→ {keep.name}\n")

        # ---------- 派生 ----------
        made = []
        for o in cfg.get("outputs", []):
            key = o["placement"]
            spec = specs["placements"].get(key)
            if spec is None:
                sys.exit(f"[未知投放位] {key}")
            if spec.get("enabled") is False:
                print(f"  – {key} 已停用（enabled: false），跳过")
                continue
            dst = outdir / o.get("file", f"{cfg.get('slug','video')}__{key}.mp4")
            subs = subs_for(spec, cfg.get("subtitles"))
            derive(master, dst, spec, mode, pad_hex, vbr, abr, subs, base)
            print(f"  ✓ {dst.name}  [{key}]  {spec['aspect_ratio']} "
                  f"{spec['width']}x{spec['height']}  字幕 {len(subs)} 层")
            made.append(dst)

        if not made:
            print("\n没有产出任何成片 —— 检查配置里的 outputs")
            sys.exit(2)

        # ---------- 校验 ----------
        # 先 flush，否则子进程的输出会插到本脚本的日志前面，读起来是倒的
        print("\n" + "=" * 60, flush=True)
        sys.stdout.flush()
        checker = Path(__file__).resolve().parent / "视频规格校验.py"
        r = subprocess.run([sys.executable, str(checker), str(outdir),
                            "--specs", str(sp)],
                           text=True, encoding="utf-8", errors="replace")
        sys.exit(r.returncode)

    finally:
        if args.keep_temp:
            print(f"\n中间文件保留在 {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv)>1 and sys.argv[1].startswith("r03-"):raise SystemExit(r03_cli(sys.argv[1:]))
    main()
