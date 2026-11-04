import {SchematicWorkspace} from './schematic-workspace';
import {PcbWorkspace} from './pcb-workspace';
import {jobTimingLabel,loadPreviewImage,applyJobPhase,geometryStatus} from './workflow-display';
import type {LiveDrawing} from './live-drawing';
import {api,fileUrl,requestId,type Artifact,type Context,type Project,type EventRecord,type Revision,type Job} from './api';
import type {ModelViewer,AssemblyView} from './model';
import {SourcesPanel} from './sources';
import {renderEngineeringReview} from './engineering-review';
import {FirmwarePanel} from './firmware';
import {ManufacturingPanel} from './manufacturing';
import {ManufacturingWorkspace} from './manufacturing-workspace';
import {ProcurementPanel} from './procurement';
import {ExternalManufacturingPanel} from './external-manufacturing';
import {OrdersPanel} from './orders';
import {JlcPanel} from './jlc';
import {StructuralPanel} from './structural';
import {MechanicalParameters} from './mechanical-parameters';
import {MechanicalWorkspace} from './mechanical-workspace';
const mechanicalWorkspace=new MechanicalWorkspace();
import {StructuralQuotesPanel} from './structural-quotes';
import {renderPcbModelSources} from './pcb-model-sources';
import {symbolResolutionDetails} from './symbol-resolution';
import {SupplierPanel} from './supplier';
import {SettingsPanel} from './settings';
import {confirmMaintenance} from './maintenance';
import {viewerArtifacts,isModelArtifact,hasAssemblyControls,type PcbViewMode} from './viewer-artifacts';
import {initI18n,locale,translateText} from './i18n';
import {renderAgentWorkflow} from './agent-workflow';
import {renderProductConcepts} from './product-concepts';

initI18n();

