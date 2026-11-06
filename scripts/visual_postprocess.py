"""R03 local image preparation and actual OCR; generation is never acceptance."""
from __future__ import annotations
from difflib import SequenceMatcher
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import unicodedata

import numpy as np
from PIL import Image,ImageOps,PngImagePlugin
import yaml

HOME=Path(__file__).resolve().parents[1]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rules():return yaml.safe_load((HOME/'templates/amazon_visual_platform_rules.yaml').read_bytes())


def pixel_sha(path):
    with Image.open(path) as image:
        image=image.convert('RGB')
        return hashlib.sha256(str(image.size).encode()+image.tobytes()).hexdigest()


def normalize_image(source,target,kind,*,dimensions=None):
    source,target=Path(source).resolve(),Path(target).resolve()
    if source==target:raise ValueError('原图必须保留；后处理写入候选最终文件，不能覆盖回填原件')
    config=rules()['image_targets']
    if kind not in {'main','secondary','aplus_desktop','aplus_mobile','standard_aplus'}:raise ValueError('未知图位类型')
    if kind=='standard_aplus' and (not isinstance(dimensions,list) or len(dimensions)!=2 or not all(type(n) is int and 100<=n<=5000 for n in dimensions)):
        raise ValueError('普通 A+ 须沿用已确认标准模块尺寸，不能套高级双端尺寸')
    before=sha(source)
    with Image.open(source) as opened:
        original_info=dict(opened.info)
        image=ImageOps.exif_transpose(opened).convert('RGBA')
        image=Image.alpha_composite(Image.new('RGBA',image.size,'white'),image).convert('RGB')
        exif=opened.getexif();exif.pop(274,None)
    size=tuple(dimensions if kind=='standard_aplus' else config[kind]);bbox=None
    if kind=='main':
        pixels=np.asarray(image);border=np.concatenate((pixels[0],pixels[-1],pixels[:,0],pixels[:,-1]))
        background=np.median(border,axis=0)
        if min(background)<210 or max(background)-min(background)>14:
            raise ValueError('主图背景不明确，不能自动抠掉产品；请返修为白底完整产品')
        distances=np.max(abs(pixels.astype(np.int16)-background),axis=2)
        possible=(distances<30).astype(np.uint8)
        import cv2
        padded=np.pad(possible,1,constant_values=1)
        flood=padded.copy();cv2.floodFill(flood,None,(0,0),2)
        background_mask=flood[1:-1,1:-1]==2
        foreground=~background_mask
        ys,xs=np.where(foreground)
        if len(xs)<4:raise ValueError('主图没有可识别的完整产品，先返修')
        box=(int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1)
        if box[0]==0 or box[1]==0 or box[2]==image.width or box[3]==image.height:
            raise ValueError('主图主体触边，无法确认产品完整，先返修')
        rgba=np.concatenate((pixels,np.where(background_mask,0,255).astype(np.uint8)[...,None]),axis=2)
        product=Image.fromarray(rgba).crop(box)
        scale=min(size[0]*config['main_product_ratio']/product.width,size[1]*config['main_product_ratio']/product.height)
        product=product.resize((max(1,round(product.width*scale)),max(1,round(product.height*scale))),Image.Resampling.LANCZOS)
        canvas=Image.new('RGB',size,'white');x=(size[0]-product.width)//2;y=(size[1]-product.height)//2
        canvas.paste(product,(x,y),product);bbox=[x,y,x+product.width,y+product.height]
    else:
        fitted=ImageOps.contain(image,size,Image.Resampling.LANCZOS)
        canvas=Image.new('RGB',size,'white');canvas.paste(fitted,((size[0]-fitted.width)//2,(size[1]-fitted.height)//2))
    target.parent.mkdir(parents=True,exist_ok=True)
    options={}
    if exif:options['exif']=exif.tobytes()
    if original_info.get('icc_profile'):options['icc_profile']=original_info['icc_profile']
    if target.suffix.lower()=='.png':
        info=PngImagePlugin.PngInfo()
        packet=original_info.get('XML:com.adobe.xmp')
        if packet:info.add_itxt('XML:com.adobe.xmp',packet)
        options['pnginfo']=info
    elif original_info.get('xmp'):options['xmp']=original_info['xmp']
    canvas.save(target,quality=95,subsampling=0,**options)
    return {'source_sha256':before,'output_sha256':sha(target),'pixel_sha256':pixel_sha(target),
            'dimensions':list(size),'product_bbox':bbox,'operation':'normalize','background_rgb':[255,255,255]}


@lru_cache(maxsize=1)
def _ocr_engine():
    import rapidocr
    from rapidocr import RapidOCR
    models=Path(rapidocr.__file__).parent/'models'
    names={'Det':'PP-OCRv6_det_small.onnx','Rec':'PP-OCRv6_rec_small.onnx','Cls':'ch_ppocr_mobile_v2.0_cls_mobile.onnx'}
    for name in names.values():
        if not (models/name).is_file():raise ValueError('本地 OCR 模型缺失，请用正式安装器补依赖；不自动下载模型或假报通过')
    params={'EngineConfig.onnxruntime.intra_op_num_threads':1,'EngineConfig.onnxruntime.inter_op_num_threads':1,'Global.log_level':'error'}
    params.update({key+'.model_path':str(models/name) for key,name in names.items()})
    return RapidOCR(params=params),{name:sha(models/name) for name in names.values()}


@lru_cache(maxsize=128)
def _recognize(path,pixels):
    engine,models=_ocr_engine()
    result=engine(Path(path))
    return {'status':'pass','lines':list(result.txts or []),'scores':list(result.scores or []),
            'boxes':result.boxes.tolist() if result.boxes is not None else [],
            'engine':'RapidOCR 3.10.0 / local PP-OCRv6','models_sha256':models,'pixel_sha256':pixels}


def recognize_text(path):
    try:
        result=_recognize(str(Path(path).resolve()),pixel_sha(path))
        return {**result,'image_sha256':sha(path)}
    except (ImportError,OSError,ValueError,RuntimeError) as error:
        return {'status':'unavailable','lines':[],'errors':['OCR 未实测通过：'+str(error)]}


def _tokens(value):
    value=unicodedata.normalize('NFKC',value).casefold().replace('’',"'").replace('‘',"'").replace('“','"').replace('”','"')
    return re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]|[^\w\s]',value)


def compare_copy(expected_lines,ocr):
    if ocr.get('status')!='pass':return {'status':'needs_review','errors':ocr.get('errors') or ['OCR 不可用，不能视为空白或通过']}
    expected=_tokens(' '.join(expected_lines));actual=_tokens(' '.join(ocr['lines']))
    differences=[]
    for operation,i,j,k,l in SequenceMatcher(a=expected,b=actual,autojunk=False).get_opcodes():
        if operation!='equal':differences.append({'expected':expected[i:j],'actual':actual[k:l]})
    low=[score for score in ocr.get('scores') or [] if score<rules()['image_targets']['ocr_confidence']]
    return {'status':'needs_review' if low else 'needs_revision' if differences else 'pass',
            'differences':differences,'low_confidence':len(low),
            'errors':['OCR 置信度不足，需视觉复核；保留识别差异，不直接判为错字或通过'] if low else ['图上文案与规划有多字/漏字/错字，请按差异返修'] if differences else []}


def _module(path,name):
    spec=importlib.util.spec_from_file_location('_postprocess_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def _safe(root,path):
    result=(Path(root)/path).resolve();result.relative_to(Path(root).resolve());return result


def read_review(root,reference):
    if not reference:return {},None
    if isinstance(reference,dict):
        data=json.loads(_safe(root,reference['path']).read_bytes())
        selector=reference.get('selector')
        if not isinstance(selector,str) or not selector.startswith('/'):raise ValueError('视觉复核引用缺有效 JSON Pointer')
        for part in selector[1:].split('/'):
            part=part.replace('~1','/').replace('~0','~');data=data[int(part)] if isinstance(data,list) else data[part]
        fingerprint=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    else:
        path=_safe(root,reference);data=json.loads(path.read_bytes());fingerprint=sha(path)
    if not isinstance(data,dict):raise ValueError('视觉复核必须是结构化回执')
    return data,fingerprint


def _roi(image,box):
    if (not isinstance(box,list) or len(box)!=4 or not all(isinstance(n,int) for n in box)
            or not 0<=box[0]<box[2]<=image.width or not 0<=box[1]<box[3]<=image.height):
        raise ValueError('缺有效的产品取色区域，不能用整图背景冒充产品颜色')
    return image.crop(box)


def _color_mean(path,box):
    # Reuse the established script's CIELab conversion, without touching pixels.
    color=_module(HOME/'scripts/产品色彩审计.py','color')
    with Image.open(path) as image:sample=_roi(image.convert('RGB'),box).resize((32,32))
    values=[color.rgb_to_lab(rgb) for rgb in color.pixels(sample) if min(rgb)<245]
    if not values:raise ValueError('产品取色区域只有白色背景，需重新指定')
    return np.mean(values,axis=0)


def inspect_image(root,path,slot_id,variant,device,review_path=None,*,reference=None,final_proof=None):
    root=Path(root).resolve();path=_safe(root,path);errors=[];checks={};config=rules()['image_targets']
    try:
        planner=_module(HOME/'skills/_visual_suite_contract/scripts/visual_suite.py','planner')
        plan=planner.load_plan(root)
        manual=_module(HOME/'scripts/product_manual.py','manual').read_confirmed(root)
        listing=[slot for slot in plan['slots'] if slot['slot_id']==slot_id]
        modules=[slot for slot in plan['aplus_slots'] if slot['slot_id']==slot_id]
        brand_slot=None;brand_hash=None
        if not listing and not modules and '::' in slot_id:
            brand=_module(HOME/'skills/_brand_visual_contract/scripts/brand_preview.py','brand_slot')
            brand_slot,brand_hash=brand.r03_read_slot(root,slot_id)
        if len(listing)+len(modules)+bool(brand_slot)!=1:raise ValueError('图位不在当前唯一规划中')
        slot=brand_slot or (listing or modules)[0]
        if modules and device=='mobile' and plan.get('aplus_production_version')=='r03.m2' and not planner.aplus_mobile_ready(root,plan):raise ValueError('先验收桌面版式，再回填独立构图的手机版')
        if variant not in plan['variants']:raise ValueError('颜色不在本次确认范围')
        kind='main' if listing and slot_id=='IMG01' else 'secondary' if listing else 'standard_aplus' if brand_slot or plan['aplus_path']=='standard' else 'aplus_'+device
        expected_size=slot.get('canvas') if kind=='standard_aplus' else config.get(kind)
        if not isinstance(expected_size,list) or len(expected_size)!=2:raise ValueError('图位设备/模块尺寸无效')
        with Image.open(path) as image:
            image=image.convert('RGB');width,height=image.size
            if [width,height]!=expected_size:errors.append('实际尺寸不符：应为 '+str(expected_size))
            if kind=='main':
                if any(image.getpixel((x,y))!=(255,255,255) for x,y in [(0,0),(width-1,0),(0,height-1),(width-1,height-1)]):errors.append('主图背景须为 RGB 255 纯白')
                pixels=np.asarray(image);ys,xs=np.where(np.min(pixels,axis=2)<245)
                ratio=max(xs.max()-xs.min()+1,ys.max()-ys.min()+1)/width if len(xs) else 0
                if not .80<=ratio<=.90:errors.append('主图产品占比偏离约85%，请重做白底后处理')
        review,review_hash=read_review(root,review_path)
        pixels=pixel_sha(path)
        manual_hash=sha(root/'00_产品资料/说明书确认.json');plan_hash=sha(planner.folder(root)/'视觉规划确认.json')
        if not review:
            errors.append('缺实际视觉复核：逐项核对说明书结构、道具、颜色与人物，不能把 OCR 通过当成结构通过')
        else:
            if any(review.get(key)!=value for key,value in [('slot_id',slot_id),('variant',variant),('device',device)]):errors.append('视觉复核对应的图位/颜色/设备不一致')
            if review.get('source') not in {'user','model_visual'} or not str(review.get('message') or '').strip():errors.append('视觉复核缺实际来源或说明')
            if review.get('source')=='model_visual' and not review.get('task_id'):errors.append('模型视觉复核缺当前任务证据')
            if review.get('pixel_sha256')!=pixels:errors.append('视觉复核像素指纹已变化，请重新看当前图片')
            if review.get('manual_confirmation_sha256')!=manual_hash or review.get('plan_confirmation_sha256')!=plan_hash:errors.append('说明书或规划已变化，旧视觉复核失效')
            if final_proof:
                if (final_proof.get('final_sha256')!=sha(path) or final_proof.get('reviewed_image_sha256')!=review.get('image_sha256')
                        or final_proof.get('review_sha256')!=review_hash or final_proof.get('validator_sha256')!=sha(__file__)
                        or final_proof.get('final_basename')!=path.name):
                    errors.append('最终文件、复核或验收器已变化，需要重新验收')
            elif review.get('image_sha256')!=sha(path):errors.append('视觉复核未绑定当前候选文件')
            if brand_slot and (review.get('brand_contract_sha256')!=brand_hash or review.get('product_present') is not brand_slot['product_visible']):errors.append('品牌资料/规划已变化或未核对产品是否可见，需重新复核')
            required=set(slot.get('structure_ids') or [])
            for part in manual.get('structure') or []:
                item=(review.get('structure') or {}).get(part['id']) or {}
                allowed={'pass'} if part['id'] in required else {'pass','not_visible'}
                if item.get('status') not in allowed or not item.get('note'):errors.append('结构待复核：'+part.get('name',part['id']))
            for key,label in [('no_extra_parts','是否新增无依据结构'),('variant_layout','图位/版式/文案是否一致'),('source_color_match','本品来源颜色是否一致')]:
                if review.get(key) is not True:errors.append('视觉复核未通过：'+label)
            if not isinstance(review.get('props_present'),bool):errors.append('未核对是否存在不附送道具')
            if not isinstance(review.get('has_photorealistic_human'),bool):errors.append('写实人物/手尚未识别，需视觉复核')
        expected=[slot['on_image_copy']] if slot.get('on_image_copy') else []
        props=bool(slot.get('props')) or review.get('props_present') is True
        if props and kind!='main' and 'toolsnotincluded' not in re.sub(r'\W+','',' '.join(expected).casefold()):expected.append('Tools not included')
        if props and kind=='main':errors.append('主图出现不附送道具，请返修')
        ocr=recognize_text(path);copy=compare_copy(expected,ocr);checks['copy']=copy;checks['ocr']=ocr
        errors.extend(copy['errors'])
        if modules and device=='mobile' and plan.get('aplus_production_version')=='r03.m2':
            if any((max(p[1] for p in box)-min(p[1] for p in box))*config['mobile_preview_width']/width<config.get('aplus_text_min_preview_height',16) for box in ocr.get('boxes') or []):errors.append('手机版图上小字不足约16px预览高度，需独立重排放大')
        scanner=_module(HOME/'scripts/compliance_scan.py','compliance')
        scanned=scanner.scan_fields({'visible_text':'\n'.join(ocr.get('lines') or [])},series=Path(plan.get('series_root') or root),
            evidence={'sku':(plan.get('sku_ids') or {}).get(variant,variant),'marketplace':plan.get('marketplace','US')})
        checks['compliance']=scanned
        if scanned['blocked']:errors.append('图上文字命中合规 block，必须返修，不能降为提示')
        if props and kind!='main':
            disclaimer_boxes=[box for line,box in zip(ocr.get('lines') or [],ocr.get('boxes') or []) if 'toolsnotincluded' in re.sub(r'\W+','',line.casefold())]
            readable=any((max(p[1] for p in box)-min(p[1] for p in box))*config['mobile_preview_width']/width>=config['disclaimer_min_preview_height'] for box in disclaimer_boxes)
            if not readable or review.get('disclaimer_readable') is not True:errors.append('免责小字缺失或手机不可读，请加大到可读并复核')
        if review and kind!='main' and not (brand_slot and brand_slot['product_visible'] is False and review.get('product_present') is False):
            if not reference:errors.append('缺该变体主图色彩基准，不能拿其他变体或整图背景代替')
            else:
                references={reference['variant']:reference} if 'path' in reference else reference
                regions=review.get('color_regions') or [{'variant':variant,'roi':review.get('product_roi')}]
                color_checks=[]
                for region in regions:
                    ref=references.get(region['variant'])
                    if not ref:raise ValueError('缺变体主图色彩基准：'+region['variant'])
                    ref_path=_safe(root,ref['path']);baseline=_color_mean(ref_path,ref['roi']);current=_color_mean(path,region['roi'])
                    delta_ab=float(np.linalg.norm(current[1:]-baseline[1:]));delta_l=abs(float(current[0]-baseline[0]))
                    failed=delta_ab>config['color_delta_ab_max'] or (np.linalg.norm(baseline[1:])<5 and delta_l>config['neutral_color_delta_l_max'])
                    color_checks.append({'variant':region['variant'],'reference_sha256':sha(ref_path),'delta_ab':delta_ab,'delta_l':delta_l,'ok':not failed})
                    if failed:errors.append('变体产品颜色与本变体主图不符：'+region['variant'])
                checks['color']=color_checks
        origin=review.get('generation_origin')
        if review.get('has_photorealistic_human') and origin not in {'ai_generated','real_photo'}:errors.append('人物来源未明确：确认 AI 生成或真实拍摄，不能默认当作无需标记')
        needs_tag=review.get('has_photorealistic_human') is True and origin=='ai_generated'
        if final_proof and needs_tag:
            marker=_module(HOME/'scripts/ai_person_tag.py','marker')
            if not marker.has_tag(path):errors.append('最终文件缺 AI 人物 XMP 标记，后处理后必须最后写入并读回')
        return {'status':'needs_revision' if errors else 'pass' if final_proof else 'ready_to_finalize',
            'errors':list(dict.fromkeys(errors)),'checks':checks,'needs_ai_tag':needs_tag,
            'manual_confirmation_sha256':manual_hash,'plan_confirmation_sha256':plan_hash,
            'image_sha256':sha(path),'pixel_sha256':pixels,'review_sha256':review_hash,'brand_contract_sha256':brand_hash}
    except (OSError,ValueError,KeyError,TypeError,IndexError) as error:
        return {'status':'needs_revision','errors':list(dict.fromkeys(errors+[str(error)])),'checks':checks}


def finalize_image(root,path,slot_id,variant,device,review_path,*,reference=None):
    checked=inspect_image(root,path,slot_id,variant,device,review_path,reference=reference)
    if checked['status']!='ready_to_finalize':raise ValueError('图片尚不能交付：'+'；'.join(checked['errors']))
    before=sha(path)
    marker=_module(HOME/'scripts/ai_person_tag.py','final_marker')
    if checked['needs_ai_tag']:marker.tag(path)
    # Tagging is the last content operation; subsequent publication may move
    # these exact bytes without renaming the already selected basename.
    if pixel_sha(path)!=checked['pixel_sha256']:raise ValueError('写元数据改变了图像像素，拒交')
    proof={'status':'pass','slot_id':slot_id,'variant':variant,'device':device,
        'reviewed_image_sha256':before,'review_sha256':checked['review_sha256'],
        'pixel_sha256':checked['pixel_sha256'],'final_sha256':sha(path),'final_basename':Path(path).name,
        'validator_sha256':sha(__file__),'manual_confirmation_sha256':checked['manual_confirmation_sha256'],
        'plan_confirmation_sha256':checked['plan_confirmation_sha256'],
        'operations':['normalize','actual_ocr','compliance','structure_review','variant_color','xmp_final' if checked['needs_ai_tag'] else 'xmp_no_person_or_real_photo'],
        'checks':checked['checks'],'needs_ai_tag':checked['needs_ai_tag'],'brand_contract_sha256':checked.get('brand_contract_sha256')}
    final=inspect_image(root,path,slot_id,variant,device,review_path,reference=reference,final_proof=proof)
    if final['status']!='pass':raise ValueError('最终读回未通过：'+'；'.join(final['errors']))
    return proof


def media_folder(root,kind,plan):
    name='出图_主副图' if kind=='listing' else '出图_高级A+' if plan['aplus_path']=='premium' else '出图_普通A+'
    return Path(root)/'S3_视觉板块'/name


def verify_media_manifest(root,manifest,kind,*,only_devices=None):
    """Re-run actual gates; a handwritten approved flag is never sufficient."""
    root=Path(root).resolve();errors=[];checked=[]
    try:
        planner=_module(HOME/'skills/_visual_suite_contract/scripts/visual_suite.py','manifest_planner')
        plan=planner.load_plan(root);data=yaml.safe_load(_safe(root,manifest).read_bytes())
        if data.get('schema_version')!='r03.s3':raise ValueError('新 S3 媒体缺原生后处理/复核回执，不接受仅写 approved')
        rows=data.get('items') or []
        slots=plan['slots'] if kind=='listing' else plan['aplus_slots']
        variants=plan['variants'] if kind=='listing' else [plan.get('aplus_variant') or plan['variants'][0]]
        devices=['listing'] if kind=='listing' else ['desktop'] if plan['aplus_path']=='standard' else ['desktop','mobile']
        if only_devices is not None:
            if not only_devices or not set(only_devices)<=set(devices):raise ValueError('验收设备范围无效')
            devices=list(only_devices);rows=[row for row in rows if row.get('device') in devices]
        expected={(slot['slot_id'],variant,device) for slot in slots for variant in variants for device in devices}
        actual=[(row.get('slot_id'),row.get('variant'),row.get('device')) for row in rows]
        if set(actual)!=expected or len(actual)!=len(expected):errors.append('最终版图位/颜色/双端数量不完整或有重复：'+str(len(rows))+'/'+str(len(expected)))
        listing_rows=rows if kind=='listing' else (yaml.safe_load((media_folder(root,'listing',plan)/'media_manifest.yaml').read_bytes()).get('items') or [])
        references={row['variant']:{'variant':row['variant'],'path':row['output_path'],'roi':(row.get('normalization') or {}).get('product_bbox')}
                    for row in listing_rows if row.get('slot_id')=='IMG01' and row.get('final_audit')}
        for row in rows:
            identity=(row.get('slot_id'),row.get('variant'),row.get('device'))
            if identity not in expected:continue
            if not row.get('final_audit') or not row.get('review_ref'):
                errors.append(str(identity)+' 缺结构复核及最终处理回执');continue
            source=_safe(root,row['source_return_path'])
            if sha(source)!=(row.get('normalization') or {}).get('source_sha256'):errors.append(str(identity)+' 回填来源已变化')
            target=_safe(root,row['output_path']);target.relative_to((media_folder(root,kind,plan)/'02_回填/最终版').resolve())
            if row.get('sha256')!=sha(target):errors.append(str(identity)+' 成品指纹不一致')
            result=inspect_image(root,target,*identity,row['review_ref'],reference=references,final_proof=row['final_audit'])
            checked.append({'identity':identity,'status':result['status']})
            errors.extend(str(identity)+' '+error for error in result.get('errors') or [])
        return {'status':'fail' if errors else 'pass','errors':errors,'items':checked,'final_accepted':not errors,'item_count':len(rows)}
    except (OSError,ValueError,KeyError,TypeError) as error:
        return {'status':'fail','errors':errors+[str(error)],'final_accepted':False}
