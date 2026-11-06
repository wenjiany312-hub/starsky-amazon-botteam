#!/usr/bin/env python3
"""R03 video sheet/export, adapted from the supplied reference implementation.

OpenCV decodes local video on Windows/macOS without requiring shell ffmpeg.
Contact sheets carry source timestamps; exports record source/frame hashes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from PIL import Image, ImageDraw


def _capture(video):
    try:
        import cv2
    except ImportError as error:
        raise ValueError('缺少本地视频解码依赖 opencv-python-headless，请用正式安装器补齐') from error
    cap = cv2.VideoCapture(str(Path(video).resolve()))
    if not cap.isOpened():
        cap.release()
        raise ValueError('无法解码视频；保留原文件并检查格式或解码依赖：'+str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    if not math.isfinite(fps) or fps <= 0 or not math.isfinite(frames) or frames <= 0:
        cap.release();raise ValueError('视频没有有效时长或帧率')
    return cv2, cap, fps, int(frames)


def _frame(cv2, cap, timestamp, fps, count):
    number = int(round(timestamp*fps))
    if timestamp < 0 or number >= count:
        raise ValueError('截帧秒数超出视频范围：'+str(timestamp))
    cap.set(cv2.CAP_PROP_POS_FRAMES, number)
    ok, frame = cap.read()
    if not ok: raise ValueError('视频在此秒数解码失败：'+str(timestamp))
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), number/fps


def _crop(video):
    """Stable dark borders only; ambiguous/fully dark videos retain full frame."""
    import numpy as np
    cv2, cap, fps, count = _capture(video)
    boxes = []
    try:
        for fraction in (.1, .25, .5, .75, .9):
            image,_ = _frame(cv2,cap,min((count-1)/fps,count/fps*fraction),fps,count)
            pixels=np.asarray(image)
            active=np.max(pixels,axis=2)>20
            rows=np.flatnonzero(active.mean(axis=1)>.15)
            cols=np.flatnonzero(active.mean(axis=0)>.15)
            if len(rows) and len(cols):boxes.append((cols[0],rows[0],cols[-1]+1,rows[-1]+1))
        if len(boxes)<3:return ''
        # Union protects details that appear only in one sampled state.
        left=min(b[0] for b in boxes);top=min(b[1] for b in boxes)
        right=max(b[2] for b in boxes);bottom=max(b[3] for b in boxes)
        width,height=image.size
        if left<8:left=0
        if top<8:top=0
        if width-right<8:right=width
        if height-bottom<8:bottom=height
        w=width if left==0 and right==width else max(16,int(right-left)//16*16)
        h=height if top==0 and bottom==height else max(16,int(bottom-top)//16*16)
        x=max(0,round((left+right-w)/2));y=max(0,round((top+bottom-h)/2))
        if (w,h)==(width,height):return ''
        return f'crop={w}:{h}:{x}:{y}'
    finally:cap.release()


def _box(crop):
    if not crop:return None
    w,h,x,y=map(int,crop.removeprefix('crop=').split(':'))
    return x,y,x+w,y+h


def _proof(video,out,crop,frames,files,mode):
    document={'schema_version':1,'mode':mode,'source':str(Path(video).resolve()),
        'source_sha256':hashlib.sha256(Path(video).read_bytes()).hexdigest(),'crop':crop,
        'frames':frames,'outputs':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        'scope':'real decoded frames; no AI generation or structural approval'}
    (Path(out)/(mode+'_evidence.json')).write_text(json.dumps(document,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def sheet(video,out,fps=2.0,per=24,cols=6):
    if not 0<fps<=30 or not 1<=per<=100 or not 1<=cols<=12:raise ValueError('抽帧参数无效')
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    crop=_crop(video);box=_box(crop);cv2,cap,source_fps,count=_capture(video)
    sheets=[];manifest=[];chunk=[];w=320;h=None
    try:
        for n in range(math.ceil(count/source_fps*fps)):
            requested=n/fps
            if round(requested*source_fps)>=count:break
            image,actual=_frame(cv2,cap,requested,source_fps,count)
            if box:image=image.crop(box)
            h=round(image.height*w/image.width)
            image=image.resize((w,h),Image.Resampling.LANCZOS)
            draw=ImageDraw.Draw(image);draw.rectangle((0,0,80,22),fill='black')
            draw.text((4,3),f'{actual:.2f}s',fill='yellow')
            chunk.append(image);manifest.append({'requested':requested,'actual':actual,'sheet':len(sheets)+1})
            if len(chunk)==per or n==math.ceil(count/source_fps*fps)-1:
                page=Image.new('RGB',(cols*w,math.ceil(len(chunk)/cols)*h),'white')
                for i,frame in enumerate(chunk):page.paste(frame,((i%cols)*w,(i//cols)*h))
                path=out/f'sheet_{len(sheets)+1:02d}.jpg';page.save(path,quality=88);sheets.append(path);chunk=[]
        if chunk:
            page=Image.new('RGB',(cols*w,math.ceil(len(chunk)/cols)*h),'white')
            for i,frame in enumerate(chunk):page.paste(frame,((i%cols)*w,(i//cols)*h))
            path=out/f'sheet_{len(sheets)+1:02d}.jpg';page.save(path,quality=88);sheets.append(path)
    finally:cap.release()
    _proof(video,out,crop,manifest,sheets,'sheet')
    return sheets


def export(video,out,marks,scale=1):
    if not 1<=scale<=4:raise ValueError('放大倍数必须在 1–4 之间；放大不增加真实细节')
    parsed=[]
    for mark in marks:
        value,name=mark.split('=',1);stamp=float(value)
        if not name or re.search(r'[<>:"/\\|?*\x00-\x1f]',name) or name in {'.','..'}:
            raise ValueError('截帧名称不能包含路径或非法字符')
        if not math.isfinite(stamp):raise ValueError('截帧秒数无效')
        parsed.append((stamp,name))
    out=Path(out);out.mkdir(parents=True,exist_ok=True);crop=_crop(video);box=_box(crop)
    cv2,cap,fps,count=_capture(video);files=[];frames=[]
    try:
        for stamp,name in parsed:
            image,actual=_frame(cv2,cap,stamp,fps,count)
            if box:image=image.crop(box)
            if scale>1:image=image.resize((image.width*scale,image.height*scale),Image.Resampling.LANCZOS)
            path=out/f'{name}_{stamp:.1f}s.jpg';image.save(path,quality=95);files.append(path)
            frames.append({'requested':stamp,'actual':actual,'path':path.name})
    finally:cap.release()
    _proof(video,out,crop,frames,files,'export');return files


def main():
    ap=argparse.ArgumentParser(description=__doc__);subs=ap.add_subparsers(dest='command',required=True)
    for name in ('sheet','export'):
        command=subs.add_parser(name);command.add_argument('video',type=Path);command.add_argument('out',type=Path)
        if name=='sheet':command.add_argument('--fps',type=float,default=2)
        else:command.add_argument('marks',nargs='+');command.add_argument('--scale',type=int,default=1)
    args=ap.parse_args()
    try:
        files=sheet(args.video,args.out,args.fps) if args.command=='sheet' else export(args.video,args.out,args.marks,args.scale)
        for path in files:print(path)
        return 0
    except (OSError,ValueError) as error:ap.exit(2,str(error)+'\n')


if __name__=='__main__':raise SystemExit(main())
