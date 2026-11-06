"""Fixed M00–M18 reader projection from the existing sealed stage model.

No new observations, scores, purchase permissions or supplier facts are inferred.
"""
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import re

from s1_report_v2 import cell, decision_headline
from s1_readability import reader_projection
from s1_criteria import criterion_text

TITLES = ['报告头 · 阶段 S1 选品可行性研究报告', '关键判定卡', '五问总决策表',
          '核心指标卡（每个数字必带口径）', '增长跑赢强制警示（双重基准）',
          '明确不做 / 唯一观察方向 / 硬门槛', '允许动作 / 禁止动作 / 当前最高授权预算',
          '结论翻转条件与下次复核', '市场容量与集中度', '成交价格带与价格分位',
          '竞品穿透（变体 / 上架日期分层 / 双时间窗）', '季节性与最晚下单日',
          '财务模型与允许成本情景', '供应链与成本核实', 'IP 与合规',
          '横向与纵向替代市场（只做候选，不作投资推荐）', '数据解释窗口（五栏）',
          '证据与口径索引', '验收自检']


def table(headers, rows):
    rows = list(rows)
    if not rows:
        return '待核：本次尚无对应数据；补取同口径原始证据后重算。\n'
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows]) + '\n'


def restore_modules(model, root, *, cost, ruler, product, inputs):
    root = Path(root)
    report = model['report_v2']
    old = model['sections']
    chapters = {x['id']: x for x in report['chapters']}
    market = {x['title']: x['body'] for x in chapters['market'].get('subsections', [])}
    facts = model['facts']; decision = report['decision_summary']
    label = decision_headline(decision)
    sections = {f'M{n:02}': {'title': title, 'body': old.get(f'M{n:02}', {}).get('body', '')}
                for n, title in enumerate(TITLES)}
    identity=model.get('plugin_identity') or {}
    host={'codex':'Codex','claude':'Claude','source_preview':'源码预览'}.get(identity.get('host'),'源码预览')
    sections['M00']['body']+='\n'+table(['运行插件来源','版本'],[(host,identity.get('version') or '版本未知')])
    def set_body(n, body):
        sections[f'M{n:02}']['body'] = body
    def gap(what, how):
        return '待核：' + what + '；补证方法：' + how + '。'
    def market_part(name):
        return '### ' + name + '\n\n' + (market.get(name) or gap(name+'证据不足', '补取站点、市场与评论来源'))

    set_body(1, '**研究结论：' + label + '**\n\n' + table(['判定项目', '本轮判断'], [
        ('当前研究范围', model['product_name']), ('主要原因', decision.get('conditions')),
        ('阶段业务验收', '正式门禁独立核验，研究暂缓不等于取证失败'),
        ('采购授权', '未授权，0' if not decision['purchase_authorized'] else '仅按已有用户授权记录执行'),
        ('下一步', '补齐下列翻转条件后复核，不自动下单或进入 S2')]))
    answers = report['front']['answers']
    set_body(2, '**本轮研究结论：' + label + '**\n\n' + table(['五问', '结论与关键数', '证据边界'], [
        ('是什么', model['product_name'], '所选产品 CT 与用户选款记录，见首屏和 M13'),
        ('为什么选', cell((facts.get('price_bands') or {}).get('p50'))+' 美元样本中位价；研究结论 '+label,
         '目标形态需求仍待核，不用整个类目的销量代替本品需求'),
        ('会不会侵权', '；'.join(answers[1]['metrics']) if len(answers)>1 else '待核', 'M14 逐件核查，未取得清权不等于不侵权'),
        ('好不好推', '；'.join(answers[2]['metrics']) if len(answers)>2 else '待核', '仅保本 CVR 与缺口，M12；数据不足，暂不判断'),
        ('赚不赚钱', '；'.join(answers[3]['metrics']) if len(answers)>3 else '待核', '主口径为条件测算，见 M12；不代表实际利润')]) +
        '\n'+market_part('为什么是这个站点'))
    # The site-choice evidence lives once, here; demand/capacity live in M08.
    set_body(3, sections['M03']['body']+'\n'+table(['指标','来源','时间','范围'], [
        ('价格分位', '已保存竞品详情，见 M17', model.get('evidence_date') or '待核：原回包日期', '同父体去重后的代表样本'),
        ('销量与评论', '工具回包及逐条评论', model.get('evidence_date') or '待核：回包日期', '工具估计与本次评论样本，不是全体市场'),
        ('成本/目标价', '本轮成本模型与汇率快照', cost.get('evaluation_date') or model.get('evidence_date'), '所选 SKU 的条件费用情景'),
        ('授权预算', '本轮用户授权记录', model.get('evidence_date'), '没有授权即为0，不按研究预算推定采购许可')]))
    criteria = facts.get('criteria') or {}
    growth = facts.get('growth_comparison') or (facts.get('market_analysis') or {}).get('growth_comparison') or {}
    set_body(4, table(['比较基准','本细分','基准增速','判断与补证'], [
        ('上级类目大盘', growth.get('category_growth'), growth.get('parent_growth'),
         growth.get('parent_result') or gap('缺同时间窗上级类目增速', '补取本细分及上级类目同时间窗销量')),
        ('站点大盘', growth.get('category_growth'), growth.get('marketplace_growth'),
         growth.get('marketplace_result') or gap('缺站点大盘增速', '取得站点同时间窗可比数据'))])+
        '\n双重基准未齐时无法证明增长跑赢；不能将旺季或整个盘子增长当成细分独有机会。'+
        '比较必须同时注明站点、起止月份和统计范围；分母变化、父体合并或样本增减都会影响同比，不用单月工具估值推出全年增长。')
    set_body(5, sections['M05']['body']+'\n唯一观察方向：'+model['product_name']+'；'+
        '以目标形态真实需求、可接受售价、完整含包装报价和权利核查作为共同硬门槛。\n\n'+
        table(['硬门槛','本轮边界'],[(str(k),criterion_text(v)) for k,v in criteria.items()
                                   if k in ('strongest_counterargument','pass_hold_stop','allowable_landed_cost','coverage_downgrade')]))
    budget = model['decision'].get('authorized_budget')
    if budget is None:budget=model['decision'].get('purchase_budget')
    set_body(6, table(['动作与授权','允许范围'], [
        ('允许动作','只读补证、索取正式报价、样品规格核查；付费动作须另行授权'),
        ('禁止动作','未经用户授权采购、付样品款、投广告、发布或上传 Listing'),
        ('当前最高授权预算', budget if budget is not None else '未授权，0'),
        ('采购授权','仅按已有用户记录执行' if decision['purchase_authorized'] else '未授权；预算不等于采购许可'),
        ('研究状态',label),('采购清单','完成清单不会新增采购授权')])+'\n'+chapters['purchase']['body'])
    base_date = model.get('evidence_date')
    next_date = (date.fromisoformat(base_date[:10])+timedelta(days=7)).isoformat() if base_date and re.fullmatch(r'\d{4}-\d{2}-\d{2}',base_date[:10]) else '待核：缺研究日期；补日期后安排7天复核'
    set_body(7, table(['翻转条件','如何补证','下次复核'], [
        ('目标价进入可接受价格带','同款市场证据、完整费用和售价情景重算',next_date),
        ('包装/入库/报价核实','同一 SKU 厂家书面报价与后台费用',next_date),
        ('权利与合规风险闭环','逐项检索及专业核查，见 M14',next_date),
        ('目标形态需求成立','独立需求和评论证据，见 M08 / M10',next_date)])+'\n'+chapters['risk']['body'])
    set_body(8, market_part('市场容量')+'\n'+market_part('需求与痛点')+'\n'+
        table(['集中度项目','本次事实'],[('头部集中度',(facts.get('market_analysis') or {}).get('concentration')),
                                      ('父体代表样本数',len(facts.get('representatives') or []))]))
    sections['M09']['body']+=('\n'+table(['父体代表','可见成交参考价（美元）','价格边界'],[
        (c.get('asin'),c.get('price_usd'),'工具可见页价；未核coupon、变体或结账价') for c in facts.get('representatives') or []])+
        '\n分位只描述本次去重样本；不同容量、材质或组合规格先分组，不把高端款售价直接当本款可接受价。目标价超出用户价带时保持暂缓，补足完整报价后重算。')
    competitors = facts.get('competitors') or []
    set_body(10, '### 变体穿透\n\n'+table(['ASIN','父体','规格/变体','价格','月销估计','评论','来源 / 日期'], [
        (c.get('asin'),c.get('parent_asin'),(c.get('raw') or {}).get('variations') or c.get('variation_count') or (c.get('raw') or {}).get('variation_count') or '待核：需变体回包',
         c.get('price_usd'),c.get('monthly_sales'),c.get('reviews'),str(c.get('source'))+' / '+str(c.get('observed_at'))) for c in competitors])+
        '\n### 上架日期分层\n\n'+table(['ASIN','上架日期','分层边界'],[(c.get('asin'),c.get('listing_date') or c.get('online_date'),
            '按原始日期分层；日期未取到不归入新品') for c in competitors])+
        '\n### 双时间窗（近 180 天 vs 近 365 天）\n\n'+table(['时间窗','销量口径','当前情况'],[
            ('近180天','同 ASIN 连续时间序列',gap('近180天逐月完整序列','补取同ASIN历史销量后比较')),
            ('近365天','同 ASIN 连续时间序列',gap('近365天逐月完整序列','补取同ASIN一年历史销量，不用单月乘12'))])+
        '\n### 头部基准与费用\n\n'+market_part('竞争格局')+'\n'+
        gap('竞品逐件后台费用未取得','按同一包装与计费时点核对FBA费用，不将本品费用套给竞品')+
        '\n\n'+market_part('切入点与机会'))
    sections['M10']['body']+='\n'+table(['ASIN','页面规格/标题','重量（克）','记录边界'],[
        (c.get('asin'),(c.get('raw') or {}).get('title') or c.get('title'),(c.get('raw') or {}).get('weight_g') or c.get('weight_g'),
         '原回包字段；未知净重/毛重属性不作推断') for c in competitors])
    set_body(11, table(['季节性与时点','当前证据','解释'],[
        ('月度趋势',facts.get('monthly_trend'),'按实际月份列出；未形成完整双窗时不硬算增长'),
        ('最晚下单日',criterion_text(criteria.get('latest_order_date')),'缺采购/生产/头程提前期时无法给出确定日期'),
        ('重算项','生产交期、运输天数、接收缓冲、旺季窗口','厂家与货代确认后重算')]))
    set_body(12, chapters['cost']['body']+'\n### 保本 CVR 与推广证据\n\n'+chapters['promotion']['body']+
        '\n推广证据不足时不作可推判断；本模块不另立研究结论。')
    set_body(13, chapters['product']['body']+'\n### 到岸成本逐项拆解\n\n'+
        table(['成本项','值','来源类型 / 待核'],[(r.get('label'),r.get('value'),r.get('source'))
            for r in ((cost.get('unified_cost') or {}).get('blocks',{}).get('landed',{}).get('rows') or [])])+
        '\n### 形态与结构风险\n\n'+gap('最终包装、盖板材质、支撑件和净重/毛重边界',
                '核对所选SKU，厂家确认并用样品验证结构、缝线、折叠与装配，不把页重当最终包规')+
        '\n### 做工与结构细节图\n\n')
    selected = product.get('selected_offer') or {}
    offer = str(selected.get('offer_id') or '') if isinstance(selected,dict) else str(selected)
    folder = root/'S1_选品板块/1688-product-detail-scraper/媒体'/offer/'详情图'
    evidence_root=root/'S1_选品板块/1688-product-detail-scraper/运行数据'
    observation_path=evidence_root/'05_image_observations.json'
    image_index=evidence_root/offer/'1688_detail_images.json'
    observations=json.loads(observation_path.read_bytes()).get('images',[]) if observation_path.is_file() else []
    descriptions={}
    for picture in folder.glob('*'):
        if not picture.is_file():continue
        digest=hashlib.sha256(picture.read_bytes()).hexdigest()
        exact=[];bound=[]
        for observation in observations:
            source=str(observation.get('source','')).replace('\\','/').split('/')
            if source[-2:]!=['详情图',picture.name]:continue
            if observation.get('sha256') and observation['sha256']!=digest:continue
            if len(source)>=3 and source[-3]==offer:exact.append(observation)
            elif len(source)==2 and observation.get('sha256')==digest:bound.append(observation)
        matches=exact or bound
        if matches:descriptions[picture.name]=matches[-1].get('text','')
    urls=json.loads(image_index.read_bytes()).get('urls',[]) if image_index.is_file() else []
    pictures=[]
    if offer.isdigit() and urls:
        for picture in sorted(folder.glob('*')):
            match=re.fullmatch(r'detail_(\d+)\.(?:jpg|jpeg|png|webp)',picture.name,re.I)
            if match and picture.is_file() and 0<int(match[1])<=len(urls) and re.search('折叠|支撑|粘扣|内布|盖板',descriptions.get(picture.name,'')):
                pictures.append(picture)
    for picture in pictures[:5]:
        relative = os.path.relpath(picture, root/'S1_选品板块/_阶段总览').replace('\\','/')
        index=int(re.search(r'\d+',picture.stem)[0])-1
        sections['M13']['body'] += '\n![所选 offer '+offer+' 详情图 '+picture.stem+'](<'+relative+'>)\n\n[查看大图](<'+relative+'>)\n\n观察：'+descriptions[picture.name]+'\n\n来源：[所选货源详情页](https://detail.1688.com/offer/'+offer+'.html) · [原详情图](<'+urls[index]+'>)，本地留存 '+picture.name+'；点击查看大图。\n'
        model['source_manifest'][picture.relative_to(root).as_posix()] = hashlib.sha256(picture.read_bytes()).hexdigest()
    if len(pictures)<4:
        sections['M13']['body'] += gap('所选 offer 可核验详情图不足4张','从该offer详情页采集做工/结构图片并逐图查看')
    patents=ruler.get('patent_comparisons') or []
    if not patents:
        numbers=re.findall(r'\bUSD\d+S\d\b',' '.join(str(x.get('result') or '') for x in ruler.get('searches') or []))
        patents=[{'patent_number':n,'name':'待核：已检索相关卷纸篮外观文字记录，缺标题核验',
                  'similarities':'待核：缺附图与所选SKU实物逐线比较',
                  'differences':'待核：未核对实线/虚线与保护范围',
                  'conclusion':'待核：不能据文字记录判无侵权；补专利图纸及实物，由专业人员逐件复核'} for n in dict.fromkeys(numbers)]
    if not patents:
        patents=[{'patent_number':'待核：尚无可核验专利号','name':'待核：补检索',
                  'similarities':'待核：补专利图纸与本SKU对照','differences':'待核：缺保护范围比较',
                  'conclusion':'待核：检索和逐件比对完成前不声称已清权'}]
    set_body(14, chapters['ip']['body']+'\n### 外观专利逐件比对\n\n'+
        table(['专利号','名称','相似点','差异点','结论'],[
            (x.get('patent_number') or x.get('number'),x.get('name') or x.get('title'),x.get('similarities'),x.get('differences'),x.get('conclusion'))
            for x in patents])+
        '\n'+gap('逐件比对证据不足不等于已清权','补专利号、附图、权利范围和所选SKU逐件比较；不复制参考报告专利结论'))
    set_body(15, '本次未发现可替代市场。\n\n横向替代与纵向延伸仅作候选；需独立核查需求、竞争、成本和权利，不作投资推荐。')
    manifest = model['source_manifest']
    set_body(17, table(['来源 / 文件位置','时间','SHA-256'],[(p,model.get('evidence_date') or '待核：缺源日期',sha)
            for p,sha in manifest.items()])+'\n\n历史备注和关键词原文保留在原生封存及历史资料；不覆盖、不当作本轮新增证据。')
    set_body(18, table(['验收项目','本轮自检','验收边界'],[
        ('M00–M18模块','通过','19模块顺序固定，正式C8另行只读复验'),
        ('数字与中文状态','通过','统一展示投影；原始数值保留'),
        ('市场证据与覆盖','待核' if any(v.get('status')!='met' for v in (facts.get('coverage') or {}).values() if isinstance(v,dict)) else '通过','缺项逐条列M03；不能将缺项当取数达标'),
        ('成本假设','待核','页面值、默认值、用户确认分开，实际利润待报价'),
        ('权利与合规','待核','逐件检索完成前不声称已清权'),
        ('研究结论',label,'首屏、M01、M02与第9步卡同源'),
        ('采购授权','未授权，0' if not decision['purchase_authorized'] else '按用户记录','阶段研究不授予采购许可'),
        ('正式阶段门禁','等待本轮 record 只读核验','实际结果写入回执；不引用旧日志冒充本次通过')]))
    sections['M18']['body']+='\n'+table(['门禁与证据核对','当前要求','复核方法'],[
        ('市场必备字段','为什么是这个站点、需求与痛点、切入点与机会不得省略','由C1与本步record共同检查'),
        ('事实产物','由市场事实脚本生成并绑定输入来源','C3核对覆盖与来源；数据不足只按真实缺口记录'),
        ('MD与HTML','固定模块和同一决策模型输出','C8/C9及来源封存分别复验'),
        ('历史资料','原始关键词、备注、回包及用户选款保留','只采用本轮已登记版本，旧结论作为历史核对'),
        ('正式回复与ack','每张卡独立final，下一轮实际取证','commentary不算出卡；阶段go须独立用户决策'),
        ('研究结论与授权','暂缓与技术检查分别记录','研究可交付不等于采购可执行，金额仍以用户授权为限')])
    for section in sections.values():
        section['body'] = reader_projection(section['body'])
    # Provenance hashes are allowed only in M17; retain them there explicitly.
    sections['M17']['body'] = table(['来源 / 文件位置','时间','SHA-256'],[(p if sha else Path(p).name+'（未提供）',model.get('evidence_date') or '待核',sha) for p,sha in manifest.items()])
    for key,section in old.items():
        if '知识库依据' in section['title']:
            sections[key] = {**section,'title':'知识库依据','body':reader_projection(re.sub(r'(?i)\b[0-9a-f]{64}\b','见 M17 证据索引',section['body']).replace('SHA-256','证据索引'))}
    model['sections'] = sections
    model['module_contract'] = 'M00-M18'
    return model
