import {fileUrl,type Artifact,type Context} from './api';
import {locale,translateText} from './i18n';

const make=(tag:string,text='',cls='')=>{const e=document.createElement(tag);e.textContent=text;e.className=cls;return e;};
const el=(id:string)=>document.getElementById(id)!;
export const manufacturingSections:Record<string,[string,string,string]>={
  overview:['制造总览','选择制造路径，集中查看本工程的资料与任务。','overview'],
  native:['从设计导出','从固定原图与 PCB 版本生成审核资料。','prepare'],
  external:['外部文件审核','登记并审核外部 Gerber、BOM 和坐标文件。','prepare'],
  structural:['结构件','选择制作件、材料与数量，检查 STEP / STL 资料。','prepare'],
  procurement:['采购计划','核对器件料号、装配范围及 BOM / CPL。','quote'],
  'structural-quotes':['结构报价','基于已检查的结构资料准备和记录报价。','quote'],
  jlc:['官方接口','查看官方接口配置、计价与订单核对能力。','quote'],
  supplier:['下单执行','查看供应商会话、复核记录与已授权任务。','execute'],
  orders:['平台订单','查看本地关联订单及历史观察，保留原始资料绑定。','execute'],
};
const artifacts:Record<string,[string,string]>={
  manufacturing_project:['PCB 审核资料包','pcb'],manufacturing_report:['PCB 预检报告','pcb'],manufacturing_gerbers:['Gerber 与钻孔包','pcb'],manufacturing_bom:['原始 BOM','pcb'],manufacturing_positions:['原始坐标','pcb'],manufacturing_assembly_review:['装配审核表','pcb'],manufacturing_drc:['原生 DRC','pcb'],manufacturing_erc:['原生 ERC','pcb'],
  procurement_project:['采购计划包','assembly'],procurement_report:['采购检查报告','assembly'],procurement_review:['采购审核表','assembly'],procurement_plan:['采购计划与来源','assembly'],procurement_jlc_bom:['BOM 审核副本','assembly'],procurement_jlc_cpl:['CPL 审核副本','assembly'],procurement_matching_bom:['选型 BOM（非生产）','assembly'],procurement_matching_cpl:['选型坐标（非生产）','assembly'],procurement_matching_notes:['选型待核说明','assembly'],
  structural_project:['结构件审核包','structural'],structural_report:['结构件检查报告','structural'],structural_stl_report:['STL 网格检查报告','structural'],structural_parts:['结构件选择清单','structural'],
  external_cam_package:['外部文件审核包','external'],external_cam_report:['外部文件检查报告','external'],external_cam_assembly:['外部装配审核表','external'],
};
export function manufacturingFiles(context:Context){return context.artifacts.filter(a=>a.projectId===context.project.id&&artifacts[a.kind]).sort((a,b)=>b.createdAt.localeCompare(a.createdAt)||a.id.localeCompare(b.id));}
export function filterManufacturingFiles(files:Artifact[],category:string,revision:string,query:string){
  const term=query.trim().toLocaleLowerCase();
  return files.filter(a=>(!category||artifacts[a.kind]?.[1]===category)&&(!revision||a.revisionId===revision)&&(!term||[a.name,a.kind,a.revisionId,a.jobId,artifacts[a.kind]?.[0],translateText(artifacts[a.kind]?.[0]||'','en')].join(' ').toLocaleLowerCase().includes(term)));
}
const status:Record<string,string>={completed:'任务已结束',failed:'任务失败',cancelled:'已取消',interrupted:'已中断',queued:'排队中',running:'执行中'};
const jobNames:Record<string,string>={'manufacturing.prepare':'PCB 资料审核','manufacturing.external_review':'外部文件审核','manufacturing.mechanical_prepare':'结构件资料审核','manufacturing.procurement':'采购计划检查','manufacturing.jlc_quote':'官方计价','manufacturing.jlc_sync':'官方订单核对'};
export class ManufacturingWorkspace {
  private signature='';
  private context:Context|null=null;
  private all:Artifact[]=[];
  private query='';private category='';private revision='';private offset=0;
  private list=make('div');private count=make('span');private previous=document.createElement('button');private next=document.createElement('button');
  constructor(){
    const tabs=el('manufacturing-tabs');
    for(const button of tabs.querySelectorAll<HTMLButtonElement>('[data-manufacturing-tab]')){
      const id=button.dataset.manufacturingTab!;button.dataset.stage=manufacturingSections[id][2];button.id='manufacturing-tab-'+id;button.title=manufacturingSections[id][1];
    }
    tabs.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const buttons=Array.from(tabs.querySelectorAll<HTMLButtonElement>('button'));let i=buttons.indexOf(document.activeElement as HTMLButtonElement);i=e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[i].click();buttons[i].focus();};
  }
  render(view:string,tab:string,context:Context|null){
    const active=view==='manufacturing';
    el('manufacturing-overview').hidden=!active||tab!=='overview';
    if(!active)return;
    this.context=context;
    if(!context){this.signature='';el('manufacturing-overview').replaceChildren(make('h2','制造总览'),make('p','请先选择工程。','empty-small'));return;}
    const files=manufacturingFiles(context),jobs=context.jobs.filter(j=>j.kind.startsWith('manufacturing.'));
    const signature=JSON.stringify([context.project.id,context.project.version,context.project.moduleHeads,context.project.headRevisionId,files.map(a=>a.id),jobs.map(j=>[j.id,j.status])]);
    if(signature===this.signature)return;
    const projectChanged=!this.signature||JSON.parse(this.signature)[0]!==context.project.id;this.signature=signature;this.all=files;
    if(projectChanged){this.query='';this.category='';this.revision='';this.offset=0;}
    const host=el('manufacturing-overview');host.replaceChildren();
    const metrics=make('div','','manufacturing-summary');
    for(const [title,value,note] of [['资料文件',files.length,'包含历史版本'],['制造任务',jobs.length,'本地任务记录'],['执行中的任务',jobs.filter(j=>['queued','running'].includes(j.status)).length,'排队与执行中'],['失败任务记录',jobs.filter(j=>['failed','interrupted'].includes(j.status)).length,'历史失败保留']]){const item=make('div');item.append(make('span',String(title)),make('strong',String(value)));metrics.append(item);}host.append(metrics);
    const sectionHead=make('div','','card-heading');sectionHead.append(make('h2','制造资料中心'),make('span','历史资料 · 非生产放行','small-tag'));host.append(sectionHead);
    const tools=make('div','','manufacturing-file-tools');const search=document.createElement('input');search.type='search';search.placeholder='搜索文件、任务或版本…';search.setAttribute('aria-label','搜索制造资料');search.value=this.query;search.oninput=()=>{this.query=search.value;this.offset=0;this.renderFiles();};
    const category=document.createElement('select');category.setAttribute('aria-label','资料类别');for(const [id,title] of [['','全部类别'],['pcb','PCB 资料'],['assembly','装配与采购'],['structural','结构件资料'],['external','外部文件']]){const option=document.createElement('option');option.value=id;option.textContent=title;category.append(option);}category.value=this.category;category.onchange=()=>{this.category=category.value;this.offset=0;this.renderFiles();};
    const revision=document.createElement('select');revision.setAttribute('aria-label','资料版本');const all=document.createElement('option');all.value='';all.textContent='全部资料版本';revision.append(all);for(const id of new Set(files.map(a=>a.revisionId))){if(!id)continue;const option=document.createElement('option');option.value=id;option.textContent='修订 '+id.slice(-8);revision.append(option);}revision.value=this.revision;if(!revision.value)this.revision='';revision.onchange=()=>{this.revision=revision.value;this.offset=0;this.renderFiles();};
    const clear=make('button','清除筛选','button secondary');clear.onclick=()=>{search.value='';category.value='';revision.value='';this.query=this.category=this.revision='';this.offset=0;this.renderFiles();};tools.append(search,category,revision,clear);
    this.list=make('div','','manufacturing-file-list');this.list.tabIndex=0;this.list.setAttribute('aria-label','制造资料列表');this.count=make('span','','field-note');this.count.setAttribute('role','status');const pager=make('div','','manufacturing-file-pager');this.previous=make('button','上一页','button secondary') as HTMLButtonElement;this.next=make('button','下一页','button secondary') as HTMLButtonElement;this.previous.onclick=()=>{this.offset=Math.max(0,this.offset-12);this.renderFiles();};this.next.onclick=()=>{this.offset+=12;this.renderFiles();};pager.append(this.count,this.previous,this.next);host.append(tools,this.list,pager);this.renderFiles();
  }
  private renderFiles(){
    const rows=filterManufacturingFiles(this.all,this.category,this.revision,this.query);if(this.offset>=rows.length)this.offset=0;this.list.replaceChildren();
    for(const artifact of rows.slice(this.offset,this.offset+12)){
      const row=make('article','','manufacturing-file-row');const info=make('div','','manufacturing-file-info'),job=this.context?.jobs.find(j=>j.id===artifact.jobId);
      const name=make('strong',artifact.name);name.translate=false;const description=make('p','','field-note');description.append(make('span',artifacts[artifact.kind][0]),document.createTextNode(' · '+(artifact.size/1024).toFixed(1)+' KB'));info.append(name,description);
      const metadata=make('p','','field-note');metadata.append(make('span',artifact.revisionId?'修订 '+artifact.revisionId.slice(-8):'未绑定设计修订'),document.createTextNode(' · '+artifact.jobId.slice(-8)+' · '+new Date(artifact.createdAt).toLocaleString(locale(),{hour12:false})));info.append(metadata);
      const details=make('details','','manufacturing-file-evidence');details.append(make('summary','文件摘要与来源'),make('code','SHA-256 '+artifact.sha256,'library-digest'),make('p',job?(jobNames[job.kind]||job.kind):'任务详情未载入','field-note'));info.append(details);
      const tag=make('span',job?(status[job.status]||job.status):'任务状态未知','badge '+(job&&['failed','interrupted','cancelled'].includes(job.status)?'warning':'neutral'));
      const download=make('a','下载文件','button secondary') as HTMLAnchorElement;download.href=fileUrl(artifact)+'?download=true';download.title=artifact.name;row.append(info,tag,download);this.list.append(row);
    }
    if(!rows.length)this.list.append(make('p',this.all.length?'没有匹配的制造资料。':'暂无制造资料','empty-small'));
    this.count.textContent=`${rows.length?this.offset+1:0}–${Math.min(this.offset+12,rows.length)} / ${rows.length}`;this.previous.disabled=this.offset===0;this.next.disabled=this.offset+12>=rows.length;
  }
}