const el=<T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as T;
const input=(id:string)=>el<HTMLInputElement>(id);
const text=(id:string,value:string)=>{el(id).textContent=value;};
const make=(tag:string,cls='',content='')=>{const e=document.createElement(tag);e.className=cls;e.textContent=content;return e;};
let context:Context|null=null,projects:Project[]=[],view='overview',active=localStorage.getItem('formforge-project')||'';
let cursor=0,events:EventRecord[]=[],refreshTimer:number|undefined,toastTimer:number|undefined,dirty=false,editVersion=0;
let selectedArtifact='',scale=1,tx=0,ty=0,drag:{x:number;y:number}|null=null,model:ModelViewer|undefined,viewGeneration=0;
let liveDrawing:LiveDrawing|undefined,displayedImageScope='',displayedImageRevision='';
let previewRevision='',followRevision=true,renderSequence=0;
let linearMotionActive=false;
let pcbView:PcbViewMode='2d';
const modelView=()=>view==='mechanical'||(view==='pcb'&&pcbView==='3d');
const jobHistoryLimits=new Map<string,number>();
const sourcesPanel=new SourcesPanel(()=>context?.project,toast,refresh);
const firmwarePanel=new FirmwarePanel(el('firmware-panel'));
const mechanicalParameters=new MechanicalParameters(el('mechanical-parameters'),refresh,toast);
const manufacturingPanel=new ManufacturingPanel(el('manufacturing-panel'),refresh,toast);
const procurementPanel=new ProcurementPanel(el('procurement-panel'),refresh,toast);
const externalManufacturingPanel=new ExternalManufacturingPanel(el('external-manufacturing-panel'),refresh,toast,()=>void setView('sources'));
const ordersPanel=new OrdersPanel(el('orders-panel'),toast);
const supplierPanel=new SupplierPanel(el('supplier-panel'),toast);
const settingsPanel=new SettingsPanel(el('settings-panel'),toast,refresh,()=>context??undefined);
const schematicWorkspace=new SchematicWorkspace();
const pcbWorkspace=new PcbWorkspace();
const manufacturingViews=new Map<string,string>();
const manufacturingWorkspace=new ManufacturingWorkspace();
const jlcPanel=new JlcPanel(el('jlc-panel'),refresh,toast,()=>{if(context)manufacturingViews.set(context.project.id,'orders');void renderView().catch(e=>toast(e.message,true));});
const structuralPanel=new StructuralPanel(el('structural-panel'),refresh,toast);
const structuralQuotesPanel=new StructuralQuotesPanel(el('structural-quotes-panel'),refresh,toast);
const mechanicalReads=new Map<string,any>();
const observedGeometryArtifacts=new Set<string>();
const observedInsertionArtifacts=new Set<string>();
const visibleRevisions=()=>context?.revisions.filter(r=>view==='mechanical'?r.module==='mechanical':view==='firmware'?r.module==='firmware':(r.module||'eda')==='eda')||[];
const moduleHead=(r?:Revision)=>r?.module==='manufacturing'?context?.project.moduleHeads?.manufacturing:r?.module==='mechanical'?context?.project.moduleHeads?.mechanical:r?.module==='firmware'?context?.project.moduleHeads?.firmware:context?.project.headRevisionId;
const statuses:Record<string,string>={queued:'等待执行',running:'执行中',completed:'执行完成',failed:'未完成',cancelled:'已取消',interrupted:'已中断'};
const terminal=(status:string)=>!['queued','running'].includes(status);
const defaultPreviewRevision=()=>{const revisions=visibleRevisions();return revisions.find(r=>r.id===moduleHead(r))?.id||revisions[0]?.id||'';};
const rejected=(r:Revision)=>!!r.validation&&!['passed','draft','pending','ready'].includes(r.validation.status);
const time=(date:string)=>new Date(date).toLocaleTimeString(locale(),{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
function mergeEvents(incoming:EventRecord[]){events=Array.from(new Map([...events,...incoming].map(e=>[e.seq,e])).values()).sort((a,b)=>a.seq-b.seq).slice(-150);}
function toast(message:string,error=false){clearTimeout(toastTimer);text('toast',message);el('toast').classList.toggle('error',error);el('toast').hidden=false;toastTimer=window.setTimeout(()=>{el('toast').hidden=true;},error?9000:3500);}
function setLiveConnection(connected:boolean,label:string){text('live-label-text',label);el('live-status').classList.toggle('disconnected',!connected);}
function act(fn:()=>Promise<unknown>){return async()=>{try{await fn();}catch(error){toast((error as Error).message,true);}};}
function badge(status:string,label?:string){const cls=status==='completed'?'success':status==='running'||status==='queued'?'running':status==='failed'?'failed':'neutral';return make('span','badge '+cls,label||statuses[status]||status);}
function requestRefresh(){clearTimeout(refreshTimer);refreshTimer=window.setTimeout(()=>{void refresh().catch(e=>toast(e.message,true));},150);}

function closeNavigation(){
  document.body.classList.remove('navigation-open');
  el('sidebar-backdrop').hidden=true;
  el('sidebar-toggle').setAttribute('aria-expanded','false');
  el('sidebar').removeAttribute('inert');
  if(window.matchMedia('(max-width: 760px)').matches)el('sidebar').setAttribute('inert','');
}
function renderProjects(){
  const host=el('project-list'),scrollTop=host.scrollTop;
  const focused=host.contains(document.activeElement)?(document.activeElement as HTMLElement).dataset.projectId:undefined;
  const query=input('project-search').value.trim().toLocaleLowerCase();
  host.replaceChildren();
  const matches=projects.filter(p=>p.name.toLocaleLowerCase().includes(query));
  text('project-total',query?`${matches.length} / ${projects.length}`:String(projects.length));
  el('project-search-empty').hidden=!query||!!matches.length;
  for(const p of matches){
    const b=make('button','project-item'+(p.id===active?' selected':''),p.name);
    b.title=p.name;b.dataset.projectId=p.id;b.translate=false;
    if(p.id===active)b.setAttribute('aria-current','true');
    b.onclick=act(async()=>{if(active===p.id&&context?.project.id===p.id&&el('project-transition').hidden)return;active=p.id;localStorage.setItem('formforge-project',active);closeNavigation();renderProjects();await refresh(false);if(window.matchMedia('(max-width: 760px)').matches)el('sidebar-toggle').focus();});
    host.append(b);
    if(focused===p.id)b.focus({preventScroll:true});
  }
  host.scrollTop=scrollTop;
}

function projectTransition(state:'loading'|'ready'|'error'){
  const loading=state!=='ready',host=el('project-transition');
  host.hidden=!loading;
  host.style.top=`${document.querySelector('.topbar')!.getBoundingClientRect().height}px`;
  document.body.classList.toggle('project-loading',loading);
  document.querySelectorAll<HTMLElement>('.content-layout,.page-heading,#schematic-navigation').forEach(node=>{node.inert=loading;});
  el('workspace').setAttribute('aria-busy',String(state==='loading'));
  text('project-transition-title',translateText(state==='error'?'工程加载失败':'正在切换工程…'));
  text('project-transition-name',projects.find(p=>p.id===active)?.name||'');
  text('project-transition-detail',translateText(state==='error'?'请重试，或从左侧选择其他工程。':'正在读取工程内容与预览，请稍候。'));
  el('project-transition-retry').hidden=state!=='error';
}
el('project-transition-retry').onclick=act(()=>refresh(false));

let refreshSequence=0;
async function refresh(reloadProjects=true){
  const sequence=++refreshSequence;
  const transitioning=context?.project.id!==active||!el('project-transition').hidden;
  if(transitioning){projectTransition('loading');renderSequence++;viewGeneration++;liveDrawing?.deactivate();firmwarePanel.deactivate();}
  try{
  const nextProjects=reloadProjects?await api<Project[]>('projects'):projects;
  if(sequence!==refreshSequence)return;
  projects=nextProjects;
  if(!projects.some(p=>p.id===active))active=projects[0]?.id||'';
  const projectAtRequest=active;
  const selected=context?.project.id===active&&!followRevision&&previewRevision?'&revisionId='+encodeURIComponent(previewRevision):'';
  const next=active?await api<Context>('projects/'+active+'?compact=true'+selected):null;
  if(sequence!==refreshSequence||projectAtRequest!==active)return;
  const switched=context?.project.id!==next?.project.id;
  // Preserve explicitly opened history details while their task is unchanged.
  if(next&&context?.project.id===next.project.id)for(let i=0;i<next.jobs.length;i++){
    const j=next.jobs[i],old=context.jobs.find(x=>x.id===j.id);
    if(j._summary&&old&&!old._summary&&old.status===j.status&&old.finishedAt===j.finishedAt)next.jobs[i]=old;
  }
  context=next;
  if(switched){
    dirty=false;selectedArtifact='';previewRevision='';followRevision=true;
    el<HTMLDetailsElement>('revision-history').open=false;
    if(active)void api<{events:EventRecord[]}>('event-snapshot?projectId='+encodeURIComponent(active)).then(recent=>{
      if(projectAtRequest!==active)return;mergeEvents(recent.events);renderEvents();
    }).catch(error=>{if(projectAtRequest===active)toast(error.message,true);});
  }
  if(followRevision||!visibleRevisions().some(r=>r.id===previewRevision))previewRevision=defaultPreviewRevision();
  renderProjects();
  text('project-name',context?.project.name||translateText('未选择工程'));
  input('import-button').disabled=!context;
  input('project-export-button').disabled=!context||context.jobs.some(j=>!terminal(j.status));
  input('rename-project').disabled=!context;
  input('delete-project').disabled=!context||context.jobs.some(j=>!terminal(j.status));
  input('inspect-button').disabled=!context?.revisions.some(r=>(r.module||'eda')==='eda')||context.jobs.some(j=>!terminal(j.status))||!context.capabilities.adapters[0].available;
  el('welcome').hidden=!!context;el('project-overview').hidden=!context;
  for(const id of ['requirements','preferences','save-context'])input(id).disabled=!context;
  const effective=el('effective-preferences');effective.replaceChildren();effective.hidden=true;
  renderAgentWorkflow(el('agent-workflow'),context);
  renderProductConcepts(el('product-concepts'),context);
  if(context){
    const p=context.project;
    text('overview-project-name',p.name);
    text('context-version','工程版本 '+p.version);
    const updated=p.updatedAt?new Date(p.updatedAt):null;
    text('stat-updated',updated&&!Number.isNaN(updated.getTime())?updated.toLocaleString(locale(),{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'—');
    for(const [id,value] of [['info-description',p.description]]){
      const field=el(id);field.translate=!value;field.textContent=value||'尚未填写';field.classList.toggle('fact-empty',!value);
    }
    el('info-description').parentElement!.hidden=!p.description||p.description===p.specification.text;
    const modules=el('info-modules');modules.replaceChildren();
    const heads=[['原理图 / PCB',p.headRevisionId],['机械',p.moduleHeads?.mechanical],['固件',p.moduleHeads?.firmware],['制造',p.moduleHeads?.manufacturing]];
    for(const [label,head] of heads){const tag=make('span','small-tag'+(head?' module-recorded':''));tag.append(make('span','',label),make('span','',head?'已有版本':'尚未建立'));modules.append(tag);}
    if(!dirty){input('requirements').value=p.specification.text||'';input('preferences').value=p.preferences.text||'';editVersion=p.version;text('draft-notice','');}
    else if(p.version!==editVersion)text('draft-notice','工程已有新版本。你的输入已保留，保存时会检查冲突。');
    const rules=Array.isArray(p.preferences.rules)?p.preferences.rules.filter((rule:any)=>rule?.scope==='pcb'):[];
    const labels:Record<string,string>={rounded:'圆角',square:'直角',balanced:'平衡',tidy:'整齐优先',shortest:'短路径优先',compact:'紧凑',serviceable:'维修空间优先',functional:'功能标识',minimal:'精简',detailed:'详细','project-rules':'项目工艺规则',conservative:'保守尺寸'};
    for(const rule of rules){const raw=rule.value,shown=raw&&typeof raw==='object'?(labels[raw.mode]||raw.mode)+(raw.radiusMm?` R${raw.radiusMm}mm`:''):labels[String(raw)]||String(raw);effective.append(make('span','small-tag',`${rule.label||rule.id}：${shown} · ${rule.source==='personal-default'?'个人默认':'项目设置'}`));}
    effective.hidden=!rules.length;
    text('stat-revisions',String(context.revisions.length).padStart(2,'0'));
    text('stat-jobs',String(context.jobs.length).padStart(2,'0'));
    text('stat-artifacts',String(context.artifacts.filter(a=>!['log'].includes(a.kind)).length).padStart(2,'0'));
    text('revision-count',context.revisions.length+' 份修订（含候选）');
    const revisions=el('revision-list'),history=el('revision-history-list');revisions.replaceChildren();history.replaceChildren();
    el('revision-history').hidden=context.revisions.length<=5;
    text('revision-history-label',`查看更早 ${Math.max(0,context.revisions.length-5)} 份修订与候选`);
    if(!context.revisions.length)revisions.append(make('p','empty-small','还没有输入版本。导入完整 KiCad 工程后开始检查。'));
    for(const [index,r] of context.revisions.entries()){
      const row=make('div','revision-row'),info=make('div');
      info.append(make('div','revision-name',r.module==='manufacturing'?'采购计划 · '+(r.procurementDesign?.mode==='draft'?'草案':'资料审核'):r.module==='firmware'?'固件 · '+r.firmwareDesign?.profile:r.module==='mechanical'?'机械设计 · '+r.mechanicalDesign?.kind:(r.origin?.startsWith('pcb_')?r.entrypoints.board:r.entrypoints.schematic)||r.entrypoints.board||r.id));
      info.append(make('small','',revisionName(r)+' · '+(r.manifestFileCount??r.manifest.length)+' 个文件 · '+(r.module==='manufacturing'?'独立采购版本':r.module==='firmware'?'独立固件版本':r.module==='mechanical'?'独立机械版本':r.origin?.startsWith('pcb_')?'PCB 草稿':r.origin==='edited'?'修改生成':r.origin==='initialized'?'从明确需求建立':'已有工程导入')));
      const status=r.id===moduleHead(r)?(r.validation?.status==='draft'?'当前 PCB 草稿':r.module==='manufacturing'?'当前采购计划':r.module==='mechanical'?'当前机械版本':r.module==='firmware'?'当前固件版本':'当前版本'):r.validation?.status==='pending'?'检查中':r.validation?.status==='ready'?(r.parentRevisionId===p.headRevisionId?'待审阅':'历史候选'):rejected(r)?'未采纳':'历史版本';
      row.append(info,make('span','small-tag',status));(index<5?revisions:history).append(row);
    }
    renderChecks();
  }
  renderJobs();renderEvents();await renderView();
  if(sequence===refreshSequence)projectTransition('ready');
  }catch(error){
    if(sequence!==refreshSequence)return;
    if(transitioning)projectTransition('error');
    throw error;
  }
}

function renderChecks(){
  const host=el('check-summary');host.replaceChildren();
  const mechanical=context?.jobs.find(j=>j.kind==='mechanical.design'&&j.status==='completed'&&j.result?.revisionId===context?.project.moduleHeads?.mechanical);
  if(mechanical?.result){const r=mechanical.result;const row=make('div','check-item');row.append(make('strong','','机械实体与 STEP 回读'),make('span','badge '+(r.checksPassed?'success':'warning'),r.checksPassed?'名义检查通过':'需处理'));host.append(row,make('p','field-note',`${r.parts.length} 个原生零件 · 配合、公差、载荷与制造需独立验证。`));}
  if(mechanical?.result){const e=mechanical.result.engineering;const row=make('div','check-item');const complete=e?.status==='checked-critical-regions';row.append(make('strong','','工艺与装配检查'),make('span','badge '+(complete?'success':'warning'),!e||e.status==='unplanned'?'尚未规划工艺':e.status==='failed'?'发现工程问题':complete?'已检查关键部位':'工程检查待确认'));host.append(row);if(e?.material)host.append(make('p','field-note',`${e.process} · ${e.material} · ${e.finish==='unpainted'?'原色未喷涂':'表面处理需核对'}；关键截面检查不代表全表面壁厚或实物验收。`));if(e?.retentions?.length)host.append(make('p','field-note',`紧固连接 ${e.joints?.length||0} 处 · 限位/防脱 ${e.retentions.length} 组；按声明位移检查。`));for(const note of e?.reviews||[])host.append(make('p','field-note',String(note)));}
  const firmware=context?.jobs.find(j=>j.kind==='firmware.design'&&j.status==='completed'&&j.result?.revisionId===context?.project.moduleHeads?.firmware);
  if(firmware?.result){const r=firmware.result;const row=make('div','check-item');row.append(make('strong','','固件编译与映像回读'),make('span','badge '+(r.checksPassed?'success':'warning'),r.checksPassed?'构建检查通过':'需处理'));host.append(row,make('p','field-note','独立固件版本；行为、时序和上板运行尚未验证。'));}
  const latest=context?.jobs.find(j=>['eda.inspect','schematic.edit','schematic.initialize','pcb.initialize','pcb.edit','pcb.route','pcb.pair_route','pcb.sync'].includes(j.kind)&&j.status==='completed'&&j.result?.checks&&(j.result?.revisionId||j.inputRevision)===context?.project.headRevisionId);
  if(!latest?.result){text('check-badge',firmware?'固件构建已核对':mechanical?'机械名义检查通过':'等待检查');if(!mechanical&&!firmware)host.textContent='运行检查后，这里显示真实 ERC / DRC 结果。';return;}
  const result=latest.result;
  const old=(latest.result?.revisionId||latest.inputRevision)!==context?.project.headRevisionId;
  text('check-badge',old?'旧输入的报告':result.checksPassed?'所运行规则通过':result.draftChecksPassed?'PCB 草稿 · 检查待完成':'发现待处理问题');
  for(const check of result.checks){const row=make('div','check-item'),info=make('div');info.append(make('strong','',check.kind==='connectivity'?'连接目标核对':check.kind.toUpperCase()+' 原生检查'),make('small','',`${check.errors} 个错误 · ${check.warnings} 个警告${check.kind==='drc'?' · '+check.unconnected+' 处未连':''}`));row.append(info,make('span','badge '+(check.status==='passed'?'success':'warning'),check.status==='passed'?'通过':'需处理'));host.append(row);}
  host.append(make('p','quiet-note',old?'工程输入已更新，此报告仍属于旧版本；请重新检查。':'这是原生规则检查结果。功能、制造与实物验证仍需独立进行。'));
  const first=result.checks.flatMap((c:any)=>c.items||[]).slice(0,3);
  first.forEach((finding:any)=>host.append(make('p','empty-small',finding.description||finding.type)));
}

function placementReviewView(review:any){
  const details=make('details','quiet-note');
  details.append(make('summary','',`摆放复核：${review.reviewCount} 组绕行值得检查 · 已分析 ${review.seriesPairCount} 组二端串联连接`));
  for(const p of review.pairs.filter((p:any)=>p.needsPlacementReview))details.append(make('p','field-note',`${p.references.join(' / ')} · ${p.net}：连线 ${p.lengthMm.toFixed(3)} mm，几何下界 ${p.octilinearLowerBoundMm.toFixed(3)} mm · ${p.bendCount} 次转弯${p.lockedReferences.length?' · 已锁定 '+p.lockedReferences.join('、'):''}。先比较旋转、交换位置或横竖串排，按连接焊盘中心对齐，同时检查供电入口和回流。`));
  details.append(make('p','field-note',`这是局部摆放提示；下界未计障碍和出线直段，不代表已有可行路线或全板最优。${review.uncoveredLinks.length} 组复杂连接未纳入。候选仍须检查庭院、本体、净距、连接和已有电气条件。`));
  return details;
}

function layoutFeedbackView(feedback:any,proof?:any){
  const details=make('details','quiet-note');
  details.append(make('summary','',`布局反馈：间隙覆盖 ${feedback.coveredFootprintCount}/${feedback.footprintCount} · 未覆盖 ${feedback.uncoveredFootprintCount} · 既有冲突 ${feedback.existingConflictCount}`));
  details.append(make('p','field-note',`本次${feedback.enforced?'已启用间隙避让':'仅作间隙审核'}${proof?' · 新增铜保存复核'+(proof.passed?'通过':'未通过'):''}。仅覆盖推定的表面SMD焊盘间隙；完整本体、未识别封装与装配空间尚未验证。`));
  const covered=(feedback.footprints||[]).filter((fp:any)=>fp.status==='covered'&&fp.polygonMm?.length>=3);
  if(covered.length){
    const points=covered.flatMap((fp:any)=>fp.polygonMm),xs=points.map((p:number[])=>p[0]),ys=points.map((p:number[])=>p[1]);
    const x=Math.min(...xs),y=Math.min(...ys),w=Math.max(...xs)-x,h=Math.max(...ys)-y,pad=Math.max(w,h)*.08+1;
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',`${x-pad} ${y-pad} ${w+2*pad} ${h+2*pad}`);svg.setAttribute('role','img');svg.setAttribute('aria-label','已识别器件间隙分布');svg.style.cssText='width:100%;height:230px;background:#f5f8fb;border-radius:8px';
    const blocked=new Set([...(feedback.searchObstacles||[]),...(feedback.existingConflicts||[])].map((r:any)=>r.footprintId));
    for(const fp of covered){
      const polygon=document.createElementNS(svg.namespaceURI,'polygon');polygon.setAttribute('points',fp.polygonMm.map((p:number[])=>p.join(',')).join(' '));polygon.setAttribute('fill',blocked.has(fp.footprintId)?'#e99a7d':'#90bacd');polygon.setAttribute('stroke',blocked.has(fp.footprintId)?'#a94b2a':'#337b98');polygon.setAttribute('stroke-width',String(Math.max(w,h)/300));
      const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=fp.reference+' · '+fp.layer+' · '+fp.footprint;polygon.append(title);svg.append(polygon);
      const label=document.createElementNS(svg.namespaceURI,'text');label.setAttribute('x',String(fp.polygonMm.reduce((s:number,p:number[])=>s+p[0],0)/fp.polygonMm.length));label.setAttribute('y',String(Math.min(...fp.polygonMm.map((p:number[])=>p[1]))-.2));label.setAttribute('font-size',String(Math.max(w,h)/30));label.setAttribute('text-anchor','middle');label.setAttribute('fill','#213d4a');label.textContent=fp.reference;svg.append(label);
    }
    details.append(svg,make('p','field-note','已识别间隙的板坐标分布（mm）；红色位号有既有铜冲突或整段检查曾受阻。图形仅表示推定间隙，未覆盖器件未绘出。'));
  }
  const reasons:Record<string,string>={requires_pure_surface_smd_pads:'含通孔或非纯表面焊盘',custom_pad_geometry:'自定义焊盘',unsupported_fab_shape:'Fab曲线/形状尚不支持',missing_or_ambiguous_closed_fab:'缺少单一闭合Fab轮廓',degenerate_fab:'Fab轮廓退化',ambiguous_fab:'Fab轮廓歧义',nonconvex_fab:'Fab轮廓非凸',center_or_exposed_pad:'含中心/暴露焊盘',unsupported_pad_arrangement:'焊盘排列未覆盖',interior_pad_arrangement:'含内部焊盘排列',gap_below_0_2mm:'中心间隙不足0.2mm',gap_overlaps_pad:'间隙与焊盘重叠',empty_fab_gap_intersection:'Fab与间隙无有效交集',unsupported_footprint_layer:'器件面尚不支持'};
  for(const hit of feedback.searchObstacles||[])details.append(make('p','field-note',`${hit.reference}${hit.locked?'（已锁定）':''}：观察到 ${hit.rejectedCheckCount} 次整段检查受阻，核对该器件附近逃线空间。该计数不表示独立冲突数，也不证明无法布通。`));
  if(feedback.rasterObstacles?.length)details.append(make('p','field-note','本轮网格避让位号：'+feedback.rasterObstacles.map((r:any)=>r.reference+(r.locked?'（已锁定）':'')).join('、')+'。搜索受限时先核对这些区域及两端逃线。'));
  for(const item of (feedback.unresolvedConnections||[]).slice(0,12))details.append(make('p','field-note',`${item.net} · ${item.fromReference} → ${item.toReference}：${item.reason==='no_legal_pad_exit'?'未找到合法引脚出口':item.reason==='search_budget'?'达到搜索预算':item.reason==='max_expansions'?'达到展开上限':'本轮未找到路径'}${item.sourcePorts!==undefined?'（两端出口 '+item.sourcePorts+' / '+item.targetPorts+'）':''}。先核对两端逃线及上述避让区域；扩大预算/层权限与移动器件需按实际约束决定。`));
  const conflicting=[...new Set((feedback.existingConflicts||[]).map((c:any)=>c.reference))];
  if(conflicting.length)details.append(make('p','field-note','既有铜穿越推定间隙：'+conflicting.join('、')+'；本次未自动移除。'));
  for(const fp of feedback.footprints||[])if(fp.status==='uncovered')details.append(make('p','field-note',`${fp.reference}：${reasons[fp.reason]||fp.reason}`));
  details.append(make('p','field-note',`反馈输入：${feedback.revisionId} · PCB SHA ${feedback.boardSha256}`));
  return details;
}

function renderJobs(){
  const host=el('job-list');host.replaceChildren();
  const limit=jobHistoryLimits.get(active)||8;
  const scoped=(context?.jobs||[]).filter(job=>view!=='manufacturing'||!input('manufacturing-only-tasks').checked||job.kind.startsWith('manufacturing.'));
  const jobs=scoped.slice(0,limit);
  if(!jobs.length){host.append(make('div','empty-small','暂无任务'));return;}
  for(const job of jobs){
    const row=make('div','job-row'),symbol=make('span','job-symbol '+job.status,job.kind==='eda.import'?'↓':'⌘');symbol.setAttribute('aria-hidden','true');
    const info=make('div','job-info');
    const detailedError=!!job.error&&(job.error.length>300||job.error.includes('Traceback'));
    info.append(make('strong','',job.kind==='components.freeze_local'?'冻结本地器件资料':job.kind==='components.query'?'查询器件与库存':job.kind==='manufacturing.mechanical_prepare'?'准备结构件制造资料':job.kind==='manufacturing.jlc_quote'?'获取官方工艺计价':job.kind==='manufacturing.jlc_sync'?'核对官方订单':job.kind==='manufacturing.external_review'?'审核外部制造文件':job.kind==='manufacturing.procurement'?'核对采购计划':job.kind==='manufacturing.prepare'?'准备制造审核资料':job.kind==='firmware.device'?'固件设备操作':job.kind==='firmware.design'?'构建固件与回读验证':job.kind==='mechanical.insertion_check'?'检查配对件与插拔空间':job.kind==='pcb.geometry_export'?'导出板体与器件几何':job.kind==='mechanical.design'?'构建机械模型与验证':job.kind==='eda.import'?'冻结工程输入':job.kind==='schematic.initialize'?'从需求建立原理图':job.kind==='schematic.edit'?'修改原理图与验证':job.kind==='schematic.review'?'核对电气约束':job.kind==='pcb.handoff'?'生成 PCB 连接交接':job.kind==='pcb.initialize'?'创建真实 PCB 草稿':job.kind==='pcb.edit'?'修改 PCB 草稿':job.kind==='pcb.route'?'自研选网布线':job.kind==='pcb.pair_route'?'生成成组走线':job.kind==='pcb.sync'?'同步新版原理图到PCB':job.kind==='pcb.reference_check'?'检查参考地覆盖':'KiCad 10 工程检查'),make('p','',detailedError?'原生工具执行失败，候选未采纳。':job.error||job.phase),make('small','',time(job.createdAt)+' · '+job.id.slice(-8)));
    const clock=make('small','job-clock',jobTimingLabel(job));clock.dataset.jobId=job.id;info.append(clock);
    if(detailedError){const details=make('details','quiet-note');details.append(make('summary','','查看工具错误详情'),make('p','',job.error));info.append(details);}
    const reviewed=context?.revisions.find(r=>r.createdByJobId===job.id);
    const reviewLabel=job.result?.adoptionMode==='review'&&job.result?.readyForAdoption?(reviewed?.validation?.adopted?'审阅后已采纳':job.inputRevision===context?.project.headRevisionId?'等待审阅':'历史候选'):'';
    const actions=make('div','job-actions');actions.append(reviewLabel?make('span','badge '+(reviewed?.validation?.adopted?'success':'neutral'),reviewLabel):job.result?.noop?make('span','badge neutral','无变化 · 未重新验收'):job.kind==='manufacturing.mechanical_prepare'&&job.result?make('span','badge '+(job.result.fileChecksPassed?'success':'warning'),job.result.fileChecksPassed?'STEP已核对 · 未放行':'STEP需处理'):job.kind==='manufacturing.external_review'&&job.result?make('span','badge '+(job.result.checksPassed?'success':'warning'),job.result.checksPassed?'文件检查通过 · 未放行':'文件需处理'):job.kind==='manufacturing.procurement'&&job.result?make('span','badge '+(job.result.adopted&&job.result.procurementReviewed?'success':'warning'),!job.result.adopted?'未采纳':job.result.procurementReviewed?'料号资料通过':'采购草案'):job.kind==='manufacturing.prepare'&&job.result?make('span','badge '+(job.result.fileConsistencyPassed?'success':'warning'),job.result.fileConsistencyPassed?'资料一致 · 未放行':'资料需处理'):job.kind==='mechanical.insertion_check'&&job.result?make('span','badge '+(job.result.checksPassed?'success':'warning'),job.result.checksPassed?'声明净空通过':'插拔空间需处理'):job.kind==='pcb.geometry_export'&&job.result?make('span','badge '+(job.result.checksPassed?'success':'warning'),geometryStatus(job.result)):job.kind==='pcb.reference_check'&&job.result?make('span','badge '+(job.result.referenceCheckPassed?'success':'warning'),job.result.referenceCheckPassed?'所查几何已核对':'参考覆盖需处理'):['firmware.design','mechanical.design','schematic.edit','pcb.initialize','pcb.edit','pcb.route','pcb.pair_route','pcb.sync'].includes(job.kind)&&job.result?.adopted===false?make('span','badge warning','未采纳'):job.kind.startsWith('pcb.')&&job.result?.adopted&&!job.result?.checksPassed?make('span','badge warning','草稿已保存'):job.kind==='schematic.review'&&job.result?.checksPassed===false?make('span','badge warning','检查需处理'):badge(job.status));
    if(job._summary){const detail=make('button','text-button','查看任务详情');detail.onclick=act(async()=>{const pid=active;const full=await api<Job>('jobs/'+job.id);if(context?.project.id!==pid)return;context.jobs=context.jobs.map(j=>j.id===full.id?full:j);renderJobs();});actions.append(detail);}
    if(!terminal(job.status)){const cancel=make('button','text-button',job.cancelRequested?'停止中':'取消') as HTMLButtonElement;cancel.disabled=!!job.cancelRequested;cancel.onclick=act(async()=>{cancel.disabled=true;try{await api('jobs/'+job.id+'/cancel','POST');requestRefresh();}catch(error){cancel.disabled=false;throw error;}});actions.append(cancel);}
    else {const report=context?.artifacts.find(a=>a.jobId===job.id&&a.kind===(job.kind==='components.freeze_local'?'component_library_report':job.kind==='components.query'?'component_observation':['manufacturing.jlc_quote','manufacturing.jlc_sync'].includes(job.kind)?'jlc_api_receipt':job.kind==='manufacturing.mechanical_prepare'?'structural_report':job.kind==='manufacturing.external_review'?'external_cam_report':job.kind==='manufacturing.procurement'?'procurement_report':job.kind==='manufacturing.prepare'?'manufacturing_report':job.kind==='firmware.device'?'firmware_device_report':job.kind==='firmware.design'?'firmware_report':job.kind==='mechanical.insertion_check'?'mechanical_insertion_report':job.kind==='pcb.geometry_export'?'pcb_geometry_report':job.kind==='mechanical.design'?'mechanical_report':job.kind==='schematic.edit'?'edit_report':job.kind==='schematic.review'?'design_review':job.kind==='pcb.handoff'?'pcb_handoff_report':job.kind==='pcb.reference_check'?'pcb_reference_report':job.kind.startsWith('pcb.')?'pcb_edit_report':'summary'));if(report){const a=make('a','text-button','报告 ↗') as HTMLAnchorElement;a.href=fileUrl(report)+'?download=true';actions.append(a);}}
    if(['pcb.route','pcb.pair_route'].includes(job.kind)){const plan=context?.artifacts.find(a=>a.jobId===job.id&&a.kind==='pcb_route_plan');if(plan){const link=make('a','text-button','找路依据 ↗') as HTMLAnchorElement;link.href=fileUrl(plan)+'?download=true';actions.append(link);}}
    if(job.kind==='firmware.device'){const log=context?.artifacts.find(a=>a.jobId===job.id&&a.kind==='firmware_device_log');if(log){const link=make('a','text-button','设备日志 ↗') as HTMLAnchorElement;link.href=fileUrl(log)+'?download=true';actions.append(link);}}
    if(job.result?.layoutFeedback)info.append(layoutFeedbackView(job.result.layoutFeedback,job.result.bodyAvoidance));
    const stages=context?.artifacts.find(a=>a.jobId===job.id&&a.kind==='job_stage_timings');
    if(stages){const link=make('a','text-button','阶段耗时 ↗') as HTMLAnchorElement;link.href=fileUrl(stages)+'?download=true';actions.append(link);}
    const metrics=context?.artifacts.find(a=>a.jobId===job.id&&a.kind==='pcb_read_metrics');
    if(metrics){const link=make('a','text-button','读取耗时 ↗') as HTMLAnchorElement;link.href=fileUrl(metrics)+'?download=true';actions.append(link);}
    row.append(symbol,info,actions);host.append(row);
  }
  const remaining=scoped.length-jobs.length;
  if(remaining>0){const more=make('button','text-button',`查看更早任务（还有 ${remaining} 条）`);more.onclick=()=>{jobHistoryLimits.set(active,limit+20);renderJobs();};host.append(more);}
}
window.setInterval(()=>{
  for(const clock of document.querySelectorAll<HTMLElement>('.job-clock')){
    const job=context?.jobs.find(j=>j.id===clock.dataset.jobId);
    if(job&&!terminal(job.status))clock.textContent=jobTimingLabel(job);
  }
},1000);

const eventTitles:Record<string,string>={'project.workflow_updated':'更新设计与使用检查','supplier.approval':'更新提交确认','supplier.attempt':'更新下单执行步骤','supplier.session':'更新供应商登录状态','structural_quote.created':'建立结构报价资料','structural_quote.observed':'记录结构平台观察','order.adopted':'关联既有平台订单','order.observed':'记录平台观察','source.upload.started':'开始接收工程资料','source.upload.progress':'正在接收工程资料','source.registered':'资料已登记','source.upload.aborted':'资料上传已取消','project.created':'创建工程','project.updated':'更新需求与偏好','project.exported':'导出工程包','project.imported':'导入工程包','revision.imported':'工程输入已冻结','job.queued':'任务已排队','job.started':'开始执行','artifact.created':'生成工程产物','job.finished':'任务结束'};
Object.assign(eventTitles,{'revision.candidate':'生成修改候选','revision.validated':'修改版本检查结束'});
function renderEvents(){const host=el('activity-list');host.replaceChildren();const recent=events.filter(e=>e.projectId===active).slice(-9).reverse();if(!recent.length){host.append(make('p','empty-small','暂无变化'));return;}for(const event of recent){const item=make('div','activity-item',event.payload.phase||eventTitles[event.type]||event.type);item.append(make('time','',time(event.createdAt)));host.append(item);}}

const viewInfo:Record<string,string>={sources:'工程资料',overview:'工程概览',schematic:'原理图',pcb:'PCB',firmware:'固件',manufacturing:'制造',mechanical:'机械设计',settings:'设置'};
async function setView(next:string){
  view=next;closeNavigation();window.scrollTo(0,0);selectedArtifact='';
  if(next==='settings'||location.hash==='#settings')history.replaceState(null,'',next==='settings'?'#settings':location.pathname+location.search);
  if(!visibleRevisions().some(r=>r.id===previewRevision)||followRevision)previewRevision=defaultPreviewRevision();
  document.querySelectorAll('.nav-item').forEach(b=>{const current=(b as HTMLElement).dataset.view===view;b.classList.toggle('active',current);if(current)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  text('view-title',viewInfo[view]);await ensureRevisionDetail();await renderView();renderJobs();if(window.matchMedia('(max-width: 760px)').matches)el('sidebar-toggle').focus();
}
async function ensureRevisionDetail(){
  if(!context?.revisions.find(r=>r.id===previewRevision)?._summary)return;
  const pid=active,rid=previewRevision;
  const next=await api<Context>('projects/'+pid+'?compact=true&revisionId='+encodeURIComponent(rid));
  if(context?.project.id===pid&&previewRevision===rid)context=next;
}
function revisionName(revision:Revision){return '修订 '+revision.id.slice(-8);}
function renderRevision(){
  const select=el<HTMLSelectElement>('preview-revision');select.replaceChildren();
  for(const r of visibleRevisions()){const option=document.createElement('option');option.value=r.id;option.textContent=revisionName(r)+(r.id===moduleHead(r)?(r.validation?.status==='draft'?' · 当前 PCB 草稿':r.module==='mechanical'?' · 当前机械版本':' · 当前版本'):r.validation?.status==='pending'?' · 检查中':r.validation?.status==='ready'?(r.parentRevisionId===context?.project.headRevisionId?' · 待审阅':' · 历史候选'):rejected(r)?' · 未采纳':'');select.append(option);}
  select.value=previewRevision;
  text('follow-revision',followRevision?'正在跟随当前版本':'跟随当前版本');el('follow-revision').classList.toggle('following',followRevision);
  const revision=context?.revisions.find(r=>r.id===previewRevision);
  const current=revision?.id===moduleHead(revision);
  const state=revision?.validation?.status;
  text('revision-state',current?(state==='draft'?'当前 PCB 草稿':'当前版本'):state==='pending'?'候选 · 检查中':state==='ready'?(revision?.parentRevisionId===context?.project.headRevisionId?'候选 · 待审阅':'历史候选'):revision&&rejected(revision)?'候选 · 未采纳':'历史版本');
  el('revision-state').classList.toggle('pending',!!revision&&!current&&rejected(revision));
  text('revision-message',revision?.message||(revision?.origin==='initialized'?'从明确需求建立工程':'已有工程导入'));
  const changes=el('revision-changes'),findings=el('revision-findings');changes.replaceChildren();findings.replaceChildren();
  if(view==='mechanical'){renderMechanicalRevision(revision);return;}
  if(view==='firmware'){renderFirmwareRevision(revision);return;}
  let moreChanges:HTMLElement|undefined;
  for(const [index,change] of (revision?.changes||[]).entries()){const row=make('p');const name=change.reference||change.sheet||change.libraryId||'工程';let detail='';
    if(change.kind==='set_property')detail=`${change.property}：${change.before} → ${change.after}`;
    else if(change.kind==='move_field')detail=`${change.property} 位置：${change.before.join(', ')} → ${change.after.join(', ')} mm`;
    else if(change.kind==='set_text')detail=`说明：${change.after}`;
    else if(change.kind==='reroute_wire')detail=`导线路径：${change.before.length-1} 段 → ${change.after.length-1} 段`;
    else if(change.kind==='add_symbol')detail=`新增器件 ${change.after.value} · ${change.after.at.join(', ')} mm`;
    else if(change.kind==='add_library_symbol')detail=`从库新增 ${change.symbol} · ${change.after.value} · 来源版本 ${change.sourceVersion}${change.symbolResolution?' · 继承 '+change.symbolResolution.baseSymbol+' 的图形和引脚；父链与展开摘要已记录':''}`;
    else if(change.kind==='import_symbol_library')detail=`冻结符号库 · ${change.after.sourceVersion} · ${change.after.symbolCount} 个符号`;
    else if(change.kind==='import_footprint')detail=`冻结外部封装 ${change.after.footprint} · ${change.after.sourceVersion} · 来源和原始摘要已保存`;
    else if(change.kind==='initialize_schematic')detail=`从已确认需求建立 ${change.after.entrypoint}`;
    else if(change.kind==='move_symbol')detail=`移动器件：${change.before.join(', ')} → ${change.after.join(', ')} mm`;
    else if(change.kind==='remove_symbol')detail='删除器件并核对剩余引脚目标';
    else if(change.kind==='add_label')detail=`新增网络标签 ${change.after.text}`;
    else if(change.kind==='remove_label')detail='删除网络标签';
    else if(change.kind==='add_no_connect')detail='标记有意不连接的引脚';
    else if(change.kind==='remove_no_connect')detail='删除未连接标记';
    else if(change.kind==='add_text')detail=`新增说明：${change.after.text}`;
    else if(change.kind==='add_power_flag')detail='声明外部供电来源';
    else if(change.kind==='set_document_date')detail=`图纸日期：${change.before} → ${change.after}`;
    else if(change.kind==='add_wire')detail=`新增 ${change.after.length-1} 段导线`;
    else if(change.kind==='remove_wire')detail='删除原导线';
    else if(change.kind==='add_junction')detail=`新增连接点 ${change.after.join(', ')} mm`;
    else if(change.kind==='remove_junction')detail='删除连接点';
    else if(change.kind==='initialize_pcb')detail=`创建真实 PCB · ${change.after.componentCount} 个器件 · ${change.after.outline.width} × ${change.after.outline.height} mm`;
    else if(change.kind==='move_footprint')detail=`移动 PCB 器件至 ${change.at.join(', ')} mm · ${change.rotation}°${change.layer?` · ${change.layer==='B.Cu'?'底面':'顶面'}`:''}`;
    else if(change.kind==='set_outline')detail=`PCB 板框 ${change.outline.width} × ${change.outline.height} mm · ${change.outline.cornerRadius?`R${change.outline.cornerRadius} 圆角`:'直角'}`;
    else if(change.kind==='add_track')detail=`新增 ${change.points.length-1} 段 ${change.net} 铜线 · ${change.width} mm`;
    else if(change.kind==='add_via')detail=`新增 ${change.net} 过孔 · ${change.diameter}/${change.drill} mm`;
    else if(change.kind==='add_mounting_hole')detail=`新增机械安装孔 ${change.reference} · ${change.drill} mm · 位置 ${change.at.join(', ')} mm`;
    else if(change.kind==='remove_mounting_hole')detail='明确删除机械安装孔，保留电气器件与连接';
    else if(change.kind==='remove_track'||change.kind==='remove_via')detail='删除 PCB 铜对象并重新检查';
    else if(change.kind==='set_via_rules')detail=`通孔最小外径 ${change.diameter} mm · 钻孔 ${change.drill} mm · 原因：${change.reason}`;
    else if(change.kind==='add_zone')detail=`新增 ${change.net} ${change.layer} 铺铜并原生填充`;
    else if(change.kind==='remove_zone')detail='删除铜区并重新填充';
    else if(change.kind==='set_zone_rules')detail=`设置铜区生成下限：最小填充宽度 ${change.minimumThickness} mm · 热焊辐条 ${change.thermalSpoke} mm · ${change.reason}；全局走线规则保持`;
    else if(change.kind==='sync_pcb')detail=`按新原理图同步 ${change.padChanges.length} 项焊盘网络变化${change.addedComponents?.length?'，新增 '+change.addedComponents.length+' 个器件':''}${change.footprintReplacements?.length?'，明确替换 '+change.footprintReplacements.map((r:any)=>r.reference).join('、')+' 封装，允许 '+change.footprintReplacements.reduce((n:number,r:any)=>n+r.releasedPadIds.length,0)+' 个指定接点重新布线':'，保留原布局'}，保留全部铜对象`;
    else if(change.kind==='release_pair')detail=`解除成组几何约束 ${change.name}：${change.reason}；旧契约保留在历史`;
    else if(change.kind==='set_footprint_text')detail=`调整 ${change.field==='Reference'?'器件位号':'器件数值'} 的位置和角度${change.visible?'':'并隐藏'}`;
    else if(change.kind==='add_board_text')detail=`新增说明丝印 ${change.text} · ${change.layer} · ${change.at.join(', ')} mm`;
    else if(change.kind==='remove_board_text')detail='删除指定独立说明丝印';
    else if(change.kind==='set_footprint_silkscreen_line')detail=`修整丝印直线：${change.start.join(', ')} → ${change.end.join(', ')} mm · ${change.reason}`;
    else if(change.kind==='remove_footprint_silkscreen_line')detail=`删除指定丝印直线 · ${change.reason}`;
    else if(change.kind==='remove_footprint_silkscreen_polygon')detail=`删除指定丝印多边形 · ${change.reason}`;
    else if(change.kind==='set_stackup')detail=`设置 ${change.profile.copper.length} 层物理叠层 · 名义 ${change.profile.nominalThickness} mm`;
    else detail=change.kind;
    row.append(make('b','',name+' · '),document.createTextNode(detail));
    if(index<6)changes.append(row);
    else{if(!moreChanges){const detail=make('details','more-changes');detail.append(make('summary','',`查看其余 ${(revision?.changes?.length||0)-6} 项修改`));moreChanges=make('div');detail.append(moreChanges);changes.append(detail);}moreChanges.append(row);}
    const resolution=symbolResolutionDetails(change);if(resolution)row.parentElement?.append(resolution);
  }
  const editJob=context?.jobs.find(j=>j.id===revision?.createdByJobId);
  const rawResult=editJob?.kind==='eda.import'?undefined:editJob?.result;
  const result=revision?.validation?.adoptionMode==='review'&&rawResult?{...rawResult,adopted:!!revision.validation.adopted}:rawResult;
  const connectivityText=result?.connectivityPolicy==='replace_groups'?(result.connectivityMatched?'连接符合预期变化':'连接不符合预期变化'):(result?.connectivityPreserved?'引脚连接关系保持一致':'引脚连接关系改变');
  const note=state==='ready'?(revision?.parentRevisionId===context?.project.headRevisionId?'原生检查通过，候选等待审阅采纳。':'工程已变化，请基于当前版本重新生成候选。'):state==='pending'?'正在生成真实设计并检查；当前版本保持不变。':revision?.origin?.startsWith('pcb_')&&result?`${result.connectivityMatched?'焊盘网络与原理图一致':'焊盘网络不一致'}${result.copperIntentMatched===false?' · 铜线与提交目标不一致':''} · ${result.checks?.find((c:any)=>c.kind==='drc')?.unconnected??'待检查'} 处待连线 · ${result.adopted?'已保存 PCB 草稿；布局布线和制造检查继续进行':'未采纳，当前版本保持不变'}`:result?`${result.nativeChecksPassed?'ERC 通过':'ERC 需处理'} · ${connectivityText} · ${result.adopted?'已采纳新版本，下游设计需复验':'未采纳，当前版本保持不变'}`:revision?.validation?.message||'图纸和报告绑定此输入版本。';
  text('revision-checks',note);
  if(result?.blockingFindings?.length){const labels:Record<string,string>={courtyards_overlap:'封装装配空间重叠',clearance:'铜间距不足',shorting_items:'铜对象短路',mounting_connections_changed:'安装孔修改破坏原连接'};findings.append(make('p','revision-finding failed','未采纳原因：'+result.blockingFindings.slice(0,4).map((f:any)=>labels[f.type]||f.description||f.type).join('、')));}
  if(result?.mountingFeatures)changes.append(make('p','field-note',`${result.mountingFeatures.count} 个独立机械孔 · 原电气连接${result.mountingFeatures.originalPhysicalConnectionsPreserved?'保持':'未保持'} · 尺寸、来源与版本已记录`));
  if(result?.footprintSilkscreen)changes.append(make('p','field-note',`${result.footprintSilkscreen.targets.length} 条丝印目标${result.footprintSilkscreen.passed?'已核对':'未通过'} · 其余原生内容${result.footprintSilkscreen.otherNativeContentPreserved?'保持':'未保持'} · 物理连接${result.footprintSilkscreen.physicalConnectionsPreserved?'保持':'未保持'}`));
  if(result?.footprintSilkscreen?.derivedFootprints)changes.append(make('p','field-note',`${result.footprintSilkscreen.derivedFootprints.length} 项工程派生封装 · 原库保留 · 原生库一致性${result.footprintSilkscreen.nativeLibraryMatch?'通过':'未通过'}`));
  const nativeDrc=result?.checks?.find((check:any)=>check.kind==='drc');
  if(nativeDrc)findings.append(make('p','revision-finding'+(nativeDrc.errors||nativeDrc.warnings||nativeDrc.unconnected?' warning':''),`本次原生DRC：${nativeDrc.errors} 错误 · ${nativeDrc.warnings} 警告 · ${nativeDrc.unconnected} 未连；参考与阻抗另验`));
  if(result?.routingProgress)text('revision-checks',note+(result.pairReplacement||result.ordinaryReplacement?` · ${result.pairReplacement?'成组段':'普通网络'}${result.adopted?'已原子重布':'重布未采纳'} · 原物理连接${result.routingProgress.preservedSourceConnections?'保持':'未保持'}${result.ordinaryReplacement?' · 重布网络'+(result.routingProgress.reworkedNetsComplete?'全部连通':'未全部连通'):''}`:` · 原生确认合并 ${result.routingProgress.physicalComponentsMerged} 个物理连接分量`));
  if(result?.ordinaryReplacement)for(const net of result.ordinaryReplacement.nets)changes.append(make('p','field-note',`${net.net}：明确替换 ${net.removedObjectIds.length} 个铜对象 · ${net.reason}；原生DRC与源连接保持分别检查`));
  if(result?.routeReadability){const q=result.routeReadability;changes.append(make('p','field-note',`普通走线提案：转弯 ${q.before.bendCount} → ${q.after.bendCount} · 短折簇 ${q.before.shortClusterCount} → ${q.after.shortClusterCount} · 线长 ${q.before.lengthMm.toFixed(3)} → ${q.after.lengthMm.toFixed(3)} mm；接点与过孔位置保持，保存后的原生检查另列`));}
  if(result?.routingQuality){const q=result.routingQuality;changes.append(make('p','field-note',`整板线形：${q.flaggedChainCount} 条待审线链 · 短折簇 ${q.shortClusterCount||0} · 局部折返 ${q.backtrackCount||0}；已在焊盘、过孔、分支与层/线宽变化处分段，必要引出与等长需另审`));}
  if(result?.routingQuality?.cornerPolicy){const q=result.routingQuality;changes.append(make('p','field-note',`裸露转角：直角 ${q.rightAngleCount} · 锐角 ${q.acuteCornerCount}；焊盘内 ${q.padInternalCornerCount}、真实分支 ${q.branchJunctionCount} 另列`));}
  if(result?.cornerPolicy){const c=result.cornerPolicy;changes.append(make('p','field-note',`新增转角约束：${c.passed?'通过':'未通过'} · 拦截 ${c.blockingCornerCount??0} 处 · 既有未改 ${c.unchangedExistingCornerCount??0} 处`));}
  if(result?.padExits){const p=result.padExits;changes.append(make('p','field-note',`焊盘出线：斜出 ${p.diagonalExitCount} · 偏心 ${p.offCenterExitCount??'未检查'} · 过早转弯 ${p.shortNeckCount}；${p.offCenterExitCount==null?'旧报告仅检查上下左右方向，未检查中心':'从焊盘中心按上下左右直出'}，焊盘外直行 ${p.minimumStraightMm} mm 再转弯 · 覆盖 ${p.coveredPadCount} 个焊盘 · 未覆盖 ${p.uncoveredPads?.length??0} 项`));}
  if(result?.mountingPlan&&result.mountingPlan.status!=='not_planned'){
    const m=result.mountingPlan,box=make('details','quiet-note');
    box.append(make('summary','',translateText(m.status==='explicit_exception'?'固定孔：已保存例外':m.passed?'固定孔：规划匹配':'固定孔：规划被破坏')));
    const reason=m.reason||m.request?.reason,source=m.source||m.request?.source;
    if(reason){const p=make('p','field-note',reason);p.translate=false;box.append(p);}
    if(source){const p=make('p','field-note',source);p.translate=false;box.append(p);}
    changes.append(box);
  }
  if(result?.trackOptimization){const rows=result.trackOptimization.chains||[];changes.append(make('p','field-note',translateText('既有走线优化')+' · '+rows.filter((r:any)=>r.status==='improved').length+' / '+rows.length));}
  if(result?.silkscreenPlacement){const rows=result.silkscreenPlacement.placements||[];changes.append(make('p','field-note',translateText('自动位号排放')+' · '+rows.filter((r:any)=>r.status==='placed').length+' / '+rows.length));}
  if(result?.placementReview)changes.append(placementReviewView(result.placementReview));
  if(result?.connectorAccess){const a=result.connectorAccess;for(const r of a.interfaces||[]){changes.append(make('p','field-note',`接口插拔方向 ${r.reference}：${r.status==='passed'?'安装基准朝外且贴合板边':r.status==='inward'?'朝向板内，须旋转并重布':'安装基准未对齐板边'} · 板边偏移 ${r.edgeGapMm.toFixed(3)} mm；实际插头与外壳空间另验`));}if(a.unconfirmed?.length)changes.append(make('p','field-note',`接口方向未确认：${a.unconfirmed.map((r:any)=>r.reference).join('、')}；须提供有来源的插拔基准，不按接口名称猜方向`));}
  for(const p of result?.placementChange?.changedLinks||[])changes.append(make('p','field-note',`摆放连接变化 ${p.references.join(' / ')}：${p.before.lengthMm.toFixed(3)} → ${p.after.lengthMm.toFixed(3)} mm · 转弯 ${p.before.bendCount} → ${p.after.bendCount}；其他电气与几何条件另行核对`));
  if(result?.padExitPolicy){const p=result.padExitPolicy;changes.append(make('p','field-note',`新增出线约束：${p.passed?'通过':'未通过'} · 拦截 ${p.blockingExitCount??0} 处 · 既有未改 ${p.unchangedExistingFindingCount??0} 处；直接水平/垂直连接邻近端子且未转弯另列`));}
  if(result?.powerFeeds)changes.append(make('p','field-note',`电容供电节点：${result.powerFeeds.rules.filter((r:any)=>r.passed).length}/${result.powerFeeds.rules.length} 项中心线拓扑已证明；${result.powerFeeds.passed?'声明条件通过':'声明条件未通过'}，去耦回路与电源完整性另验`));
  if(result?.layoutFeedback)changes.append(layoutFeedbackView(result.layoutFeedback,result.bodyAvoidance));
  if(result?.reworkCopperEnds&&!result.reworkCopperEnds.passed)text('revision-checks',el('revision-checks').textContent+` · 重布网络有 ${result.reworkCopperEnds.danglingCopperFindingCount} 处悬空铜端，未采纳`);
  if(result?.pairedGeometry){
    const pairs=result.pairedGeometry;
    text('revision-checks',el('revision-checks').textContent+` · 成组几何${pairs.passed?'已核对':'未通过'}（不表示阻抗通过）`);
    for(const section of pairs.sections){const m=section.metrics;if(m)changes.append(make('p','subtle',`${section.name}：两线 ${m.lengthsMm.map((n:number)=>n.toFixed(3)).join(' / ')} mm · 长度差 ${m.skewMm.toFixed(3)} mm · 两端扇出 ${m.fanoutLengthsMm.map((n:number)=>n.toFixed(3)).join(' / ')} mm`));}
  }
  const stack=revision?.pcbDesign?.stackup;
  if(stack){
    const detail=make('details','more-changes');detail.append(make('summary','',`物理叠层 · ${stack.copper.length} 层 · 名义 ${stack.nominalThickness} mm`));
    detail.append(make('p','',stack.name),make('p','field-note',stack.copper.map((c:any)=>c.name).join(' → ')),
      make('p','field-note',`各层模型厚度合计 ${result?.stackup?.modelThickness??'见原生报告'} mm；原生叠层${result?.stackup?.passed?'已核对':'待核对'}，不表示阻抗通过。`),
      make('p','field-note',stack.thicknessNote),make('p','field-note',`资料：${stack.source.url} · ${stack.source.version}`),make('p','field-note',stack.source.note));
    changes.append(detail);
  }
  const reference=context?.jobs.find(j=>j.kind==='pcb.reference_check'&&j.inputRevision===previewRevision&&j.status==='completed');
  if(reference?.result){const r=reference.result;const label=r.terminalAntipads?`参考条件${r.referenceCheckPassed?'已核对':'需处理'} · 原始 ${r.strictMissingTrackCount} 项 / 当前 ${r.missingTrackCount} 项 · ${r.qualifiedTerminalTrackCount} 段受控终端`:(r.referenceCheckPassed?'所查轨迹参考覆盖已核对':'所查轨迹参考覆盖未通过');text('revision-checks',el('revision-checks').textContent+` · ${label}（不表示阻抗通过）`);}
  if(context&&revision)renderEngineeringReview(context,revision,el('engineering-review'),el('engineering-review-title'),el('engineering-review-content'));
  else el('engineering-review').hidden=true;
  const intent=revision?.connectivityIntent, details=el('connection-details'), content=el('connection-content');
  details.hidden=!intent;content.replaceChildren();
  const pins=(group:{reference:string;pin:string}[])=>group.map(p=>p.reference+'.'+p.pin).join(' ↔ ');
  if(intent){
    content.append(make('p','field-note',`明确替换 ${intent.removeGroups.length} 个原分组，形成 ${intent.addGroups.length} 个目标分组；其余连接保持不变。`));
    for(const group of intent.addGroups)content.append(make('p','connection-group',pins(group)));
    for(const [label,groups] of [['未实现的目标',result?.connectivityDifference?.missingGroups],['意外产生的连接',result?.connectivityDifference?.unexpectedGroups]] as const){
      if(groups?.length){content.append(make('b','connection-problem',label));for(const group of groups)content.append(make('p','connection-group',pins(group.pins)));}
    }
    const report=context?.artifacts.find(a=>a.revisionId===previewRevision&&a.kind==='connectivity_report');
    if(report){const link=make('a','text-button','下载完整连接核对报告 ↗') as HTMLAnchorElement;link.href=fileUrl(report)+'?download=true';content.append(link);}
  }
  const native=context?.artifacts.find(a=>a.revisionId===previewRevision&&a.kind==='native_project');
  el('native-download').hidden=!native;if(native)el<HTMLAnchorElement>('native-download').href=fileUrl(native)+'?download=true';
  const libraries=revision?.symbolLibraries||[], libraryContent=el('library-content');
  el('library-details').hidden=!libraries.length;libraryContent.replaceChildren();
  text('library-summary',`外部符号库 · ${libraries.length} 个冻结版本`);
  if(libraries.length)libraryContent.append(make('p','field-note','库文件随此设计版本保存。来源、发布版本和许可由导入方声明；文件摘要已经核对。'));
  for(const library of libraries){
    const card=make('div','library-record');
    card.append(make('b','',`${library.description} · ${library.sourceVersion}`),
      make('p','',`来源：${library.sourceUri}`),make('p','',`许可：${library.license}`),
      make('p','field-note',`${library.symbolCount} 个符号 · 格式版本 ${library.formatVersion} · ${time(library.importedAt)}`),
      make('code','library-digest',`SHA-256 ${library.sha256}`));
    const provenance=context?.artifacts.find(a=>a.kind==='symbol_library_provenance'&&a.name===library.id+'-provenance.json');
    if(provenance){const link=make('a','text-button','下载来源记录 ↗') as HTMLAnchorElement;link.href=fileUrl(provenance)+'?download=true';card.append(link);}
    libraryContent.append(card);
  }
  const spec=revision?.designSpecification,specContent=el('design-specification-content');
  el('design-specification').hidden=!spec;specContent.replaceChildren();
  if(spec){specContent.append(make('p','',spec.requirements),make('p','field-note','以下为工程建立时的设计记录；当前器件和连接以本版图纸及审查为准。'));for(const [key,value] of Object.entries(spec.decisions))specContent.append(make('p','field-note',key+'：'+(typeof value==='string'?value:JSON.stringify(value))));}
  const documents=el('document-downloads');documents.replaceChildren();
  const referenceImage=context?.artifacts.find(a=>a.revisionId===previewRevision&&a.jobId===reference?.id&&a.kind==='pcb_reference_preview');
  if(referenceImage&&view==='pcb'){const button=make('button','text-button','查看参考问题定位 ↗');button.onclick=act(async()=>{pcbView='2d';selectedArtifact=referenceImage.id;await renderView();});documents.append(button);}
  for(const [kind,title] of [['schematic_pdf','下载原理图 PDF ↗'],['bom','下载器件清单 ↗'],['pcb_reference_report','下载参考覆盖报告 ↗'],['pcb_pair_report','下载成组几何报告 ↗'],['pcb_stackup_report','下载物理叠层报告 ↗'],['pcb_geometry_report','下载板体与器件几何报告 ↗'],['pcb_geometry_project','下载几何交接包 ↗']]){const artifact=context?.artifacts.find(a=>a.revisionId===previewRevision&&a.kind===kind);if(artifact&&!(view==='schematic'&&['schematic_pdf','bom'].includes(kind))){const link=make('a','text-button',title) as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';documents.append(link);}}
}
el('preview-revision').onchange=act(async()=>{previewRevision=input('preview-revision').value;followRevision=false;selectedArtifact='';await ensureRevisionDetail();await renderView();});
document.addEventListener('schematic-candidate-adopted',event=>{const receipt=(event as CustomEvent).detail;if(receipt.projectId!==active)return;followRevision=true;previewRevision=receipt.revisionId;selectedArtifact='';void refresh().catch(error=>toast(error.message,true));});
document.addEventListener('schematic-candidate-refresh',()=>requestRefresh());
el('follow-revision').onclick=act(async()=>{followRevision=true;previewRevision=defaultPreviewRevision();selectedArtifact='';await ensureRevisionDetail();await renderView();});
function renderMechanicalRevision(revision?:Revision){
  if(revision?.id===moduleHead(revision))text('revision-state','当前机械版本');
  for(const id of ['engineering-review','connection-details','library-details','design-specification'])el(id).hidden=true;
  const result=context?.jobs.find(j=>j.id===revision?.createdByJobId)?.result;
  const documents=el('document-downloads');documents.replaceChildren();
  const native=context?.artifacts.find(a=>a.revisionId===previewRevision&&a.kind==='mechanical_project');
  el('native-download').hidden=!native;if(native)el<HTMLAnchorElement>('native-download').href=fileUrl(native)+'?download=true';
  const host=el('revision-changes');
  if(result){
    text('revision-checks',`${result.checksPassed?'名义实体与独立 STEP 回读通过':'机械检查需处理'} · ${result.parts.length} 个零件 · ${result.adopted?'已采纳机械版本':'未采纳，当前机械版本保持'} · 不代表制造或实物验证`);
    const labels:Record<string,string>={pcb_nominal_retention:'支柱接触与紧固件空间',pcb_no_interference:'板与外壳干涉检查',pcb_controlled_passages:'接头与开口对应检查',pcb_side_gap:'板和器件侧向间隙',pcb_vertical_gap:'板和器件上下间隙',base_lid_no_interference:'底壳与上盖干涉检查',assembly_no_interference:'零件之间干涉检查'};
    const failed=Object.entries(result.nativeChecks).filter(([,passed])=>!passed).map(([name])=>labels[name]||name);
    if(failed.length)host.append(make('p','field-note','未通过：'+failed.join('、')));
    for(const p of result.parts)host.append(make('p','field-note',`${p.displayName||p.id} · ${p.solidCount} 个实体 · ${p.volumeMm3.toFixed(2)} mm³`));
  }else text('revision-checks',revision?.validation?.status==='pending'?'正在构建真实机械实体并检查；当前机械版本保持。':revision?.validation?.message||'通过 MCP 提交参数化机械设计后，模型和检查会同步到这里。');
  const dependency=revision?.mechanicalDesign?.pcbDependency;
  const selectedInsertion=context?.artifacts.find(a=>a.id===selectedArtifact&&a.revisionId===revision?.id&&a.kind==='mechanical_insertion_model');
  const insertion=context?.jobs.find(j=>j.kind==='mechanical.insertion_check'&&j.result?.revisionId===revision?.id&&j.status==='completed'&&(!selectedInsertion||j.id===selectedInsertion.jobId));
  if(insertion){const r=insertion.result;host.append(make('p','field-note',`${r.engineeringEnvelopeUsed?'工程包络':'声明配对件CAD'} · 全行程保守检查${r.checksPassed?'通过':'需处理'} · 独立回读${r.independentReplayMatched?'一致':'待核对'}；插接基准为明确声明，真实兼容、公差和插拔力仍未验证。`));for(const i of r.interfaces)host.append(make('p','field-note',`${i.id} · 行程 ${i.travelMm} mm · 净空 ${i.clearanceMm} mm · ${i.passed?'通过':'需处理'}`));for(const i of r.interfaces){if(!i.seatedBodyReachesDeclaredZone)host.append(make('p','field-note',`${i.id}：就位实体没有到达声明接合区`));if(!i.withdrawalOutsideCaseAndConnector)host.append(make('p','field-note',`${i.id}：退出行程不足，尚未完全离开外壳与接头`));}for(const f of r.findings.slice(0,4))host.append(make('p','field-note',`${f.interfaceId}/${f.bodyId} 的行程与 ${f.obstacleDisplayName} 冲突`));}
  if(dependency?.geometryIncluded&&context)renderPcbModelSources(host,context,dependency);
  else if(dependency)host.append(make('p','field-note',`绑定 PCB ${dependency.revisionId.slice(-8)} · 仅记录版本依赖；板体、器件与接口实体需另行明确导入。`));
  const retention=result?.measures?.pcb_retention;
  if(retention){host.append(make('p','field-note',`${retention.rows.length} 个支柱 · 孔位来自指定 PCB · 名义支撑与紧固件空间${result.nativeChecks.pcb_nominal_retention?'通过':'需处理'}；螺钉尺寸为工程包络，未验证采购公差、受力和装配顺序。`));for(const row of retention.rows)host.append(make('p','field-note',`${row.reference} · 径向间隙 ${row.radialClearanceMm.toFixed(2)} mm · 名义旋入 ${row.nominalEngagementMm.toFixed(2)} mm · ${row.passed?'通过':'需处理'}`));}
  else if(dependency?.geometryIncluded)host.append(make('p','field-note',result?'尚无经过关联验证的板固定结构。':'固定结构尚待本次原生检查完成。'));
  const freshness=make('p','field-note');host.append(freshness);
  if(revision?.module==='mechanical'&&context){
    const key=`${active}:${revision.id}:${context.project.version}:${revision.validation?.status}`;
    const apply=(r:any)=>{if(view==='mechanical'&&previewRevision===revision.id&&host.contains(freshness))freshness.textContent=r.requiresDependencyReview?'上游 PCB、需求或偏好已变化，此模型需重新核对。':'输入版本和依赖已记录；仍需验证实际配合与制造条件。';};
    if(mechanicalReads.has(key))apply(mechanicalReads.get(key));
    else void api(`projects/${active}/mechanical?revisionId=${revision.id}`).then(r=>{mechanicalReads.set(key,r);apply(r);}).catch(()=>{if(host.contains(freshness))freshness.textContent='依赖状态暂未读到，请稍后重试。';});
  }
  const sources=revision?.mechanicalDesign?.sources||[];
  el('library-details').hidden=!sources.length;text('library-summary',`机械来源 · ${sources.length} 个冻结文件`);const library=el('library-content');library.replaceChildren();
  for(const source of sources){const row=make('div','library-record');row.append(make('b','',source.id+' · '+source.version),make('p','','来源：'+source.uri),make('p','','许可：'+source.license),make('p','field-note',source.note),make('code','library-digest','SHA-256 '+source.sha256));library.append(row);}
  for(const [kind,title] of [['mechanical_step','下载装配 STEP ↗'],['mechanical_report','下载检查报告 ↗']]){const artifact=context?.artifacts.find(a=>a.revisionId===previewRevision&&a.kind===kind);if(artifact){const link=make('a','text-button',title) as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';documents.append(link);}}
  for(const [kind,title] of [['mechanical_insertion_report','下载插拔检查 ↗'],['mechanical_insertion_project','下载插拔诊断包 ↗']]){const artifact=context?.artifacts.find(a=>a.revisionId===previewRevision&&a.kind===kind&&(!selectedInsertion||a.jobId===selectedInsertion.jobId));if(artifact){const link=make('a','text-button',title) as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';documents.append(link);}}
}
function renderFirmwareRevision(revision?:Revision){
  for(const id of ['engineering-review','connection-details','library-details','design-specification'])el(id).hidden=true;
  const result=context?.jobs.find(j=>j.id===revision?.createdByJobId)?.result;
  text('revision-checks',result?`${result.checksPassed?'编译与映像回读通过':'构建检查未通过'} · ${result.adopted?'已采纳独立固件版本':'未采纳，当前固件版本保持'} · 行为与实物验证另列`:revision?.validation&&['cancelled','failed','interrupted'].includes(revision.validation.status)?'候选未采纳，当前固件版本保持不变。':'正在构建候选；当前固件版本保持不变。');
  const host=el('revision-changes');
  for(const change of revision?.changes||[])host.append(make('p','field-note',change.kind==='firmware_template'?`从模板建立应用源码 · ${change.template}`:`${change.kind==='add_file'?'新增':change.kind==='remove_file'?'删除':'修改'} ${change.file}`));
  el('native-download').hidden=true;el('native-download').removeAttribute('href');
  el('document-downloads').replaceChildren();
}
async function renderView(){
  const renderEpoch=++renderSequence;
  const current=()=>renderEpoch===renderSequence;
  const previousView=document.body.dataset.view;
  document.body.dataset.view=view;
  const createHost=el(view==='overview'&&!context?'welcome-action':'new-project-slot');
  if(el('new-project').parentElement!==createHost)createHost.append(el('new-project'));
  const contextHost=el(view==='overview'?'overview-context':'workspace-inspector');
  if(el('project-context-card').parentElement!==contextHost)contextHost.prepend(el('project-context-card'));
  if(el('agent-workflow').parentElement!==contextHost)el('project-context-card').after(el('agent-workflow'));
  el('project-context-card').hidden=!context;
  el('manufacturing-task-filter').hidden=view!=='manufacturing';
  document.body.classList.toggle('module-view',['sources','pcb','mechanical','firmware','manufacturing'].includes(view));
  document.getElementById('view-icon')?.setAttribute('href','#i-'+({sources:'sources',pcb:'pcb',mechanical:'mechanical',firmware:'firmware',manufacturing:'manufacturing',settings:'settings',schematic:'schematic',overview:'overview'}[view]||'overview'));
  if(view!=='schematic'&&!(view==='pcb'&&pcbView==='2d'))liveDrawing?.deactivate();
  const inspectorScope=document.body.dataset.inspectorScope||'';
  const nextInspectorScope=view==='schematic'?'schematic':view==='mechanical'?'mechanical':view==='firmware'?'firmware':view==='manufacturing'?'manufacturing':'';
  document.body.dataset.inspectorScope=nextInspectorScope;
  if(view!=='firmware')firmwarePanel.deactivate();
  if(view!=='manufacturing')manufacturingWorkspace.render(view,'overview',null);
  mechanicalWorkspace.render(view,context,context?.revisions.find(r=>r.id===previewRevision));
  schematicWorkspace.render(view,context,context?.revisions.find(r=>r.id===previewRevision));
  if(view==='overview')setInspector(false);
  else if(inspectorScope!==nextInspectorScope||previousView==='overview')setInspector(nextInspectorScope?localStorage.getItem('formforge-'+nextInspectorScope+'-inspector')!=='hidden':localStorage.getItem('formforge-inspector')==='visible');
  const settings=view==='settings';
  el('pcb-overview').hidden=view!=='pcb';
  document.body.classList.toggle('settings-view',settings);
  text('project-name',settings?translateText('本机工作台'):context?.project.name||translateText('未选择工程'));
  el('inspector-toggle').hidden=settings||view==='overview';el('task-card').hidden=settings;
  el('settings-panel').hidden=!settings;
  if(settings){
    document.querySelectorAll<HTMLElement>('.view-panel').forEach(panel=>{panel.hidden=panel.id!=='settings-panel';});
    el('revision-card').hidden=true;el('import-button').hidden=true;el('inspect-button').hidden=true;el('package-import-button').hidden=true;el('project-export-button').hidden=true;
    settingsPanel.render();return;
  }
  settingsPanel.deactivate();
  el('package-import-button').hidden=view!=='overview';el('project-export-button').hidden=view!=='overview';
  el('import-button').hidden=['mechanical','firmware','sources','manufacturing'].includes(view);el('inspect-button').hidden=['mechanical','firmware','sources','manufacturing'].includes(view);
  el('overview').hidden=view!=='overview';
  el('sources-panel').hidden=view!=='sources';if(view==='sources')await sourcesPanel.render();if(!current())return;
  el('firmware-panel').hidden=view!=='firmware';
  const manufacturingTab=manufacturingViews.get(context?.project.id||'')||manufacturingViews.get('')||'overview';
  el('manufacturing-tabs').hidden=view!=='manufacturing';
  manufacturingWorkspace.render(view,manufacturingTab,context);
  document.querySelectorAll<HTMLButtonElement>('[data-manufacturing-tab]').forEach(button=>{button.setAttribute('aria-selected',String(button.dataset.manufacturingTab===manufacturingTab));button.tabIndex=button.dataset.manufacturingTab===manufacturingTab?0:-1;button.onclick=()=>{manufacturingViews.set(active||'',button.dataset.manufacturingTab!);if(!active||context?.project.id===active)void renderView().catch(e=>toast(e.message,true));};});
  el('manufacturing-panel').hidden=view!=='manufacturing'||manufacturingTab!=='native';
  el('procurement-panel').hidden=view!=='manufacturing'||manufacturingTab!=='procurement';
  el('external-manufacturing-panel').hidden=view!=='manufacturing'||manufacturingTab!=='external';
  el('orders-panel').hidden=view!=='manufacturing'||manufacturingTab!=='orders';
  el('supplier-panel').hidden=view!=='manufacturing'||manufacturingTab!=='supplier';
  el('jlc-panel').hidden=view!=='manufacturing'||manufacturingTab!=='jlc';
  el('structural-panel').hidden=view!=='manufacturing'||manufacturingTab!=='structural';
  el('structural-quotes-panel').hidden=view!=='manufacturing'||manufacturingTab!=='structural-quotes';
  el('canvas').classList.toggle('pcb-canvas',view==='pcb'&&pcbView==='2d');
  el('pcb-view-tabs').hidden=view!=='pcb';
  el('pcb-fullscreen').hidden=view!=='pcb';
  el('pcb-layer-toolbar').hidden=view!=='pcb'||pcbView!=='2d';
  if(view!=='pcb')closePcbFullscreen();
  document.querySelectorAll<HTMLButtonElement>('[data-pcb-view]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.pcbView===pcbView)));
  const graphical=['schematic','pcb','mechanical'].includes(view);el('viewer-panel').hidden=!graphical;
  el('revision-card').hidden=(!graphical&&view!=='firmware')||!visibleRevisions().length;
  if(graphical||view==='firmware'){text('native-download','下载原生工程 ↗');renderRevision();}
  pcbWorkspace.render(view,context,context?.revisions.find(r=>r.id===previewRevision));
  if(view==='firmware')await firmwarePanel.render(context,context?.revisions.find(r=>r.id===previewRevision));if(!current())return;
  await mechanicalParameters.render(view==='mechanical'?context:null,context?.revisions.find(r=>r.id===previewRevision));if(!current())return;
  if(view==='manufacturing'&&manufacturingTab==='native')await manufacturingPanel.render(context);if(!current())return;
  if(view==='manufacturing'&&manufacturingTab==='procurement')await procurementPanel.render(context);if(!current())return;
  if(view==='manufacturing'&&manufacturingTab==='external')await externalManufacturingPanel.render(context);if(!current())return;
  if(view==='manufacturing'&&manufacturingTab==='orders')await ordersPanel.render(context);if(!current())return;
  if(view==='manufacturing'&&manufacturingTab==='supplier')await supplierPanel.render(context);if(!current())return;
  if(view==='manufacturing'&&manufacturingTab==='jlc')await jlcPanel.render(context);if(!current())return;
  if(view==='manufacturing'&&manufacturingTab==='structural')await structuralPanel.render(context);if(!current())return;
  if(view==='manufacturing'&&manufacturingTab==='structural-quotes')await structuralQuotesPanel.render(context);if(!current())return;
  text('viewer-help',modelView()?'左键旋转 · 右键平移 · 滚轮缩放':'拖动平移 · 滚轮缩放');
  if(view!=='mechanical')el('mechanical-controls').hidden=true;
  el('planned-panel').hidden=graphical||view==='overview'||view==='sources'||view==='firmware'||view==='manufacturing';
  if(!graphical){liveDrawing?.deactivate();return;}
  const artifacts=viewerArtifacts(context?.artifacts||[],view,previewRevision,pcbView);
  const select=el<HTMLSelectElement>('artifact-select');select.replaceChildren();
  const objectPreview=view==='schematic'||(view==='pcb'&&pcbView==='2d');
  select.hidden=objectPreview||view==='pcb';el('viewer-caption').hidden=objectPreview;
  if(view==='pcb'&&pcbView==='3d'&&followRevision){const latest=artifacts.find(a=>a.kind==='pcb_geometry_model');if(latest&&!observedGeometryArtifacts.has(latest.id)){observedGeometryArtifacts.add(latest.id);selectedArtifact='';}}
  if(view==='mechanical'&&followRevision){const latest=artifacts.find(a=>a.kind==='mechanical_insertion_model');if(latest&&!observedInsertionArtifacts.has(latest.id)){observedInsertionArtifacts.add(latest.id);selectedArtifact='';}}
  if(!objectPreview&&view!=='pcb')for(const artifact of artifacts){const option=document.createElement('option');option.value=artifact.id;option.textContent=(artifact.kind==='mechanical_insertion_model'?'插拔诊断 · ':'')+artifact.name+' · '+(artifact.kind.startsWith('baseline_')?'修改前 · ':'')+artifact.jobId.slice(-6);select.append(option);}
  if(objectPreview){
    if(context&&previewRevision){
      const generation=++viewGeneration;
      if(!liveDrawing){const {LiveDrawing}=await import('./live-drawing');if(generation!==viewGeneration)return;liveDrawing=new LiveDrawing(el('canvas'));}
      const shown=await liveDrawing.show({projectId:context.project.id,revisionId:previewRevision,kind:view as 'schematic'|'pcb',status:context.revisions.find(r=>r.id===previewRevision)?.validation?.status||'unverified',nativePreviews:artifacts.filter(a=>a.kind==='schematic_preview')});
      if(generation!==viewGeneration)return;
      if(shown){selectedArtifact='';el('viewer-empty').hidden=true;el('image-stage').hidden=true;el('model-stage').hidden=true;el('mechanical-controls').hidden=true;el('download-artifact').hidden=true;return;}
    }
    liveDrawing?.deactivate();model?.clear();el('viewer-empty').replaceChildren(make('h2','',view==='pcb'?'此版本的 PCB 无法读取':'此版本的原理图无法读取'),make('p','','请检查版本文件是否完整后重试。'));el('viewer-empty').hidden=false;el('image-stage').hidden=true;el('model-stage').hidden=true;el('mechanical-controls').hidden=true;el('download-artifact').hidden=true;selectedArtifact='';return;
  }
  liveDrawing?.deactivate();
  if(!artifacts.length){
    const previousImage=!modelView()&&displayedImageScope.startsWith(`${context?.project.id}:${view}:`);
    if(previousImage){el('image-stage').hidden=false;el('model-stage').hidden=true;el('viewer-empty').hidden=true;el('download-artifact').hidden=true;el('mechanical-controls').hidden=true;text('viewer-caption',`正在生成 ${previewRevision.slice(-8)} · 当前仍显示旧版 ${displayedImageRevision.slice(-8)}`);selectedArtifact='';viewGeneration++;return;}
    model?.clear();el('viewer-empty').replaceChildren(make('h2','',view==='mechanical'?'还没有可显示的机械模型':modelView()?'此版本还没有三维模型':'还没有可显示的图纸'),make('p','',view==='mechanical'?'Agent 提交机械设计参数后，真实模型和检查将显示在这里。':'原生产物生成后将显示在这里；不会代用其他阶段或版本的图形。'));const option=document.createElement('option');option.textContent='尚无工程产物';select.append(option);el('viewer-empty').hidden=false;el('image-stage').hidden=true;el('model-stage').hidden=true;el('mechanical-controls').hidden=true;el('download-artifact').hidden=true;text('viewer-caption',previewRevision?`版本 ${previewRevision.slice(-8)} · 等待此版本的预览`:'尚无输入版本');selectedArtifact='';viewGeneration++;return;
  }
  if(artifacts.some(a=>a.id===selectedArtifact)){select.value=selectedArtifact;updateCaption(artifacts.find(a=>a.id===selectedArtifact)!);return;}
  const previous=context?.artifacts.find(a=>a.id===selectedArtifact);
  await showArtifact(artifacts.find(a=>a.name===previous?.name)||artifacts[0]);
}
function updateCaption(artifact:Artifact){
  const job=context?.jobs.find(j=>j.id===artifact.jobId);
  const state=job?.status==='completed'?'检查任务产物':job&&terminal(job.status)?'诊断产物 · 任务未完成':'候选预览 / 检查进行中';
  text('viewer-caption',artifact.kind==='mechanical_insertion_model'?`插拔诊断 · ${artifact.revisionId.slice(-8)} · 位置查看不等于兼容或实物证明`:artifact.kind==='pcb_geometry_model'?`板体与已选器件 · 版本 ${artifact.revisionId.slice(-8)} · 覆盖范围见报告`:artifact.kind==='board_model'?'板体模型 · 不含元件与外壳':artifact.kind==='mechanical_model'?`版本 ${artifact.revisionId.slice(-8)} · ${state} · 爆炸仅用于查看`:artifact.kind==='pcb_reference_preview'?`版本 ${artifact.revisionId.slice(-8)} · 参考问题定位 · 非制造图`:`版本 ${artifact.revisionId.slice(-8)} · ${state}`);
}
async function showArtifact(artifact:Artifact){
  const generation=++viewGeneration;selectedArtifact=artifact.id;el<HTMLSelectElement>('artifact-select').value=artifact.id;
  el('viewer-empty').hidden=false;el('viewer-empty').textContent='正在读取 '+artifact.name+' · '+artifact.revisionId.slice(-8);el('download-artifact').hidden=true;el<HTMLAnchorElement>('download-artifact').href=fileUrl(artifact)+'?download=true';
  liveDrawing?.deactivate();
  updateCaption(artifact);
  const is3d=isModelArtifact(artifact);
  const keepImage=!is3d&&displayedImageScope===`${context?.project.id}:${view}:${artifact.name}`;
  el('image-stage').hidden=!keepImage;el('model-stage').hidden=true;el('mechanical-controls').hidden=true;
  if(keepImage){el('viewer-empty').hidden=true;text('viewer-caption',`正在读取 ${artifact.revisionId.slice(-8)} · 当前仍显示旧版 ${displayedImageRevision.slice(-8)}`);}
  const failed=(message:string)=>{if(generation!==viewGeneration)return;selectedArtifact='';const host=el('viewer-empty');host.hidden=false;host.replaceChildren(make('p','',message));const retry=make('button','text-button','重新加载此预览');retry.onclick=act(()=>showArtifact(artifact));host.append(retry);text('viewer-caption',`预览未加载 · 版本 ${artifact.revisionId.slice(-8)}`);};
  const loaded=()=>{el('viewer-empty').hidden=true;el('download-artifact').hidden=false;};
  if(is3d){
    try{
      let assembly:AssemblyView|undefined;
      if(['mechanical_model','pcb_geometry_model','mechanical_insertion_model'].includes(artifact.kind)){
        const metadata=context?.artifacts.find(a=>a.kind===(artifact.kind==='pcb_geometry_model'?'pcb_geometry_view':artifact.kind==='mechanical_insertion_model'?'mechanical_insertion_view':'mechanical_view')&&a.jobId===artifact.jobId&&a.revisionId===artifact.revisionId);
        if(!metadata)throw new Error('尚未收到零件信息');
        const response=await fetch(fileUrl(metadata));if(!response.ok)throw new Error('无法读取零件信息');assembly=await response.json();
        if(assembly!.glbSha256!==artifact.sha256)throw new Error('零件信息和模型版本不一致');
        if(generation!==viewGeneration)return;
        if(artifact.kind==='mechanical_model'&&context){
          const key=`${artifact.projectId}:${artifact.revisionId}:${context.project.version}:${context.revisions.find(r=>r.id===artifact.revisionId)?.validation?.status}`;
          const read=mechanicalReads.get(key)||await api(`projects/${artifact.projectId}/mechanical?revisionId=${artifact.revisionId}`);
          mechanicalReads.set(key,read);
          const names=new Map<string,string>((read.partPresentation||[]).filter((p:any)=>p.colorGroup==='screws'||p.displayName!==p.id).map((p:any)=>[p.id,p.displayName]));
          for(const part of assembly!.parts)if(names.has(part.id))part.displayName=names.get(part.id);
        }
      }
      if(generation!==viewGeneration)return;
      if(!model){const {ModelViewer}=await import('./model');if(generation!==viewGeneration)return;model=new ModelViewer(el('model-stage'));}
      await model.load(fileUrl(artifact),assembly);if(generation!==viewGeneration)return;
      el('model-stage').hidden=false;loaded();
      el('mechanical-wireframe').setAttribute('aria-pressed','false');
      el('mechanical-clay').setAttribute('aria-pressed','false');
      document.querySelectorAll<HTMLElement>('[data-model-camera]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.modelCamera==='iso')));
      if(view==='mechanical'&&assembly&&hasAssemblyControls(artifact)&&(artifact.kind!=='pcb_geometry_model'||assembly.parts.length===1)){
        el('mechanical-controls').querySelector<HTMLElement>('.explode-control')!.hidden=assembly.parts.length<2;
        linearMotionActive=!!assembly.linearMotionView;input('explode').max=linearMotionActive?'1':'2';input('explode').step=linearMotionActive?'0.01':'0.05';el('mechanical-controls').querySelector('label[for="explode"]')!.textContent=linearMotionActive?'拔出位置':'爆炸查看';
        input('explode').value='0';text('explode-value',linearMotionActive?'就位':'装配姿态');const list=el('part-list');list.replaceChildren();input('mechanical-part-search').value='';
        for(const part of assembly.parts){const label=make('label','part-toggle');const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=true;checkbox.dataset.partId=part.id;label.dataset.search=((part.displayName||'')+' '+part.id).toLocaleLowerCase();checkbox.onchange=()=>model?.showPart(part.id,checkbox.checked);label.append(checkbox,document.createTextNode(part.displayName||part.id));list.append(label);}
        el('mechanical-controls').hidden=false;
        if(view==='mechanical')renderRevision();
      }
    }catch(e){failed('3D 模型加载失败：'+(e as Error).message);}
  }else{
    try{await loadPreviewImage(fileUrl(artifact));if(generation!==viewGeneration)return;const scope=`${context?.project.id}:${view}:${artifact.name}`;if(scope!==displayedImageScope){scale=1;tx=ty=0;}displayedImageScope=scope;displayedImageRevision=artifact.revisionId;updateTransform();el<HTMLImageElement>('preview-image').src=fileUrl(artifact);el('image-stage').hidden=false;loaded();}
    catch(e){if(keepImage&&generation===viewGeneration){selectedArtifact='';el('viewer-empty').hidden=true;text('viewer-caption',`新图读取失败 · 当前仍显示 ${displayedImageRevision.slice(-8)} · 可重新选择图纸重试`);}else failed((e as Error).message);}
  }
}
input('manufacturing-only-tasks').onchange=()=>renderJobs();
el('mechanical-manufacturing').onclick=act(async()=>{manufacturingViews.set(active,'structural');await setView('manufacturing');});
document.querySelectorAll<HTMLButtonElement>('[data-model-camera]').forEach(button=>button.onclick=()=>{
  if(el('model-stage').hidden)return;
  model?.cameraView(button.dataset.modelCamera as 'iso'|'top'|'front'|'side');
  document.querySelectorAll<HTMLElement>('[data-model-camera]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
});
el('mechanical-wireframe').onclick=()=>{
  if(el('model-stage').hidden)return;
  const enabled=el('mechanical-wireframe').getAttribute('aria-pressed')!=='true';
  model?.wireframe(enabled);el('mechanical-wireframe').setAttribute('aria-pressed',String(enabled));
};
el('mechanical-clay').onclick=()=>{
  if(el('model-stage').hidden)return;
  const enabled=el('mechanical-clay').getAttribute('aria-pressed')!=='true';model?.clay(enabled);
  el('mechanical-clay').setAttribute('aria-pressed',String(enabled));
  const wireframe=el('mechanical-wireframe').getAttribute('aria-pressed')==='true';model?.wireframe(wireframe);
};
el('mechanical-image').onclick=()=>{if(!el('model-stage').hidden)model?.exportImage('FormForge-'+previewRevision+'-preview.png');};
input('mechanical-part-search').oninput=()=>{const query=input('mechanical-part-search').value.trim().toLocaleLowerCase();el('part-list').querySelectorAll<HTMLElement>('label').forEach(label=>label.hidden=!label.dataset.search?.includes(query));};
for(const [id,visible] of [['mechanical-show-all',true],['mechanical-hide-all',false]] as const)el(id).onclick=()=>{
  el('part-list').querySelectorAll<HTMLInputElement>('input').forEach(box=>{box.checked=visible;model?.showPart(box.dataset.partId!,visible);});
};
function updateTransform(){el('preview-image').style.transform=`translate(${tx}px,${ty}px) scale(${scale})`;}
input('explode').oninput=()=>{const amount=Number(input('explode').value);model?.explode(amount);text('explode-value',linearMotionActive?(amount===0?'就位':amount===1?'行程末端':`拔出 ${Math.round(amount*100)}%`):(amount?amount.toFixed(2)+' ×':'装配姿态'));};
function zoom(factor:number){if(liveDrawing?.active){liveDrawing.zoom(factor);return;}if(modelView()){model?.zoom(factor);return;}scale=Math.max(.2,Math.min(10,scale*factor));updateTransform();}
function updatePcbFullscreen(expanded:boolean){
  el('viewer-panel').classList.toggle('pcb-fullscreen',expanded);
  document.body.classList.toggle('pcb-fullscreen-open',expanded);
  el('pcb-fullscreen').setAttribute('aria-pressed',String(expanded));
  text('pcb-fullscreen',expanded?'退出全屏':'展开全屏');
}
function closePcbFullscreen(){
  if(document.fullscreenElement===el('viewer-panel'))void document.exitFullscreen();
  updatePcbFullscreen(false);
}
el('pcb-fullscreen').onclick=async()=>{
  if(el('viewer-panel').classList.contains('pcb-fullscreen')){closePcbFullscreen();return;}
  updatePcbFullscreen(true);
  try{await el('viewer-panel').requestFullscreen();}catch{/* Keep the full-window view when browser fullscreen is unavailable. */}
};
document.addEventListener('fullscreenchange',()=>updatePcbFullscreen(document.fullscreenElement===el('viewer-panel')));
document.addEventListener('keydown',event=>{if(event.key==='Escape')closePcbFullscreen();});
el('zoom-in').onclick=()=>zoom(1.2);el('zoom-out').onclick=()=>zoom(1/1.2);el('zoom-reset').onclick=()=>{if(liveDrawing?.active){liveDrawing.reset();return;}if(modelView()){model?.reset();return;}scale=1;tx=ty=0;updateTransform();};
el('image-stage').addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY>0?1/1.1:1.1);},{passive:false});
el('image-stage').onpointerdown=e=>{drag={x:e.clientX-tx,y:e.clientY-ty};el('image-stage').setPointerCapture(e.pointerId);el('image-stage').classList.add('dragging');};
el('image-stage').onpointermove=e=>{if(drag){tx=e.clientX-drag.x;ty=e.clientY-drag.y;updateTransform();}};
el('image-stage').onpointerup=el('image-stage').onpointercancel=()=>{drag=null;el('image-stage').classList.remove('dragging');};
el('artifact-select').onchange=act(async()=>{const a=context?.artifacts.find(a=>a.id===input('artifact-select').value);if(a)await showArtifact(a);});
document.querySelectorAll<HTMLElement>('[data-view]').forEach(b=>b.onclick=act(()=>setView(b.dataset.view!)));
document.querySelectorAll<HTMLElement>('[data-pcb-view]').forEach(b=>b.onclick=act(async()=>{pcbView=b.dataset.pcbView as PcbViewMode;selectedArtifact='';await renderView();}));
document.querySelectorAll<HTMLElement>('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog')?.close());
input('project-search').oninput=renderProjects;
input('project-search').onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();input('project-search').value='';renderProjects();}};
function setInspector(visible:boolean){document.body.classList.toggle('inspector-collapsed',!visible);el('workspace-inspector').hidden=!visible;el('inspector-toggle').setAttribute('aria-pressed',String(visible));text('inspector-toggle',visible?'收起需求':'展开需求');}
setInspector(localStorage.getItem('formforge-inspector')==='visible');
el('inspector-toggle').onclick=()=>{const visible=document.body.classList.contains('inspector-collapsed');setInspector(visible);localStorage.setItem(view==='schematic'?'formforge-schematic-inspector':view==='mechanical'?'formforge-mechanical-inspector':view==='firmware'?'formforge-firmware-inspector':view==='manufacturing'?'formforge-manufacturing-inspector':'formforge-inspector',visible?'visible':'hidden');};
el('sidebar-toggle').onclick=()=>{
  if(document.body.classList.contains('navigation-open')){closeNavigation();return;}
  document.body.classList.add('navigation-open');el('sidebar').removeAttribute('inert');el('sidebar-backdrop').hidden=false;el('sidebar-toggle').setAttribute('aria-expanded','true');input('project-search').focus();
};
el('sidebar-backdrop').onclick=()=>{closeNavigation();el('sidebar-toggle').focus();};
document.addEventListener('keydown',e=>{
  if(!document.body.classList.contains('navigation-open')||document.querySelector('dialog[open]'))return;
  if(e.key==='Escape'){closeNavigation();el('sidebar-toggle').focus();}
  if(e.key==='Tab'){
    const focusable=[...el('sidebar').querySelectorAll<HTMLElement>('a,button,input')].filter(node=>!node.hidden&&node.getClientRects().length>0);
    const first=focusable[0],last=focusable[focusable.length-1];
    if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
    else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
  }
});
window.matchMedia('(max-width: 760px)').addEventListener('change',closeNavigation);
closeNavigation();
function open(id:string){el<HTMLDialogElement>(id).showModal();}
el('new-project').onclick=()=>open('project-dialog');
el('package-import-button').onclick=()=>open('package-import-dialog');
el('project-export-button').onclick=act(async()=>{
  const project=context?.project;if(!project)return;
  const button=input('project-export-button');button.disabled=true;toast('正在校验并导出工程包…');
  try{
    const result=await api<{downloadUrl:string,filename:string}>('projects/'+project.id+'/exports','POST',{expectedVersion:project.version});
    const link=document.createElement('a');link.href=result.downloadUrl;link.download=result.filename;document.body.append(link);link.click();link.remove();
    toast('工程包已通过校验并开始下载。');await refresh();
  }finally{button.disabled=!context||!!context?.jobs.some(j=>!terminal(j.status));}
});
let renameTarget:Project|null=null;
el('rename-project').onclick=()=>{
  if(!context)return;
  renameTarget={...context.project};input('rename-project-name').value=renameTarget.name;
  open('rename-project-dialog');input('rename-project-name').select();
};
el('delete-project').onclick=act(async()=>{
  const project=context?.project;if(!project)return;
  if(!await confirmMaintenance('删除工程','工程将移入设置中的回收站，仍占用空间，可恢复或再彻底删除文件。未保存的输入不会保留，供应商订单不受此操作影响。','移入回收站',project.name))return;
  await api(`projects/${project.id}/delete`,'POST',{expectedVersion:project.version,confirmName:project.name});
  localStorage.removeItem('formforge-project');active='';await refresh();toast('工程已移入回收站，可在设置中恢复或彻底删除文件。');
});
el('import-button').onclick=()=>open('import-dialog');
el('inspect-button').onclick=()=>{const select=el<HTMLSelectElement>('inspect-revision');select.replaceChildren();for(const r of context?.revisions.filter(r=>(r.module||'eda')==='eda')||[]){const o=document.createElement('option');o.value=r.id;o.textContent=(r.entrypoints.schematic||r.entrypoints.board)+' · '+r.id.slice(-8);select.append(o);}open('inspect-dialog');};
for(const id of ['requirements','preferences'])input(id).oninput=()=>{dirty=true;text('draft-notice','尚未保存');};
el('save-context').onclick=act(async()=>{if(!context)return;const p=context.project;await api('projects/'+p.id,'PUT',{expectedVersion:editVersion,specification:{...p.specification,text:input('requirements').value},preferences:{...p.preferences,text:input('preferences').value}});dirty=false;toast('需求和偏好已保存。');await refresh();});
function submit(form:string,fn:()=>Promise<void>){el<HTMLFormElement>(form).onsubmit=async e=>{e.preventDefault();const button=el(form).querySelector<HTMLButtonElement>('[type=submit]')!;button.disabled=true;try{await fn();el(form).closest('dialog')?.close();}catch(error){toast((error as Error).message,true);}finally{button.disabled=false;}};}
submit('rename-project-form',async()=>{
  const target=renameTarget;if(!target)return;
  const name=input('rename-project-name').value.trim();
  if(!name){input('rename-project-name').setCustomValidity(translateText('请输入工程名称。'));input('rename-project-name').reportValidity();throw new Error(translateText('请输入工程名称。'));}
  const renamed=await api<Project>(`projects/${target.id}/rename`,'POST',{expectedVersion:target.version,name});
  if(active===target.id&&editVersion===target.version)editVersion=renamed.version;
  renameTarget=null;await refresh();toast('工程名称已修改。');
});
input('rename-project-name').oninput=()=>input('rename-project-name').setCustomValidity('');
submit('project-form',async()=>{const p=await api<Project>('projects','POST',{name:input('new-name').value,description:input('new-description').value});active=p.id;localStorage.setItem('formforge-project',active);el<HTMLFormElement>('project-form').reset();await refresh();await setView('overview');toast('工程已创建。');});
submit('package-import-form',async()=>{const result=await api<{project:Project}>('project-imports','POST',{packagePath:input('package-path').value,name:input('package-project-name').value.trim()||null});active=result.project.id;localStorage.setItem('formforge-project',active);el<HTMLFormElement>('package-import-form').reset();await refresh();await setView('overview');toast('工程包已校验并导入为新工程。');});
submit('import-form',async()=>{if(!active)return;await api('projects/'+active+'/imports','POST',{sourceDir:input('source-dir').value,schematic:input('schematic-entry').value||null,board:input('board-entry').value||null,requestId:requestId()});toast('导入任务已开始。');await refresh();});
submit('inspect-form',async()=>{if(!active)return;await api('projects/'+active+'/inspections','POST',{revisionId:input('inspect-revision').value,include3d:input('include-3d').checked,timeoutSeconds:Number(input('timeout').value),requestId:requestId()});toast('检查任务已开始。');await refresh();});

function connect(){
  const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws?after=${cursor}`);
  ws.onopen=()=>{text('connection-status','实时已连接');el('connection-status').classList.add('connected');setLiveConnection(true,'实时同步');requestRefresh();};
  ws.onmessage=e=>{const msg=JSON.parse(e.data);if(msg.type==='events'){
    const incoming=(msg.events as EventRecord[]).filter(x=>x.seq>cursor);cursor=Math.max(cursor,msg.cursor);mergeEvents(incoming);renderEvents();
    let refreshNeeded=false,phaseChanged=false;
    for(const event of incoming){
      if(event.projectId===active&&context&&applyJobPhase(context.jobs,event))phaseChanged=true;
      else if(event.projectId===active||event.type.startsWith('project.')||event.type==='supplier.session')refreshNeeded=true;
    }
    if(refreshNeeded)requestRefresh();else if(phaseChanged)renderJobs();
  }};
  ws.onclose=()=>{text('connection-status','连接已断开 · 重连中');el('connection-status').classList.remove('connected');setLiveConnection(false,'连接中断 · 重连中');window.setTimeout(()=>{void api('session','POST').then(connect).catch(()=>window.setTimeout(connect,3000));},2000);};
  ws.onerror=()=>ws.close();
}
async function openSubmission(){manufacturingViews.set(active,'supplier');await setView('manufacturing');el('submission-approvals')?.scrollIntoView({block:'start'});}
window.addEventListener('hashchange',()=>{if(location.hash==='#submission')void openSubmission().catch(e=>toast(e.message,true));});
async function init(){await api('session','POST');const linkedProject=new URL(location.href).searchParams.get('project');if(linkedProject)active=linkedProject;const caps=await api('capabilities');text('app-version',caps.version);const snapshot=await api<{events:EventRecord[],cursor:number}>('event-snapshot');cursor=snapshot.cursor;mergeEvents(snapshot.events);await refresh();if(location.hash==='#settings')await setView('settings');if(location.hash==='#submission')await openSubmission();renderEvents();connect();}
function initialize(){void init().catch(e=>{text('connection-status','服务未连接 · ');setLiveConnection(false,'服务未连接');const retry=make('button','text-button','重新连接') as HTMLButtonElement;retry.onclick=()=>{retry.disabled=true;text('connection-status','连接中');setLiveConnection(false,'正在重新连接');initialize();};el('connection-status').append(retry);toast(e.message,true);});}
initialize();
