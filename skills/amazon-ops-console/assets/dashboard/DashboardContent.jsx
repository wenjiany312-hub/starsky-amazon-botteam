import React,{useEffect,useState} from 'react';
import {EvidenceChart,DataComponent,DataTable,Filters,Icon,MetricCard,Section,SortableItem,SortableRegion,useDataApp,useDashboardTabs} from '../../data-app-public.jsx';
import './dashboard.css';
const tabs=[{id:'overview',label:'店铺总览'},{id:'sales',label:'出单明细'},{id:'advertising',label:'广告诊断'},{id:'listings',label:'商品与文案'},{id:'copy',label:'文案建议'},{id:'restock',label:'库存补货'},{id:'costs',label:'店铺利润'},{id:'review',label:'待审核'},{id:'comparison',label:'优化复盘'}].map(t=>({...t,filterIds:['market','date'],focusFields:['sku','asin']}));
const navGroups=[{label:'经营',items:['overview','sales','advertising','costs']},{label:'商品',items:['listings','copy','restock']},{label:'决策',items:['review','comparison']}];
const tabIcons={overview:'monitor',sales:'convert',advertising:'sliders',listings:'copy',copy:'edit',restock:'sendUp',costs:'summaryBubble',review:'shieldCheck',comparison:'eye'};
const fieldNames={title:'标题',itemHighlights:'商品亮点',bulletPoints:'五点描述',searchTerms:'后台搜索词 ST',longDescription:'产品长描述'};
const textValue=v=>v==null?'未返回':Array.isArray(v)?v.map(textValue).join('\n'):typeof v==='object'?JSON.stringify(v,null,2):String(v);
const fieldState={returned:'已取得',empty:'字段为空',not_returned:'接口未返回'};
const numeric=v=>typeof v==='number'&&Number.isFinite(v);
const sum=(rows,k)=>rows.length&&rows.every(r=>numeric(r[k]))?rows.reduce((a,r)=>a+r[k],0):null;
const number=v=>numeric(v)?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:0}).format(v):'待取数';
const money=(v,c)=>numeric(v)?new Intl.NumberFormat('zh-CN',{style:'currency',currency:c,maximumFractionDigits:2}).format(v):'待取数';
const decimal=v=>numeric(v)?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:4}).format(v):'待填写';
const pct=v=>numeric(v)?(v*100).toFixed(1)+'%':'不可计算';
const ratio=(a,b)=>numeric(a)&&numeric(b)&&b>0?a/b:null;
const add=(items,k)=>items.reduce((a,r)=>a+(numeric(r[k])?r[k]:0),0);
const latest=rows=>rows.map(r=>r.date).filter(Boolean).sort().at(-1);
const productTitle=(item,products)=>item.title||products.find(p=>p.sku===item.sku)?.title||item.sku;
const zeroSpend=r=>numeric(r.zero_order_spend)?r.zero_order_spend:add(r.zero_order_search_terms??[],'spend');
function adWorkItems(ads,currency){
 return ads.filter(r=>numeric(r.spend)&&r.spend>0).flatMap(r=>{
  const zero=zeroSpend(r);
  const oos=r.inventory===0||(r.expansion_blockers||[]).includes('inventory_unavailable');
  const over=numeric(r.acos)&&numeric(r.break_even_acos)&&r.acos>r.break_even_acos;
  if(oos)return[{sku:r.sku,asin:r.asin,title:r.title,reason:`可售为 0，仍花费 ${money(r.spend,currency)}`,score:1000+r.spend}];
  if(zero>0)return[{sku:r.sku,asin:r.asin,title:r.title,reason:`零单词花费 ${money(zero,currency)}`,score:600+zero}];
  if(over)return[{sku:r.sku,asin:r.asin,title:r.title,reason:`ACOS ${pct(r.acos)}，高于盈亏线 ${pct(r.break_even_acos)}`,score:300+r.spend*(r.acos-r.break_even_acos)}];
  return[];
 }).sort((a,b)=>b.score-a.score).slice(0,5);
}
function copyWorkItems(products,coverage,checks){
 const gaps={};for(const r of coverage){if(r.state&&r.state!=='已覆盖')gaps[r.sku]=(gaps[r.sku]||0)+1;}
 const seen=new Set(),items=[];
 for(const p of [...products].sort((a,b)=>(b.issuesCount||0)-(a.issuesCount||0))){
  if((p.issuesCount||0)>0){items.push({sku:p.sku,asin:p.asin,title:p.title,reason:`Listing 问题 ${p.issuesCount} 条`});seen.add(p.sku);}
 }
 for(const r of checks.filter(c=>c.state==='超过本店约束')){
  if(seen.has(r.sku))continue;
  items.push({sku:r.sku,asin:products.find(p=>p.sku===r.sku)?.asin,title:productTitle(r,products),reason:r.reason||'超过本店文案约束'});
  seen.add(r.sku);
 }
 for(const [sku,count] of Object.entries(gaps).sort((a,b)=>b[1]-a[1])){
  if(seen.has(sku)||items.length>=5)continue;
  items.push({sku,asin:products.find(p=>p.sku===sku)?.asin,title:productTitle({sku},products),reason:`${count} 个词待核对是否写入文案`});
  seen.add(sku);
 }
 return items.slice(0,5);
}
function stockWorkItems(restock,products){
 const oos=restock.filter(r=>r.available===0&&numeric(r.daily_average_units)&&r.daily_average_units>0)
  .map(r=>({sku:r.sku,asin:r.asin,title:productTitle(r,products),reason:`可售为 0 · 日均 ${decimal(r.daily_average_units)} · 建议 ${r.latest_start_date||'核对排期'}`,score:2000}));
 const alert=restock.filter(r=>r.replenishment_alert&&(r.available||0)>0)
  .sort((a,b)=>(a.inventory_cover_days??999)-(b.inventory_cover_days??999))
  .map(r=>({sku:r.sku,asin:r.asin,title:productTitle(r,products),reason:`覆盖 ${decimal(r.inventory_cover_days)} 天 · 建议 ${r.latest_start_date||'启动补货'}`,score:1000-(r.inventory_cover_days||0)}));
 return[...oos,...alert].slice(0,5);
}
const asinLike=/^b0[0-9a-z]{8,}$/i;
const adGateNames={inventory_unavailable:'断货，勿加码',attribution_not_mature:'7天归因未结束，先记候选',exact_inventory_incomplete:'全账户精准查重未完成',term_acos_not_below_cost_threshold:'该词 ACOS 已高于盈亏线',uk_tax_basis_unverified:'英国税口径未核对',keyword_rule_blocked:'规则拦截',cost_missing_or_incomplete:'缺成本',inventory_not_current_or_complete:'库存不是当前完整快照',product_performance_incomplete:'产品广告表现未齐'};
function adTermText(value){
 if(value==null)return '';
 if(typeof value==='string')return value.trim();
 if(Array.isArray(value))return value.map(adTermText).filter(Boolean).join(' · ');
 if(typeof value==='object'){
  if(value.value)return String(value.value).trim();
  if(value.type)return String(value.type);
  return Object.values(value).map(adTermText).filter(Boolean).join(' · ');
 }
 return String(value);
}
function isAsinTerm(text){return asinLike.test(String(text||'').replace(/[\s\[\]"']/g,''));}
function isProductTarget(obs){
 const blob=JSON.stringify(obs.name??'');
 return /ASIN_SAME_AS|ASIN_SUBSTITUTE/.test(blob)||(obs.layer==='target'&&isAsinTerm(adTermText(obs.name)));
}
function classifyZeroOrder(obs,checks,ownAsin){
 const term=adTermText(obs.name);
 const compact=term.replace(/\s+/g,'');
 if(isAsinTerm(compact)&&compact.toUpperCase()!==String(ownAsin||'').toUpperCase())return {bucket:'negate',action:'建议否',why:'竞品 / 他人 ASIN。不要开成精准词；无单时否掉该搜索词或暂停该商品投放。'};
 if(isProductTarget(obs)&&(!compact||compact.toUpperCase()!==String(ownAsin||'').toUpperCase()))return {bucket:'negate',action:'建议否',why:'商品投放指向其他 ASIN 或关联商品，期间无归因订单。建议暂停该投放，不写成关键词。'};
 if(obs.layer==='target'&&/^\d{8,}$/.test(compact))return {bucket:'watch',action:'建议观察',why:'这是商品投放对象编号，快照里看不清具体 ASIN。先到后台核对该投放，再决定是否暂停。'};
 const hit=checks.find(c=>c.term===term&&(c.reason==='banned_terms'||c.reason==='competitor_brands'||c.state==='阻止进入候选'));
 if(hit)return {bucket:'negate',action:'建议否',why:hit.reason==='competitor_brands'?'疑似竞品品牌，不继续投放。':'禁用 / 敏感词，不继续投放。'};
 return {bucket:'watch',action:'建议观察',why:'有花费、暂未归因订单。词意相关时先等 7 天补记，并核页面是否讲清规格；不立刻否词。'};
}
function buildAdDesk(ad,observations,plans,checks){
 const own=ad.asin,negate=[],watch=[],exact=[],filtered=[],seen=new Set();
 const push=(list,row)=>{const key=`${row.layer||''}:${String(row.term||'').toLowerCase()}`;if(!row.term||seen.has(key))return;seen.add(key);list.push(row);};
 for(const obs of observations){
  const term=adTermText(obs.name);if(!term)continue;
  const cls=classifyZeroOrder(obs,checks,own);
  push(cls.bucket==='negate'?negate:watch,{term,layer:obs.layer,spend:obs.spend,action:cls.action,why:cls.why});
 }
 for(const z of ad.zero_order_search_terms||[]){
  const term=z.keyword;if(!term)continue;
  const cls=classifyZeroOrder({name:term,layer:'search_term',spend:z.spend},checks,own);
  push(cls.bucket==='negate'?negate:watch,{term,layer:'search_term',spend:z.spend,action:cls.action,why:cls.why});
 }
 for(const plan of plans){
  const term=plan.keyword,metrics=plan.current?.search_term_metrics||{};
  if(isAsinTerm(term)){filtered.push({term,why:'出单词是竞品 ASIN，不新开精准，也不写入文案。',spend:metrics.spend,adOrders:metrics.adOrders});continue;}
  const blocked=termBlocked(term,[],checks);
  if(blocked){filtered.push({term,why:blocked,spend:metrics.spend,adOrders:metrics.adOrders});continue;}
  const gates=(plan.blockers||[]).map(k=>adGateNames[k]||k);
  const existing=plan.recommended_ad_type==='SP_REVIEW_EXISTING_EXACT';
  const hold=gates.some(g=>g.includes('断货')||g.includes('高于盈亏线')||g.includes('规则拦截'));
  exact.push({term,spend:metrics.spend,adOrders:metrics.adOrders,adSales:metrics.adSales,acos:metrics.acos,action:existing?'核对已有精准':hold?'暂缓开精准':'建议开精准',gate:gates.join('；')||'可讨论',why:existing?'同 SKU 已有这条精准，先看活动是否开启，不重复新建。':plan.reason});
 }
 const bySpend=(a,b)=>(b.spend||0)-(a.spend||0);
 return {negate:negate.sort(bySpend),watch:watch.sort(bySpend),exact:exact.sort((a,b)=>(b.adOrders||0)-(a.adOrders||0)||bySpend(a,b)),filtered};
}
function listingFieldText(listing,key){
 const f=listing?.fields?.[key];
 return f?.state==='returned'?textValue(f.value):'';
}
function stBytes(text){return new TextEncoder().encode(text).length;}
function termBlocked(term,coverage,checks){
 const t=String(term||'').trim();
 if(!t)return '空词';
 if(asinLike.test(t.replace(/\s+/g,'')))return '疑似 ASIN / 竞品编码，不写入文案';
 if(coverage.some(r=>r.keyword===t&&r.state==='规则拦截'))return '词库规则拦截';
 const hit=checks.find(c=>c.term===t&&(c.state==='阻止进入候选'||c.reason==='banned_terms'||c.reason==='competitor_brands'));
 if(hit)return hit.reason==='competitor_brands'?'疑似竞品品牌，已拦截':hit.reason==='banned_terms'?'禁用 / 敏感词，已拦截':'规则拦截';
 return '';
}
function buildCopyDesk(listing,coverage,checks,metrics){
 const blocked=[],pool=[];
 for(const row of coverage){
  const term=row.keyword,reason=termBlocked(term,coverage,checks);
  if(reason){blocked.push({term,reason,search_volume:row.search_volume});continue;}
  if(row.state==='已覆盖')continue;
  pool.push({term,search_volume:row.search_volume,traffic_share:row.traffic_share,suggested_field:row.suggested_field,state:row.state});
 }
 pool.sort((a,b)=>(b.search_volume||0)-(a.search_volume||0));
 const before=Object.fromEntries(Object.keys(fieldNames).map(k=>[k,listingFieldText(listing,k)]));
 const used=new Set();
 let title=before.title,titleAdds=[];
 for(const c of pool){
  if(titleAdds.length>=2)break;
  if(!c.term||title.toLowerCase().includes(c.term.toLowerCase()))continue;
  const next=title?`${title} ${c.term}`:c.term;
  if(Array.from(next).length>200)continue;
  title=next;titleAdds.push(c.term);used.add(c.term);
 }
 let st=before.searchTerms,stAdds=[];
 for(const c of pool){
  if(used.has(c.term)||st.toLowerCase().includes(c.term.toLowerCase()))continue;
  const next=st?`${st} ${c.term}`:c.term;
  if(stBytes(next)>249)continue;
  st=next;stAdds.push(c.term);used.add(c.term);
  if(stAdds.length>=8)break;
 }
 return {blocked,candidates:pool.slice(0,15),drafts:[
  {key:'title',before:before.title,after:titleAdds.length?title:before.title,changed:!!titleAdds.length,why:titleAdds.length?`从本店广告搜索词 / SIF 词库取出、且未命中禁用与竞品规则的通用词补入标题：${titleAdds.join('、')}。只加产品事实能支撑的词，不写未证实功效，不堆竞品 ASIN。`:'标题已含核心表达，或再加词会超 200 字符；不编造新卖点。',note:Array.from((titleAdds.length?title:before.title)||'').length>75?'超过本店 75 字符约束，提交前需人工压字。':''},
  {key:'itemHighlights',before:before.itemHighlights,after:before.itemHighlights,changed:false,why:'接口未返回商品亮点时，不把五点改写成亮点。可在本页人工录入后再参与覆盖检查。'},
  {key:'bulletPoints',before:before.bulletPoints,after:before.bulletPoints,changed:false,why:'五点已有产品事实。缺词优先进标题或 ST，避免再加一条重复五点被判定堆砌。'},
  {key:'searchTerms',before:before.searchTerms,after:stAdds.length?st:before.searchTerms,changed:!!stAdds.length,why:stAdds.length?`ST 买家不可见，补入标题未承接且通过拦截的词：${stAdds.join('、')}。按 249 字节截断，只留相关词。`:'ST 已接近字节上限，或没有可安全补入的词。'},
  {key:'longDescription',before:before.longDescription,after:before.longDescription,changed:false,why:'长描述已有内容。本版不自动重写 HTML，避免引入绝对化用语或医疗功效承诺。'}
 ]};
}
function costFx(costs){
 const vals=[...new Set(costs.map(r=>r.fx_cny_per_currency).filter(numeric))];
 return vals.length===1?vals[0]:null;
}
function skuContribution(cost,sales,units){
 if(!cost||cost.state!=='ready'||!numeric(sales)||!numeric(units)||!numeric(cost.fx_cny_per_currency)||cost.fx_cny_per_currency<=0)return null;
 if(!numeric(cost.purchase_cny)||!numeric(cost.freight_cny)||!numeric(cost.fba_fee)||!numeric(cost.commission_rate))return null;
 const landed=(cost.purchase_cny+cost.freight_cny)/cost.fx_cny_per_currency*units;
 const fba=cost.fba_fee*units;
 const commission=sales*cost.commission_rate;
 const vatRate=numeric(cost.vat_rate)?cost.vat_rate:0;
 const vat=vatRate>0?sales*vatRate/(1+vatRate):0;
 const service=(commission+fba)*(numeric(cost.digital_service_fee_rate)?cost.digital_service_fee_rate:0);
 const other=(numeric(cost.other_fee)?cost.other_fee:0)*units;
 const product_cost=landed+fba+commission+vat+service+other;
 return {landed_cost:landed,fba_fee:fba,commission,vat,digital_service_fee:service,other_fee:other,product_cost,contribution:sales-product_cost};
}
function resolveSkuProfit(detail,cost){
 if(detail.zero_activity)return null;
 if(numeric(detail.contribution)&&numeric(detail.sales))return {sku:detail.sku,units:detail.units,sales:detail.sales,product_cost:detail.sales-detail.contribution,contribution:detail.contribution,grade:'complete'};
 const calc=skuContribution(cost,detail.sales,detail.units);
 if(!calc)return {sku:detail.sku,units:detail.units,sales:detail.sales,product_cost:null,contribution:null,grade:'blocked'};
 return {sku:detail.sku,units:detail.units,sales:detail.sales,...calc,grade:'working'};
}
function enrichProfitDay(row,costsBySku){
 if(numeric(row.estimated_profit))return {...row,working_profit:row.estimated_profit,profit_grade:'complete'};
 let product=0,contrib=0,ok=true;
 for(const detail of row.sku_rows||[]){
  if(detail.zero_activity)continue;
  if(numeric(detail.contribution)&&numeric(detail.sales)){product+=detail.sales-detail.contribution;contrib+=detail.contribution;continue;}
  const calc=skuContribution(costsBySku[detail.sku],detail.sales,detail.units);
  if(!calc){ok=false;break;}
  product+=calc.product_cost;contrib+=calc.contribution;
 }
 if(ok&&numeric(row.ad_spend)&&numeric(row.sales))return {...row,product_cost:row.product_cost??product,covered_contribution:row.covered_contribution??contrib,working_profit:contrib-row.ad_spend,profit_grade:'working'};
 return {...row,working_profit:null,profit_grade:'blocked'};
}
function profitWindow(days,mode,pickedDate){
 const sorted=[...days].filter(r=>r.date).sort((a,b)=>a.date.localeCompare(b.date));
 if(mode==='day'){const day=pickedDate||sorted.at(-1)?.date;return sorted.filter(r=>r.date===day);}
 if(mode==='7d')return sorted.slice(-7);
 const last=sorted.at(-1)?.date;if(!last)return [];
 const [y,m]=last.split('-').map(Number);
 if(mode==='month')return sorted.filter(r=>r.date.startsWith(`${y}-${String(m).padStart(2,'0')}`));
 const prev=new Date(Date.UTC(y,m-2,1));
 return sorted.filter(r=>r.date.startsWith(`${prev.getUTCFullYear()}-${String(prev.getUTCMonth()+1).padStart(2,'0')}`));
}
function addCny(row,fx){
 const keys=['sales','product_cost','ad_spend','working_profit','contribution'];
 const extra={};
 for(const key of keys)extra[`${key}_cny`]=numeric(row[key])&&numeric(fx)?row[key]*fx:null;
 return {...row,...extra,fx};
}
function enrichAdRows(ads,restock,costs){
 const stockBySku=Object.fromEntries(restock.map(r=>[r.sku,r]));
 const costBySku=Object.fromEntries(costs.map(r=>[r.sku,r]));
 return ads.map(r=>{
  const stock=stockBySku[r.sku],cost=costBySku[r.sku];
  const available=numeric(r.inventory)?r.inventory:stock?.available;
  const cover=stock?.inventory_cover_days;
  const target=cost?.target_acos??r.break_even_acos??null;
  const oos=available===0||(r.expansion_blockers||[]).includes('inventory_unavailable');
  let stock_guard='—';
  if(oos)stock_guard='断货，勿加码';
  else if(numeric(cover)&&cover<45)stock_guard=`覆盖 ${Number(cover).toFixed(0)} 天，慎加预算`;
  else if(numeric(available))stock_guard=`可售 ${number(available)}`;
  return{...r,available,inventory_cover_days:numeric(cover)?cover:null,target_acos:target,stock_guard,zero_order_spend:numeric(r.zero_order_spend)?r.zero_order_spend:(zeroSpend(r)||null)};
 });
}
const states={planning_estimate:'备货排期估算',insufficient:'待补数据',stale:'上次快照',reconciled:'金额已核对',estimate:'按参数估算',ready:'成本齐全',needs_data:'待补成本',missing:'待采集',data_gap:'有数据缺口',integrity_error:'完整性校验失败',complete:'已取得',verified:'已验证',observed:'已取得',pending:'生成中',partial:'部分数据',error:'取数失败',pending_verification:'待验证',pending_review:'待审核',approved_not_executed:'已批准 · 尚未执行',rejected:'已拒绝',executed_reported:'已记录人工回执'};
const profitGrades={complete:'已对齐估算',working:'约算 · 税口径未核对',blocked:'暂不能估算'};
const cols=(pairs,c)=>pairs.map(([field,label,type])=>({field,label,...(type==='money'?{renderCell:v=>money(v,c)}:type==='cny'?{renderCell:v=>money(v,'CNY')}:type==='number'?{renderCell:number}:type==='decimal'?{renderCell:decimal}:type==='percent'?{renderCell:pct}:type==='status'?{renderCell:v=>states[v]??v??'待验证'}:type==='grade'?{renderCell:v=>profitGrades[v]??v??'待核对'}:{renderCell:v=><span className="ops-cell-copy">{textValue(v)}</span>})}));
function TableBlock({id,queryId,title,rows,columns,onRowSelect,description,sourceRows}){
 const source=sourceRows??rows;
 return <DataComponent id={id} queryId={queryId} title={title} variant="card" kind="table" displayRows={rows} sourceRows={source} description={description}>{rows.length?<DataTable rows={rows} columns={columns} caption={title} onRowSelect={onRowSelect} rowActionLabel={onRowSelect?()=>"查看明细":undefined}/>:<div className="ops-empty"><span>暂无可展示记录</span><p>{description||'等待对应来源的数据或人工确认。'}</p></div>}</DataComponent>;
}
function ManualHighlights({product,record,apiAvailable}){
 const [current,setCurrent]=useState(record),[editing,setEditing]=useState(false),[draft,setDraft]=useState(record?.value??''),[saving,setSaving]=useState(false),[notice,setNotice]=useState('');
 const save=async event=>{
  event.preventDefault();setSaving(true);setNotice('');
  try{
   const statusResponse=await fetch('/api/status');if(!statusResponse.ok)throw new Error('本地服务未连接');const status=await statusResponse.json();
   const response=await fetch('/api/manual-highlights',{method:'POST',headers:{'Content-Type':'application/json','X-Ops-CSRF':status.csrf},body:JSON.stringify({market:product.market,asin:product.asin,sku:product.sku,value:draft,expected_revision:current?.revision??0})});
   const result=await response.json();if(!response.ok)throw new Error(result.error==='revision_conflict'?'此内容已在另一窗口修改，请刷新页面后再编辑。':result.error==='update_in_progress'?'数据正在更新，请稍后保存。':'保存未完成，请刷新数据并重试。');
   setCurrent(result.record);setEditing(false);setNotice(result.view_update_error?'已保存到本地；完整看板需稍后重新生成。':'已保存到本地，刷新页面后仍可查看。');
  }catch(error){setNotice(error.message);}finally{setSaving(false);}
 };
 return <DataComponent id="ops-manual-highlights" queryId="manual_listing_fields" variant="card" title="商品亮点 · 人工录入" displayRows={current?[current]:[]} sourceRows={current?[current]:[]}>
  <p className="ops-note">接口尚未提供亮点时，可以在这里补充。内容保存在当前店铺与站点下，拉取数据不会覆盖；保存仅更新本地记录，不提交亚马逊。</p>
  {!editing&&<><div data-reviewed-rows><pre className="ops-manual-value">{current?.value||'尚未录入内容'}</pre>{current&&<p className="ops-note">人工录入 · 修订 {current.revision} · {current.updatedAt}</p>}</div><button className="ops-button ops-secondary" disabled={!apiAvailable} onClick={()=>{setDraft(current?.value??'');setNotice('');setEditing(true);}}>{current?'修改商品亮点':'录入商品亮点'}</button></>}
  {editing&&<form className="ops-manual-form" onSubmit={save}><label htmlFor="ops-highlights-input">商品亮点</label><textarea id="ops-highlights-input" rows={5} maxLength={2000} value={draft} onChange={e=>setDraft(e.target.value)} disabled={saving} placeholder="输入当前使用的商品亮点…"/><p className="ops-note">{Array.from(draft).length} 字符 · 建议控制在 125 字符内，并按当前站点及类目要求复核。可原样保留较长内容供整理。</p><div className="ops-actions"><button className="ops-button" type="submit" disabled={saving}>{saving?'保存中…':'保存到本地'}</button><button className="ops-button ops-secondary" type="button" disabled={saving} onClick={()=>setEditing(false)}>取消</button></div></form>}
  {notice&&<p role="status">{notice}</p>}
 </DataComponent>;
}
export function DashboardContent(){
 const {snapshot,queries,filters,setFilter,exploreDashboard,viewFocus,canReturnFromExploration,returnFromExploration}=useDataApp();
 const {activeTabId}=useDashboardTabs(tabs);
 const tab=tabs.some(t=>t.id===activeTabId)?activeTabId:'overview';
 const market=filters.market??'US';
 const [api,setApi]=useState(null),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[selected,setSelected]=useState(null),[campaignFocus,setCampaignFocus]=useState(null),[salesMode,setSalesMode]=useState('closed'),[profitMode,setProfitMode]=useState('month');
 useEffect(()=>{fetch('/api/status').then(r=>r.ok?r.json():null).then(setApi).catch(()=>setApi(null));},[]);
 useEffect(()=>{
  const style=document.getElementById('ops-hide-shell-tabs')??document.createElement('style');
  style.id='ops-hide-shell-tabs';
  style.textContent='.dashboard-root{--dashboard-tabs-expanded-height:0px!important;--dashboard-tabs-visible-height:0px!important}.dashboard-tabs-collapse{display:none!important}';
  if(!style.parentNode)document.head.appendChild(style);
 },[]);
 const rows=key=>(queries[key]?.rows??[]).filter(r=>r.market===market);
 const ads=rows('ad_daily'),sales=rows('sales_daily'),products=rows('products'),connections=rows('connections'),campaigns=rows('campaigns'),actions=rows('actions');
 useEffect(()=>{
  const sku=viewFocus?.sku;
  if(!sku){setSelected(null);setCampaignFocus(null);return;}
  setSelected(products.find(p=>p.sku===sku)??{sku,asin:viewFocus.asin||undefined,market});
  setCampaignFocus(null);
 },[market,tab,viewFocus?.sku,viewFocus?.asin]);
 useEffect(()=>{setCampaignFocus(null);},[selected?.sku]);
 const currency=ads[0]?.currency??sales[0]?.currency??(market==='UK'?'GBP':'USD');
 const pickedDate=filters.date&&filters.date!=='all'?filters.date:'';
 const closedSales=sales.filter(r=>!r.isIntraday);
 const salesDate=pickedDate||latest(closedSales);
 const adDate=pickedDate||latest(ads);
 const dayAds=ads.filter(r=>r.date===adDate),daySales=closedSales.filter(r=>r.date===salesDate),todaySales=sales.filter(r=>r.isIntraday);
 const goSales=mode=>{setSalesMode(mode);exploreDashboard?.('sales',{filters:{market,...(pickedDate?{date:pickedDate}:{})}});};
 const openWork=(dest,item)=>exploreDashboard?.(dest,{filters:{market},focus:{sku:item.sku,asin:item.asin??''}});
 const skuSales=rows('sku_sales_daily').filter(r=>r.isIntraday===(salesMode==='today'));
 const selectedSalesDay=pickedDate||latest(skuSales);
 const selectedListing=selected&&rows('listing_fields').find(r=>r.sku===selected.sku&&r.asin===selected.asin);
 const copyCoverage=selected?rows('keyword_coverage').filter(r=>r.sku===selected.sku):[];
 const copyChecks=selected?rows('listing_checks').filter(r=>r.sku===selected.sku):[];
 const copyDesk=selected?buildCopyDesk(selectedListing,copyCoverage,copyChecks,rows('keyword_metrics').filter(r=>r.asin===selected.asin)):null;
 const cadence=rows('collection_cadence')[0];
 const task=async(path,body)=>{
  setBusy(true);setMessage('');
  try{const s=await fetch('/api/status').then(r=>r.json());const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Ops-CSRF':s.csrf},body:JSON.stringify(body)});const result=await r.json();if(!r.ok)throw new Error(result.error??'operation_failed');
   if(path==='/api/refresh'){setMessage('正在拉取各站点数据；报表生成期间保留上次完整快照。');const timer=setInterval(async()=>{try{const job=await fetch('/api/status').then(r=>r.json());if(!job.running){clearInterval(timer);if(job.last_error){setBusy(false);setMessage('本次更新未完成：'+job.last_error);}else location.reload();}}catch{clearInterval(timer);setBusy(false);setMessage('本地服务连接中断，请重新启动看板。');}},4000);}else location.reload();
  }catch(e){setMessage('操作未完成：'+e.message);setBusy(false);}
 };
 const pending=actions.filter(r=>r.status==='pending_review').length;
 const adDiagnostics=enrichAdRows(rows('product_ad_diagnostics'),rows('replenishment'),rows('costs'));
 const copyProducts=products.map(p=>{
  const gaps=rows('keyword_coverage').filter(r=>r.sku===p.sku&&r.state&&r.state!=='已覆盖'&&r.state!=='规则拦截').length;
  const ad=adDiagnostics.find(r=>r.sku===p.sku);
  return{...p,gap_count:gaps,ad_spend:ad?.spend??null,copy_priority:(p.issuesCount||0)*10+gaps+(ad&&numeric(ad.acos)&&numeric(ad.target_acos)&&ad.acos>ad.target_acos?20:0)};
 }).sort((a,b)=>(b.copy_priority||0)-(a.copy_priority||0));
 const adsQ=adWorkItems(adDiagnostics,currency);
 const copyQ=copyWorkItems(products,rows('keyword_coverage'),rows('listing_checks'));
 const stockQ=stockWorkItems(rows('replenishment'),products);
 const triageRows=adsQ.map(item=>adDiagnostics.find(r=>r.sku===item.sku)).filter(Boolean);
 const selectedAd=selected?.sku?adDiagnostics.find(r=>r.sku===selected.sku):null;
 const adCampaignIds=selectedAd?new Set((selectedAd.groups||[]).map(g=>String(g.campaignId))):selected?.campaignId?new Set([String(selected.campaignId)]):null;
 const observationRows=rows('ad_observations').filter(r=>!adCampaignIds||adCampaignIds.has(String(r.campaignId)));
 const campaignRows=adCampaignIds?campaigns.filter(r=>adCampaignIds.has(String(r.campaignId))):campaigns;
 const selectedPlans=selectedAd?rows('ad_recommendations').filter(r=>r.sku===selectedAd.sku):[];
 const adDesk=selectedAd?buildAdDesk(selectedAd,observationRows,selectedPlans,rows('listing_checks').filter(r=>r.sku===selectedAd.sku)):null;
 const adPeriod=selectedAd?.attribution?.start_date&&selectedAd?.attribution?.end_date?`${selectedAd.attribution.start_date} 至 ${selectedAd.attribution.end_date}`:null;
 const adColumns=cols([['sku','SKU'],['title','商品'],['spend','花费','money'],['adSales','归因销售','money'],['acos','ACOS','percent'],['target_acos','目标 / 盈亏线','percent'],['stock_guard','库存'],['clicks','点击','number'],['adOrders','归因订单','number'],['cvr','点击转化率','percent'],['zero_order_spend','零单词花费','money']],currency);
 const costRows=rows('costs');
 const fx=costFx(costRows);
 const costsBySku=Object.fromEntries(costRows.map(r=>[r.sku,r]));
 const profitDays=rows('profit_daily').map(r=>addCny(enrichProfitDay(r,costsBySku),fx));
 const profitRows=profitWindow(profitDays,profitMode,pickedDate);
 const profitReady=profitRows.length>0&&profitRows.every(r=>numeric(r.working_profit));
 const profitTotals={
  sales:profitRows.length&&profitRows.every(r=>numeric(r.sales))?profitRows.reduce((a,r)=>a+r.sales,0):null,
  product_cost:profitRows.length&&profitRows.every(r=>numeric(r.product_cost))?profitRows.reduce((a,r)=>a+r.product_cost,0):null,
  ad_spend:profitRows.length&&profitRows.every(r=>numeric(r.ad_spend))?profitRows.reduce((a,r)=>a+r.ad_spend,0):null,
  working_profit:profitReady?profitRows.reduce((a,r)=>a+r.working_profit,0):null
 };
 const profitCny=Object.fromEntries(Object.entries(profitTotals).map(([k,v])=>[k,numeric(v)&&numeric(fx)?v*fx:null]));
 const profitFrom=profitRows[0]?.date,profitTo=profitRows.at(-1)?.date;
 const profitSkuMap={};
 for(const day of profitRows){
  for(const detail of day.sku_rows||[]){
   const resolved=resolveSkuProfit(detail,costsBySku[detail.sku]);
   if(!resolved)continue;
   const cur=profitSkuMap[resolved.sku]||{sku:resolved.sku,asin:products.find(p=>p.sku===resolved.sku)?.asin,title:productTitle(resolved,products),units:0,sales:0,product_cost:0,contribution:0,grade:resolved.grade,complete:true};
   if(numeric(resolved.units))cur.units+=resolved.units;
   if(numeric(resolved.sales))cur.sales+=resolved.sales; else cur.complete=false;
   if(numeric(resolved.product_cost))cur.product_cost+=resolved.product_cost; else cur.complete=false;
   if(numeric(resolved.contribution))cur.contribution+=resolved.contribution; else cur.complete=false;
   if(resolved.grade==='working'&&cur.grade==='complete')cur.grade='working';
   if(resolved.grade==='blocked'){cur.grade='blocked';cur.complete=false;}
   profitSkuMap[resolved.sku]=cur;
  }
 }
 const profitSkus=Object.values(profitSkuMap).map(r=>addCny({...r,product_cost:r.complete?r.product_cost:null,contribution:r.complete?r.contribution:null},fx)).sort((a,b)=>(b.contribution??-1)-(a.contribution??-1));
 const weekProfit=profitWindow(profitDays,'7d').filter(r=>numeric(r.working_profit));
 const weekProfitCny=weekProfit.length&&numeric(fx)&&weekProfit.length===profitWindow(profitDays,'7d').length?weekProfit.reduce((a,r)=>a+r.working_profit,0)*fx:null;
 const profitModes=[['7d','最近7天'],['month','本月'],['last_month','上个月'],['day','所选日']];
 const profitNote=fx?`1 ${currency} = ${decimal(fx)} 元，取自成本表维护汇率，区间各日同一汇率，不是当日银行中间价。只扣已维护货本、头程、FBA、佣金${market==='UK'?'、VAT 与数字服务费':''}和 SP 广告。退款、仓储、SB/SD 与结算差异未扣。`:'成本表汇率未取得，人民币金额暂不能换算。';
 const currentTab=tabs.find(item=>item.id===tab)??tabs[0];
 const workCard=(id,queryId,title,dest,items,source,empty)=><DataComponent id={id} queryId={queryId} title={title} variant="card" displayRows={source} sourceRows={source}><div className="ops-work-list">{items.length?items.map(item=><button key={item.sku} type="button" className="ops-work-item" onClick={()=>openWork(dest,item)}><strong>{item.title}</strong><span>{item.sku} · {item.reason}</span></button>):<p className="ops-note">{empty}</p>}</div><button className="ops-link" type="button" onClick={()=>exploreDashboard?.(dest)}>打开{title} →</button></DataComponent>;
 const nav=<aside className="ops-nav" aria-label="页面导航">
  <div className="ops-nav-brand"><span className="ops-nav-mark" aria-hidden="true"><svg viewBox="0 0 36 36" width="36" height="36"><rect width="36" height="36" rx="10" fill="#0f766e"/><path d="M18 28a10 10 0 0 0 10-8.2 11 11 0 0 1-20 0A10 10 0 0 0 18 28Z" fill="#14b8a6"/><path d="M18 7.2 20.1 15.7 28.8 18 20.1 20.3 18 28.8 15.9 20.3 7.2 18 15.9 15.7Z" fill="#fff"/><path d="M10.2 10.4 11 12.6 13.2 13.4 11 14.2 10.2 16.4 9.4 14.2 7.2 13.4 9.4 12.6Z" fill="#fff"/><path d="M26.6 23.2 27.2 24.8 28.8 25.4 27.2 26 26.6 27.6 26 26 24.4 25.4 26 24.8Z" fill="#d1fae5"/></svg></span><div><strong>店铺运营</strong><span>只读快照 · 本机看板</span></div></div>
  {navGroups.map(group=><div className="ops-nav-group" key={group.label}><p className="ops-nav-label">{group.label}</p>{group.items.map(id=>{const item=tabs.find(entry=>entry.id===id);return <button key={id} type="button" className={`ops-nav-item${tab===id?' is-active':''}`} aria-current={tab===id?'page':undefined} onClick={event=>{event.preventDefault();event.stopPropagation();exploreDashboard?.(id);}}><Icon name={tabIcons[id]} size={16}/><span>{item.label}</span>{id==='review'&&pending>0&&<em>{pending}</em>}</button>;})}</div>)}
  <div className="ops-nav-foot"><span>{market==='US'?'美国站':'英国站'} · {currency}</span><span>{pickedDate||salesDate||'待取数'}</span></div>
 </aside>;
 if(!['US','UK'].includes(market))return <div className="ops-console" data-dashboard-layout="viewport">{nav}<div className="ops-main"><Filters filters={snapshot.filters} queries={queries} values={filters} onChange={setFilter}/><div className="ops-empty">请选择一个站点查看金额与建议。美元和英镑分别统计。</div></div></div>;
 const metric=(id,title,key,values,value,note,mode)=><SortableItem id={id} label={title} kind="metric" span={3} key={id}><MetricCard id={id} queryId={key} title={title} value={value} displayRows={values} sourceRows={values}><p className="ops-metric-note">{note}</p>{mode&&<button className="ops-link" onClick={()=>goSales(mode)}>查看出单商品 →</button>}</MetricCard></SortableItem>;
 const guidance=(title,text)=><div className="ops-guidance"><strong>{title}</strong><p>{text}</p></div>;
 return <div className="ops-console" data-dashboard-layout="viewport">{nav}<div className="ops-main">
 <div className="ops-pagehead"><div>{canReturnFromExploration&&<button className="ops-link" type="button" onClick={returnFromExploration}>← 返回上一页</button>}<p className="ops-eyebrow">{market==='US'?'美国站':'英国站'} · {currency}</p><h1>{currentTab.label}</h1></div><div className="ops-chips"><span>{pickedDate||salesDate||'全部日期'}</span><span>{cadence?.display||'按站点当地跨日采集'}</span><span>广告 {adDate??'待取数'} · 7天归因</span></div></div>
 <Filters filters={snapshot.filters} queries={queries} values={filters} onChange={setFilter} clearLabel="重置筛选" trailingControls={<button className="ops-button" disabled={!api||busy} onClick={()=>task('/api/refresh',{})}>{busy?'更新中…':'拉取最新数据'}</button>}/>
 {message&&<div className="ops-message" role="status">{message}</div>}
 {tab==='overview'&&<>
 <Section id="ops-work-title" title="今日作业" columns={3}>
 {workCard('ops-work-ads','product_ad_diagnostics','广告','advertising',adsQ,rows('product_ad_diagnostics').filter(r=>adsQ.some(i=>i.sku===r.sku)),'近期没有需要优先排查的花费。')}
 {workCard('ops-work-copy','listing_checks','文案','copy',copyQ,rows('listing_checks').filter(r=>copyQ.some(i=>i.sku===r.sku)),'暂无优先文案缺口。')}
 {workCard('ops-work-stock','replenishment','库存','restock',stockQ,rows('replenishment').filter(r=>stockQ.some(i=>i.sku===r.sku)),'暂无需要启动的补货。')}
 </Section>
 {pending>0&&<p className="ops-work-review"><button className="ops-link" type="button" onClick={()=>exploreDashboard?.('review')}>待审核 {pending} 条，先处理人工复核 →</button></p>}
 <DataComponent id="ops-overview-profit" queryId="profit_daily" title="店铺盈利" variant="card" displayRows={profitDays} sourceRows={rows('profit_daily')}><div className="ops-profit-strip"><strong>{money(weekProfitCny,'CNY')}</strong><span>近 {profitWindow(profitDays,'7d').length} 个完整日估算利润 / 人民币{weekProfit.some(r=>r.profit_grade==='working')?' · 含约算':''}。按成本表汇率换算，不是结算实收。</span></div><button className="ops-link" type="button" onClick={()=>exploreDashboard?.('costs')}>查看本月 / 上月利润 →</button></DataComponent>
 <SortableRegion id="ops-overview-metrics" label="经营指标" variant="canvas" columns={12} spacing="standard" rows={[{id:'ops-metric-row',kind:'metrics',items:['ops-sales','ops-spend','ops-acos','ops-today']}]}>
 {metric('ops-sales','完整日店铺销售','sales_daily',daySales,money(sum(daySales,'sales'),currency),`${salesDate??'等待 Sales API'} · ${daySales.length?number(sum(daySales,'orders'))+' 笔订单':'该日暂无完整销售'}`,'closed')}
 {metric('ops-spend','完整日广告花费','ad_daily',dayAds,money(sum(dayAds,'spend'),currency),`${adDate??'等待 Ads API'} · ${dayAds.length?'SP 广告':'该日暂无广告报表'}`)}
 {metric('ops-acos','广告 ACOS','ad_daily',dayAds,pct(ratio(sum(dayAds,'spend'),sum(dayAds,'adSales'))),'花费 ÷ 广告归因销售；不是利润率')}
 {metric('ops-today','当地今日销售','sales_daily',todaySales,money(sum(todaySales,'sales'),currency),`${latest(todaySales)??'待采集'} · 盘中销售，尚未结束`,'today')}
 </SortableRegion>
 <Section id="ops-trends-title" title="经营趋势" columns={2} spacing="after-metrics">
 <EvidenceChart id="ops-ads-trend" queryId="ad_daily" title="广告花费与归因销售" variant="card" spec={{type:ads.length>1?'line':'bar',x:'date',y:'spend',fields:['spend','adSales'],currency,stackable:false,legend:{labels:{spend:'广告花费',adSales:'广告归因销售'}}}} rows={ads} sourceRows={ads} height={260}/>
 <EvidenceChart id="ops-sales-trend" queryId="sales_daily" title="店铺销售 · 已结束的自然日" variant="card" spec={{type:'line',x:'date',y:'sales',currency,legend:{labels:{sales:'店铺销售'}}}} rows={sales.filter(r=>!r.isIntraday)} sourceRows={sales.filter(r=>!r.isIntraday)} height={260}/>
 </Section>
 <Section id="ops-connection-title" title="本次数据状态"><TableBlock id="ops-connections" queryId="connections" title="来源与更新时间" rows={connections} columns={cols([['source','数据来源'],['state','状态','status'],['period','数据区间'],['checkedAt','采集时间'],['error','缺口']],currency)}/></Section>
 </>}
 {tab==='sales'&&<>{guidance('查看这一天卖出了哪些商品。','售出件数、涉及订单数和销售额分别展示。SKU涉及的订单数不可相加为全店独立订单数；盘中数据以采集时间为准。')}
 <div className="ops-actions"><button className={`ops-button ${salesMode==='closed'?'':'ops-secondary'}`} onClick={()=>setSalesMode('closed')}>完整日</button><button className={`ops-button ${salesMode==='today'?'':'ops-secondary'}`} onClick={()=>setSalesMode('today')}>当地今日</button></div>
 <p className="ops-note">{rows('sku_sales_coverage').find(r=>r.date===selectedSalesDay&&r.isIntraday===(salesMode==='today'))?.note??'该日期的逐SKU明细尚未取得，不能根据全店金额猜测出单商品。'}</p>
 <TableBlock id="ops-sold-products" queryId="sku_sales_daily" title={`${selectedSalesDay??'待采集'} · 出单商品`} rows={skuSales.filter(r=>r.date===selectedSalesDay&&((r.units??0)>0||(r.sales??0)!==0))} columns={cols([['sku','SKU'],['asin','ASIN'],['title','商品'],['units','售出件数','number'],['sku_orders','涉及订单数','number'],['sales','销售额','money'],['dataCollectedAt','采集时间']],currency)} description="完整日与盘中按各站点日期分开。暂无明细时不生成虚构订单。"/></>}
 {tab==='advertising'&&<>{guidance('先点产品，再看三条动作。','建议否：竞品 ASIN、品牌词、禁用词和指向别人的商品投放。建议观察：相关词有花费但还没归因订单，先等 7 天补记。建议开精准：本店已出单的通用词。竞品编码不会进入开词。未改后台。')}
 <TableBlock id="ops-ad-triage" queryId="product_ad_diagnostics" title="先看这些产品" rows={triageRows} sourceRows={rows('product_ad_diagnostics').filter(r=>triageRows.some(i=>i.sku===r.sku))} columns={adColumns} onRowSelect={setSelected} description="按断货仍花费、零单词花费、ACOS 高于盈亏线排序。点一行进入该产品的广告工作台。"/>
 {selectedAd&&adDesk&&<Section id="ops-ad-desk-title" title={`${selectedAd.sku} · 广告工作台`}>
 <div className="ops-copy-brief"><div className="ops-guidance"><strong>{selectedAd.title||selectedAd.sku}</strong><p>{adPeriod?`${adPeriod} · 7天归因`:''}{selectedAd.attribution?.mature===false?'，窗口尚未完全成熟。':'。'} 花费 {money(selectedAd.spend,currency)} · 归因销售 {money(selectedAd.adSales,currency)} · ACOS {pct(selectedAd.acos)} · 盈亏线 {pct(selectedAd.target_acos)} · {selectedAd.stock_guard}。建议否 {adDesk.negate.length} · 观察 {adDesk.watch.length} · 开精准 {adDesk.exact.length}。{selectedAd.stock_guard==='断货，勿加码'?'先补货，不在这里加预算。':''}</p></div><div className="ops-actions"><button className="ops-link" type="button" onClick={()=>exploreDashboard?.('copy',{filters:{market},focus:{sku:selectedAd.sku,asin:selectedAd.asin??''}})}>去文案优化 →</button><button className="ops-link" type="button" onClick={()=>exploreDashboard?.('costs',{filters:{market},focus:{sku:selectedAd.sku,asin:selectedAd.asin??''}})}>看店铺利润 →</button><button className="ops-link" type="button" onClick={()=>setSelected(null)}>返回产品列表 →</button></div></div>
 {adDesk.negate.length>0&&<TableBlock id="ops-ad-negate" queryId="ad_observations" title="建议否" rows={adDesk.negate} sourceRows={observationRows} columns={cols([['term','词 / 投放'],['layer','层级'],['spend','花费','money'],['action','动作'],['why','为什么']],currency)} description="竞品 ASIN、品牌/禁用词、以及指向其他商品的投放。在广告后台做成否词或暂停该投放。本页未改店铺。"/>}
 {adDesk.watch.length>0&&<TableBlock id="ops-ad-watch" queryId="ad_observations" title="建议观察" rows={adDesk.watch} sourceRows={observationRows} columns={cols([['term','词 / 投放'],['layer','层级'],['spend','花费','money'],['action','动作'],['why','为什么']],currency)} description="词意可能相关，只是还没归因到订单。等窗口结束再决定是否否词；同时核文案有没有讲清规格。"/>}
 {adDesk.exact.length>0&&<TableBlock id="ops-ad-exact" queryId="ad_recommendations" title="建议开精准" rows={adDesk.exact} sourceRows={selectedPlans} columns={cols([['term','候选词'],['spend','花费','money'],['adOrders','归因订单','number'],['adSales','归因销售','money'],['acos','该词 ACOS','percent'],['action','动作'],['gate','现在能不能做'],['why','为什么']],currency)} description="本店搜索词已经出过单。断货、该词已亏或已有精准时只核对、不新开。预算和竞价要你在后台确认；未创建广告。"/>}
 {adDesk.filtered.length>0&&<TableBlock id="ops-ad-filtered" queryId="ad_recommendations" title="已拦截，不开精准" rows={adDesk.filtered} sourceRows={selectedPlans} columns={cols([['term','词'],['spend','花费','money'],['adOrders','归因订单','number'],['why','拦截原因']],currency)} description="出单也不能把竞品 ASIN 或规则拦截词做成关键词。"/>}
 </Section>}
 {!selectedAd&&<p className="ops-note">先点上面的产品。选中后才会按这个 SKU 拆成建议否、建议观察、建议开精准，避免把全店零单词混在一起。</p>}
 <Section id="ops-campaign-list-title" title={selectedAd?`${selectedAd.sku} · 按活动下钻`:'按活动下钻'}><TableBlock id="ops-campaigns" queryId="campaigns" title="广告活动表现" rows={campaignRows} columns={cols([['campaignName','广告活动'],['spend','花费','money'],['adSales','归因销售','money'],['clicks','点击','number'],['adOrders','订单','number']],currency)} onRowSelect={setCampaignFocus} description="只汇总广告组层。选中产品后只显示能关联到该 SKU 的活动。点活动不会退出上面的工作台。"/></Section>
 {campaignFocus?.campaignId&&<Section id="ops-selected-campaign-title" title={campaignFocus.campaignName??campaignFocus.campaignId}><TableBlock id="ops-ad-detail" queryId="ad_detail" title="关联广告明细" rows={rows('ad_detail').filter(r=>String(r.campaignId)===String(campaignFocus.campaignId)&&(!pickedDate||r.date===pickedDate))} columns={cols([['layer','层级'],['name','关键词 / 投放 / 搜索词'],['spend','花费','money'],['adSales','归因销售','money'],['clicks','点击','number'],['adOrders','订单','number']],currency)} description="按原生广告对象 ID 关联；搜索词不直接当作调价对象。"/></Section>}
 <TableBlock id="ops-product-ad-diagnostics" queryId="product_ad_diagnostics" title="全部产品的广告表现" rows={adDiagnostics} sourceRows={rows('product_ad_diagnostics')} columns={adColumns} onRowSelect={setSelected} description="只使用准确关联到单一SKU的广告组，同组多个产品的业绩不强行分摊。目标 / 盈亏线来自成本参数，缺成本时用盈亏线。"/>
 </>}
 {tab==='listings'&&<>{guidance('广告证据和文案，按产品准确关联。','同一广告组含多个 ASIN 时，不分摊搜索词业绩。类目禁用词、竞品品牌和功效证据由规则检查，再由人工复核。')}
 <TableBlock id="ops-products" queryId="products" title="商品与库存" rows={products} columns={cols([['sku','SKU'],['asin','ASIN'],['title','当前标题'],['available','FBA 可售','number'],['listingStatus','商品状态'],['issuesCount','问题数','number']],currency)} onRowSelect={setSelected}/>
 {selected&&<Section id="ops-product-title" title={`当前文案 · ${selected.sku}`}><DataComponent id="ops-product-facts" queryId="listing_fields" variant="card" title="后台提交快照与字段" displayRows={selectedListing?[selectedListing]:[]} sourceRows={selectedListing?[selectedListing]:[]}><p className="ops-note">ASIN {selected.asin??'未返回'} · 内容版本 {selectedListing?.version??'待采集'} · {selectedListing?.contentHash?.slice(0,12)}。这是卖家接口返回的内容，买家页面展示可能存在差异。</p><div data-reviewed-rows>{Object.entries(fieldNames).map(([key,label])=>{const f=selectedListing?.fields?.[key];const length=f?.state==='returned'?(key==='searchTerms'?`${new TextEncoder().encode(textValue(f.value)).length} 字节`:`${Array.from(textValue(f.value)).length} 字符${key==='bulletPoints'?'（合计）':''}`):'';return <div className="ops-copy-field" key={key}><div className="ops-field-heading"><strong>{label}</strong><span>{fieldState[f?.state]??'待采集'}{length?` · ${length}`:''}</span></div><pre>{f?.state==='returned'?textValue(f.value):f?.state==='empty'?'接口返回空值':'本次接口未提供此字段；不代表后台没有填写。'}</pre></div>;})}</div></DataComponent>
 <ManualHighlights key={`${market}:${selected.asin}:${selected.sku}`} product={selected} record={rows('manual_listing_fields').find(r=>r.asin===selected.asin&&r.sku===selected.sku)} apiAvailable={!!api}/>
 <TableBlock id="ops-keyword-ranks" queryId="keyword_metrics" title="关键词流量与排名信号" rows={rows('keyword_metrics').filter(r=>r.asin===selected.asin)} columns={cols([['keyword','关键词'],['provider','来源'],['traffic_share','流量份额','percent'],['search_volume','月搜索量','number'],['organic_rank','自然排名采样'],['ad_rank','广告排名采样'],['rank_at','自然位采样时间']],currency)} description="SIF优先；第三方估算与采样排名不等于本店真实流量。未采集的产品保留为空。"/>
 <TableBlock id="ops-listing-history" queryId="listing_versions" title="文案版本记录" rows={rows('listing_versions').filter(r=>r.sku===selected.sku)} columns={cols([['revision','采集修订'],['observedAt','记录时间'],['contentHash','内容指纹']],currency)}/></Section>}</>}
 {tab==='copy'&&<>{guidance('先看这个产品为什么要改文案，再看建议稿。','对照本店广告搜索词和 SIF 词库。竞品 ASIN、品牌词、禁用词和未证实功效不会写入建议。建议稿未提交亚马逊，需人工确认词意与相关性。')}
 <TableBlock id="ops-copy-products" queryId="products" title="优先处理的产品" rows={copyProducts} sourceRows={products} columns={cols([['sku','SKU'],['asin','ASIN'],['title','当前标题'],['issuesCount','Listing 问题','number'],['gap_count','待核词','number'],['ad_spend','广告花费','money']],currency)} onRowSelect={setSelected} description="按 Listing 问题、缺词和广告高于盈亏线排序。点开进入该产品的文案工作台。"/>
 {selected&&copyDesk&&<Section id="ops-copy-desk-title" title={`${selected.sku} · 文案工作台`}>
 <div className="ops-copy-brief"><div className="ops-guidance"><strong>{selected.title||selected.sku}</strong><p>广告 {selectedAd&&selectedAd.sku===selected.sku?`${money(selectedAd.spend,currency)} · ACOS ${pct(selectedAd.acos)} · ${selectedAd.stock_guard}`:'该产品近期无单独可关联的广告组表现'}。版本 {selectedListing?.version??'待采集'}。建议只改有证据的字段，目的是提高相关曝光，不保证订单。</p></div><div className="ops-actions"><button className="ops-link" type="button" onClick={()=>exploreDashboard?.('advertising',{filters:{market},focus:{sku:selected.sku,asin:selected.asin??''}})}>看这个产品的广告 →</button><button className="ops-link" type="button" onClick={()=>setSelected(null)}>返回产品列表 →</button></div></div>
 {copyDesk.blocked.length>0&&<TableBlock id="ops-copy-blocked" queryId="listing_checks" title="已拦截，不写入文案" rows={copyDesk.blocked} sourceRows={copyChecks} columns={cols([['term','词'],['reason','拦截原因']],currency)} description="竞品编码、品牌词、禁用词或规则拦截。出现在广告搜索词里也不自动埋进 Listing。"/>}
 <TableBlock id="ops-copy-candidates" queryId="keyword_coverage" title="建议评估写入的词" rows={copyDesk.candidates} sourceRows={copyCoverage} columns={cols([['term','候选词'],['search_volume','月搜索量','number'],['traffic_share','流量份额','percent'],['suggested_field','建议位置'],['state','当前判断']],currency)} description="SIF / 广告搜索词中未覆盖且通过拦截的词。搜索量是第三方估算，不是本店真实流量。"/>
 <DataComponent id="ops-copy-drafts" queryId="listing_fields" title="优化建议 · 修改前 / 修改后" variant="card" displayRows={selectedListing?[selectedListing]:[]} sourceRows={selectedListing?[selectedListing]:[]} description="从现有后台快照出发补词，不重写卖点。未提交亚马逊。">
  <div data-reviewed-rows>{copyDesk.drafts.map(draft=><div className={`ops-draft${draft.changed?' is-changed':''}`} key={draft.key}><div className="ops-field-heading"><strong>{fieldNames[draft.key]}</strong><span>{draft.changed?'有建议稿':'维持原文'}{draft.key==='searchTerms'?` · ${stBytes(draft.after||'')} 字节`:` · ${Array.from(draft.after||'').length} 字符`}</span></div><div className="ops-before-after"><div><span>修改前</span><pre>{draft.before||'（空 / 未返回）'}</pre></div><div><span>建议后</span><pre>{draft.after||'（不编造）'}</pre></div></div><p className="ops-note">{draft.why}{draft.note?` ${draft.note}`:''}</p></div>)}</div>
 </DataComponent>
 <ManualHighlights key={`${market}:${selected.asin}:${selected.sku}`} product={selected} record={rows('manual_listing_fields').find(r=>r.asin===selected.asin&&r.sku===selected.sku)} apiAvailable={!!api}/>
 <TableBlock id="ops-copy-checks" queryId="listing_checks" title="规则检查" rows={copyChecks} columns={cols([['field','字段'],['term','词 / 规则'],['state','结论'],['reason','依据']],currency)}/>
 <TableBlock id="ops-copy-changes" queryId="listing_changes" title="历史上版与本版变化" rows={rows('listing_changes').filter(r=>r.sku===selected.sku)} columns={[{field:'field',label:'字段',renderCell:v=>fieldNames[v]??v},{field:'before',label:'修改前',renderCell:v=><span className="ops-cell-copy">{textValue(v?.value)}</span>},{field:'after',label:'修改后',renderCell:v=><span className="ops-cell-copy">{textValue(v?.value)}</span>}]} description="首次采集作为基线。取得不同版本后才显示已发生的前后差异，不把本次建议稿记成已发布历史。"/>
 </Section>}</>}
 {tab==='restock'&&<>{guidance('提前安排补货，参数可以继续细化。','当前按60天交期＋15天安全缓冲提醒。近期销量用于排期估算；在途数量与到货日尚待确认，参考量未扣在途，不是最终采购数量。')}
 <TableBlock id="ops-restock" queryId="replenishment" title="库存覆盖与补货提醒" rows={rows('replenishment')} columns={cols([['sku','SKU'],['asin','ASIN'],['available','可售','number'],['daily_average_units','日均销量','decimal'],['inventory_cover_days','预计覆盖天数','decimal'],['latest_start_date','建议启动日期'],['reference_qty_before_inbound','未扣在途参考量','number'],['state','估算状态','status']],currency)} description="缺少完整销量或有效库存时，保留待核对，不按零库存/零需求推算。"/></>}
 {tab==='costs'&&<>{guidance('先看这个站这段时间大概赚了多少人民币。',profitNote)}
 <div className="ops-actions">{profitModes.map(([id,label])=><button key={id} className={`ops-button ${profitMode===id?'':'ops-secondary'}`} type="button" disabled={id==='day'&&!pickedDate} onClick={()=>setProfitMode(id)}>{label}</button>)}</div>
 <p className="ops-note">{profitRows.length?`${profitFrom} 至 ${profitTo} · ${profitRows.length} 个完整日 · ${profitRows.every(r=>r.profit_grade==='complete')?'SKU 销量已与全店对齐':profitRows.some(r=>r.profit_grade==='working')?'含约算：英国站按成本表 VAT，假设销售额含税': '部分日期暂不能出净利润'}。`:'这个窗口还没有完整日快照。'}{profitMode==='day'&&!pickedDate?' 顶部日期筛选当前为全部，所选日按最近一个完整日。':''}</p>
 <SortableRegion id="ops-profit-metrics" label="区间盈利" variant="canvas" columns={12} spacing="standard" rows={[{id:'ops-profit-metric-row',kind:'metrics',items:['ops-profit-cny','ops-profit-sales-cny','ops-profit-cost-cny','ops-profit-ads-cny']}]}>
 {metric('ops-profit-cny','估算利润 / 人民币','profit_daily',profitRows,money(profitCny.working_profit,'CNY'),`${money(profitTotals.working_profit,currency)} · ${profitReady?(profitRows.some(r=>r.profit_grade==='working')?'约算，待核对税口径':'已对齐估算'):'缺销量、成本或广告时不编造全店利润'}`)}
 {metric('ops-profit-sales-cny','销售额 / 人民币','profit_daily',profitRows,money(profitCny.sales,'CNY'),money(profitTotals.sales,currency))}
 {metric('ops-profit-cost-cny','货本及预计费用 / 人民币','profit_daily',profitRows,money(profitCny.product_cost,'CNY'),money(profitTotals.product_cost,currency))}
 {metric('ops-profit-ads-cny','SP 广告 / 人民币','profit_daily',profitRows,money(profitCny.ad_spend,'CNY'),money(profitTotals.ad_spend,currency))}
 </SortableRegion>
 <TableBlock id="ops-profit" queryId="profit_daily" title="按日拆开" rows={profitRows} sourceRows={rows('profit_daily').filter(r=>profitRows.some(x=>x.date===r.date))} columns={cols([['date','日期'],['sales','销售额','money'],['sales_cny','销售额 / 元','cny'],['product_cost','货本及费用','money'],['ad_spend','SP 广告','money'],['working_profit','估算利润','money'],['working_profit_cny','估算利润 / 元','cny'],['profit_grade','数字性质','grade'],['gap_notes','尚未扣除 / 待核对']],currency)} description="每天用当天店铺销售 − 已维护成本 − 当天 SP 广告。人民币按成本表汇率换算。约算未当作结算实收。"/>
 <TableBlock id="ops-profit-skus" queryId="profit_daily" title="这段时间出单产品的贡献" rows={profitSkus} sourceRows={rows('profit_daily').filter(r=>profitRows.some(x=>x.date===r.date))} columns={cols([['sku','SKU'],['title','商品'],['units','件数','number'],['sales','销售额','money'],['product_cost','货本及费用','money'],['contribution','扣货本后贡献','money'],['contribution_cny','贡献 / 元','cny'],['grade','数字性质','grade']],currency)} onRowSelect={row=>exploreDashboard?.('advertising',{filters:{market},focus:{sku:row.sku,asin:row.asin??''}})} description="广告费按店铺日合计只扣一次，不按 SKU 分摊。点一行去看这个产品的广告。"/>
 <p><a className="ops-link" href="/api/cost-template" download>下载成本模板（可选填写） ↓</a></p>
 <Section id="ops-cost-table-title" title="成本维护"><TableBlock id="ops-costs" queryId="costs" title="已关联成本" rows={costRows} columns={cols([['sku','SKU'],['asin','ASIN'],['purchase_cny','采购 / 元','decimal'],['freight_cny','头程 / 元','decimal'],['fba_fee','FBA 配送费','money'],['landed_unit_cost','落地成本','money'],['fx_cny_per_currency','汇率','decimal'],['target_acos','目标 ACOS','percent'],['state','维护状态','status']],currency)} description="采购和头程按元维护，落地成本用表内汇率折成站点币。改汇率后重新拉取即可重算人民币利润。"/></Section></>}
 {tab==='review'&&<>{guidance('每项修改保留理由，批准后仍需执行。','本版可批准、拒绝和导出方案。后台广告与 Listing 写入尚未开放，批准不会修改店铺。')}<p><a className="ops-link" href="/api/export-approved" download>导出已批准方案（尚未执行） ↓</a></p>
 <TableBlock id="ops-actions" queryId="actions" title="变更审核队列" rows={actions} columns={cols([['layer','对象层级'],['native_id','对象 ID'],['field','拟改字段'],['reason','为什么修改'],['status','审核状态','status']],currency)} onRowSelect={setSelected} description="暂无满足证据与规则条件的变更。系统没有生成默认调价或自动填入预算。"/>
 {selected&&<DataComponent id="ops-action-detail" queryId="actions" title="修改前后与人工决策" variant="card" displayRows={[selected]} sourceRows={[selected]}><div className="ops-before-after" data-reviewed-rows><div><span>修改前</span><pre>{JSON.stringify(selected.current,null,2)}</pre></div><div><span>拟修改为</span><pre>{JSON.stringify(selected.proposed,null,2)}</pre></div></div><p>{selected.reason}</p><p className="ops-note">内容版本 {selected.action_hash?.slice(0,12)} · 规则 {selected.rule_version}</p><div className="ops-actions"><button className="ops-button" disabled={busy||selected.status!=='pending_review'} onClick={()=>task('/api/decide',{action_id:selected.action_id,expected_hash:selected.action_hash,decision:'approve'})}>批准这项方案</button><button className="ops-button ops-secondary" disabled={busy||selected.status!=='pending_review'} onClick={()=>task('/api/decide',{action_id:selected.action_id,expected_hash:selected.action_hash,decision:'reject'})}>拒绝</button></div></DataComponent>}</>}
 {tab==='comparison'&&<>{guidance('优化效果需要回执和足够的观察时间。','对比同一对象、同一币种、等长窗口，并等待归因补记。保留其它影响因素，不把所有数值变化直接归因于本次修改。')}<TableBlock id="ops-comparisons" queryId="comparisons" title="修改前后表现" rows={rows('comparisons')} columns={cols([['native_id','对象'],['state','观察状态'],['before_period','修改前窗口'],['after_period','修改后窗口'],['note','结论']],currency)} description="尚无已执行回执和成熟的前后窗口，暂不计算提升幅度。"/></>}
 <p className="ops-footer">快照采集于 {snapshot.generatedAt}。报表归因销售与店铺订单销售口径不同；空值表示未取得。</p>
 </div></div>;
}
