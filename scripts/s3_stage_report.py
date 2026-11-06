"""Render the existing S3 report location from current evidence, with a hash receipt."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import yaml

HOME=Path(__file__).resolve().parents[1]
from stage_artifacts import role_path
MD=role_path(HOME,'S3','report_md')
HTML=role_path(HOME,'S3','report_html')
PROOF='S3_视觉板块/_阶段总览/视觉报告生成记录.json'
SOURCES=['00_产品资料/产品使用说明书.yaml','00_产品资料/说明书确认.json','00_产品资料/拍摄清单.md',
    'S3_视觉板块/amazon-listing-image-designer/素材盘点.yaml',
    'S3_视觉板块/amazon-listing-image-designer/visual_suite.yaml',
    'S3_视觉板块/amazon-listing-image-designer/视觉规划确认.json',
    'S3_视觉板块/出图_主副图/media_manifest.yaml','S3_视觉板块/出图_高级A+/media_manifest.yaml',
    'S3_视觉板块/出图_普通A+/media_manifest.yaml','S3_视觉板块/amazon-product-video-director/video_generation_manifest.yaml',
    'S3_视觉板块/视频_即梦/media_manifest.yaml','S3_视觉板块/出图_高级A+/02_回填/最终版/A+上传清单_校验.json']


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):return yaml.safe_load(path.read_bytes()) or {} if path.is_file() else {}


def helper(path,name):
    spec=importlib.util.spec_from_file_location('_s3report_'+name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def evidence(root):
    value={name:sha(root/name) if (root/name).is_file() else None for name in SOURCES}
    flow=load(root/'workflow_state.yaml').get('s3_execution') or {}
    video=next((row for row in reversed(flow.get('records') or []) if row.get('step')=='video_production'),{})
    value['video_choice']={key:video.get(key) for key in ['outcome','receipt_id']}
    return value


def generate(root):
    root=Path(root).resolve();renderer=helper(HOME/'skills/github-tabler-html-report/assets/salon_report.py','renderer')
    nav=helper(HOME/'scripts/s3_navigation.py','nav').summary(root,HOME)
    manual=load(root/SOURCES[0]);plan=load(root/SOURCES[4]);inventory=load(root/SOURCES[3]);state=load(root/'workflow_state.yaml')
    flow=state.get('s3_execution') or {};records={r.get('step'):r for r in flow.get('records') or []}
    video=records.get('video_production') or {};pending=video.get('outcome')=='deferred'
    all_images=bool(nav['counts']) and all(v['accepted']==v['expected'] and v['expected'] for v in nav['counts'].values())
    status='图片已验收；视频待补' if all_images and pending else '图片已验收' if all_images else '未完成验收'
    report=renderer.Report('S3 · 视觉交付总览',stage='S3');md=['# S3 · 视觉交付总览','',status,'',nav['text'] or '尚无已确认规划','']
    section=report.section('overview','这次交付到哪里')
    section.kpis([('图片',status,None,'blue'),('素材',str(len(inventory.get('assets') or [])),None,'blue'),
                  ('待补画面',str(len(inventory.get('gaps') or [])),None,'warn')])
    section.callout('info',renderer.esc(nav['text'] or '尚无已确认规划；先完成产品理解与规划确认。'))
    section=report.section('product','产品理解与证据')
    rows=[[renderer.esc(part.get('name',part.get('id','部件'))),renderer.esc(str(part.get('description') or '按已确认说明书核对'))]
          for part in manual.get('structure') or []]
    section.table(['部件','核对依据'],rows or [['尚未完成','先查看产品使用说明书并处理来源冲突']])
    section.callout('info','产品结构、状态和动作均以已确认说明书为准；素材缺失保留为待补，不补造事实。')
    report.section('plan','每张图解决什么问题').table(['图位','买家问题','图上文案'],[
        [renderer.esc(slot['slot_id']),renderer.esc(slot.get('buyer_question') or slot.get('task') or '见已确认规划'),
         renderer.esc(slot.get('on_image_copy') or '无图上文案')] for slot in plan.get('slots') or []])
    quality=[]
    for group in ['出图_主副图','出图_高级A+','出图_普通A+']:
        for row in load(root/'S3_视觉板块'/group/'media_manifest.yaml').get('items') or []:
            proof=row.get('final_audit') or {}
            quality.append([renderer.esc(row.get('slot_id','')),renderer.esc({'black':'黑色','khaki':'卡其色'}.get(row.get('variant'),row.get('variant',''))),
                            '已写入并读回' if proof.get('needs_ai_tag') else '无须标记（按逐图复核）',
                            '见当前最终版与验收记录，改动后须重验'])
    report.section('quality','图片验收与人物标记').table(['图位','颜色','AI 人物标记','状态'],quality or [['待回填','—','尚未判断','未完成验收']])
    gaps=inventory.get('gaps') or []
    report.section('gaps','缺哪些画面，怎样补').table(['缺口','可选处理'],[
        [renderer.esc(gap.get('description') or gap.get('tag','待补证据')),'自己拍 / 找厂家 / 先用现有素材继续'] for gap in gaps] or [['盘点未记录缺口','仍以逐图验收为准']])
    report.section('video','视频完成度').callout('info','视频待补；S4 不把 SB 视频广告标为可用。' if pending else
        '视频按回填及验收记录显示；未验收的视频不授予广告可用资格。')
    section=report.section('handoff','下一步与需要你决定的事')
    section.callout('info','图片与其他必需项通过后生成正常交接口令；视频后补列明影响。品牌故事 / 旗舰店有需要可再做。')
    section.callout('info',renderer.esc('建议补进 Q&A：把买家关心的展开、收纳、装载和适配问题交给 S2 复核；这里不自动改文案。'))
    section.table(['入口','用途'],[[renderer.esc(item['label']),'见导航对应入口'] for item in nav['links']])
    for title,items in [('产品理解',[p.get('name',p.get('id','部件')) for p in manual.get('structure') or []]),
                        ('待补画面',[g.get('description',g.get('tag','待补证据')) for g in gaps])]:
        md+=['## '+title,'']+['- '+str(item) for item in items]+['']
    md+=['## 下一步','','必需项通过后换新会话进入 S4；视频待补时不把 SB 视频广告标为可用。',
         '品牌故事 / 旗舰店有需要可再做。建议补进 Q&A 的问题交给 S2 复核，不自动修改。','']
    content=report.render().replace('</head>','<meta name="starsky-report" content="r03-s3">\n</head>')
    for relative,text in [(MD,'\n'.join(md)),(HTML,content)]:
        target=root/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf8')
    proof={'schema_version':'r03.s3.report','generator_sha256':sha(__file__),'sources':evidence(root),
           'outputs':{name:sha(root/name) for name in [MD,HTML]},'status':status,'business_accepted':False}
    (root/PROOF).write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return {'md':MD,'html':HTML,'proof':PROOF,'status':status}


def verify_report_file(path):
    path=Path(path).resolve();root=path.parents[3];errors=[]
    try:
        if root/HTML!=path:raise ValueError('报告位置与正式入口不一致')
        proof=json.loads((root/PROOF).read_bytes())
        if proof.get('schema_version')!='r03.s3.report' or proof.get('generator_sha256')!=sha(__file__):errors.append('报告生成器或记录已变化')
        if proof.get('sources')!=evidence(root):errors.append('说明书、规划或媒体证据已变化，请重新生成报告')
        if proof.get('outputs')!={name:sha(root/name) for name in [MD,HTML]}:errors.append('报告文件已变化')
    except (OSError,ValueError,KeyError,TypeError) as error:errors.append(str(error))
    return {'ok':not errors,'errors':errors,'business_accepted':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path);args=parser.parse_args()
    print(json.dumps(generate(args.root),ensure_ascii=False))
