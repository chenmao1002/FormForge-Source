import {api,fileUrl,requestId,type Context,type Job,type Artifact} from './api';
import {locale} from './i18n';

const make=(tag:string,text='',cls='')=>{const node=document.createElement(tag);node.textContent=text;node.className=cls;return node;};
const field=(text:string,input:HTMLElement)=>{const label=make('label',text);label.append(input);return label;};
const states:Record<string,string>={queued:'排队中',running:'执行中',completed:'已结束',failed:'未完成',cancelled:'已取消',interrupted:'已中断'};
type Choice={partId:string;expectedPartHash:string;disposition:'fabricate'|'provided'|'exclude';quantity:number|null;expectedStepSha256:string|null;reason:string};
type Basis={revisionId:string;designSha256:string;mechanicalJobId:string;parts:{part:any;partHash:string;step:null|{sha256:string}}[]};
type Draft={version:number;basis:Basis;stale:boolean;parts:Choice[];material:string;requirements:string;message:string;policy:string;reason:string;element?:HTMLFormElement};

export class StructuralPanel{
  private generation=0;
  private mounted='';
  private revisions=new Map<string,string>();
  private drafts=new Map<string,Draft>();
  private selected=new Map<string,string>();
  private following=new Map<string,boolean>();
  private sourceHost=make('div');
  private formHost=make('div');
  private historyHost=make('div');
  private versionNote=make('p','','field-note');
  private signature='';
  constructor(private host:HTMLElement,private refresh:()=>Promise<void>,private notify:(message:string,error?:boolean)=>void){}

  async render(context:Context|null){
    const generation=++this.generation;
    if(!context){this.mounted='';this.host.replaceChildren(make('p','请先选择工程。','empty-small'));return;}
    const pid=context.project.id;
    if(this.mounted!==pid){
      this.mounted=pid;this.signature='';
      this.sourceHost=make('div');this.formHost=make('div');this.historyHost=make('div');this.versionNote=make('p','','field-note');
      this.host.replaceChildren(make('h2','结构件制造资料'),make('p','选择制作件和数量，回读原始 STEP，再生成有来源的逐件资料包。','field-note'),this.sourceHost,this.versionNote,this.formHost,this.historyHost);
    }
    const reset=()=>{this.drafts.delete(pid);this.formHost.replaceChildren();void this.render(context);};
    const revisions=context.revisions.filter(r=>r.module==='mechanical');
    let rid=this.revisions.get(pid)||context.project.moduleHeads?.mechanical||'';
    if(!revisions.some(r=>r.id===rid))rid=context.project.moduleHeads?.mechanical||'';
    this.revisions.set(pid,rid);
    const signature=revisions.map(r=>r.id).join(',')+rid+context.project.version;
    if(signature!==this.signature){
      this.signature=signature;const select=document.createElement('select');select.setAttribute('aria-label','结构资料机械版本');
      for(const revision of revisions){const option=document.createElement('option');option.value=revision.id;option.textContent=`${revision.id.slice(-8)}${revision.id===context.project.moduleHeads?.mechanical?' · 当前机械版本':''} · ${revision.message||'机械设计'}`;select.append(option);}
      select.value=rid;select.disabled=!rid;select.onchange=()=>{this.revisions.set(pid,select.value);reset();};
      const button=make('button','按当前版本重新填写','text-button') as HTMLButtonElement;button.onclick=()=>{this.revisions.set(pid,context.project.moduleHeads?.mechanical||'');reset();};
      this.sourceHost.replaceChildren(field('机械版本',select),button);
    }
    let schemaError='';
    try{
      const reads=await Promise.allSettled([api(`projects/${pid}/structural`),!this.drafts.has(pid)&&rid?api(`structural-schema?projectId=${pid}&revisionId=${rid}`):Promise.resolve(null)]);
      if(generation!==this.generation)return;
      const [readResult,schemaResult]=reads;
      if(schemaResult.status==='rejected')schemaError=(schemaResult.reason as Error).message;
      if(schemaResult.status==='fulfilled'&&schemaResult.value){
        const schema=schemaResult.value,basis:Basis=schema.basis;
        const draft:Draft={version:schema.projectVersion,basis,stale:schema.requiresDependencyReview,material:'',requirements:'',message:'',policy:'require-current',reason:'',
          parts:basis.parts.map(item=>({partId:item.part.id,expectedPartHash:item.partHash,disposition:item.step&&item.part.solidCount===1?'fabricate':'provided',quantity:1,expectedStepSha256:item.step?.sha256||null,reason:''}))};
        this.drafts.set(pid,draft);
      }
      const draft=this.drafts.get(pid);
      if(draft){
        this.versionNote.textContent=`表单绑定工程 V${draft.version} · 机械 ${draft.basis.revisionId.slice(-8)}${draft.version!==context.project.version?`；当前工程已到 V${context.project.version}，提交将被版本检查拒绝，请明确重新填写。`:''}`;
        this.versionNote.className=draft.version!==context.project.version?'connection-problem':'field-note';
        if(!draft.element)draft.element=this.form(pid,draft);
        if(this.formHost.firstChild!==draft.element)this.formHost.replaceChildren(draft.element);
      }else this.formHost.replaceChildren(make('p',schemaError||'先在机械设计中建立并检查独立模型，再准备结构件资料。',schemaError?'connection-problem':'empty-small'));
      if(readResult.status==='rejected')throw readResult.reason;
      let read=readResult.value;const jobs:Job[]=[...read.jobs].sort((a,b)=>b.createdAt.localeCompare(a.createdAt)||b.id.localeCompare(a.id));
      const following=this.following.get(pid)!==false;
      const chosen=following?jobs[0]:jobs.find(job=>job.id===this.selected.get(pid))||jobs[0];
      if(chosen&&chosen.id!==read.job?.id){read=await api(`projects/${pid}/structural?jobId=${chosen.id}`);if(generation!==this.generation)return;}
      if(chosen)this.selected.set(pid,chosen.id);
      this.history(context,read,jobs,following,chosen);
    }catch(error){if(generation===this.generation)this.historyHost.replaceChildren(make('p',(error as Error).message,'connection-problem'));}
  }

  private form(pid:string,draft:Draft){
    const form=document.createElement('form');form.className='structural-form';
    if(draft.stale)form.append(make('p','此机械版本或其依赖已变化。请重新验证模型，或明确选择历史计价并说明原因。','connection-problem'));
    const table=make('div','','structural-parts');
    for(const [index,item] of draft.basis.parts.entries()){
      const row=draft.parts[index],group=document.createElement('fieldset');group.append(make('legend',item.part.id));
      const disposition=document.createElement('select');disposition.setAttribute('aria-label',`${row.partId} 处置`);
      for(const [value,text] of [['fabricate','制作'],['provided','已有'],['exclude','排除']]){const option=document.createElement('option');option.value=value;option.textContent=text;option.disabled=value==='fabricate'&&(!item.step||item.part.solidCount!==1);disposition.append(option);}
      disposition.value=row.disposition;
      const quantity=document.createElement('input');quantity.type='number';quantity.min='1';quantity.max='1000000';quantity.step='1';quantity.required=true;quantity.value=String(row.quantity);quantity.setAttribute('aria-label',`${row.partId} 数量`);
      quantity.oninput=()=>{row.quantity=quantity.valueAsNumber;};
      disposition.onchange=()=>{row.disposition=disposition.value as Choice['disposition'];quantity.disabled=row.disposition==='exclude';quantity.required=!quantity.disabled;row.quantity=quantity.disabled?null:Number(quantity.value);};
      const reason=document.createElement('input');reason.required=true;reason.maxLength=1000;reason.value=row.reason;reason.oninput=()=>{row.reason=reason.value;};reason.setAttribute('aria-label',`${row.partId} 处置原因`);
      group.append(field('处置',disposition),field('数量',quantity),field('用途或原因',reason),make('p',item.step?`单实体原件：${item.step.sha256.slice(0,16)}…`:'无独立单实体 STEP，不能直接作为制作件。','field-note'));table.append(group);
    }
    form.append(table);
    const policy=document.createElement('select');policy.setAttribute('aria-label','结构依赖策略');
    for(const [value,text] of [['require-current','要求当前版本和依赖一致'],['historical-quote','明确为历史模型准备计价资料']]){const option=document.createElement('option');option.value=value;option.textContent=text;policy.append(option);}policy.value=draft.policy;
    const reason=document.createElement('input');reason.required=true;reason.maxLength=1000;reason.value=draft.reason;reason.oninput=()=>{draft.reason=reason.value;};reason.setAttribute('aria-label','结构依赖说明');
    policy.onchange=()=>{draft.policy=policy.value;};
    const material=document.createElement('input');material.maxLength=120;material.value=draft.material;material.placeholder='留空表示尚未明确';material.oninput=()=>{draft.material=material.value;};material.setAttribute('aria-label','结构制作材料');
    const requirements=document.createElement('textarea');requirements.required=true;requirements.maxLength=2000;requirements.value=draft.requirements;requirements.oninput=()=>{draft.requirements=requirements.value;};requirements.setAttribute('aria-label','结构制造要求');
    const message=document.createElement('input');message.required=true;message.maxLength=2000;message.value=draft.message;message.oninput=()=>{draft.message=message.value;};message.setAttribute('aria-label','结构任务说明');
    form.append(make('p','工艺：3D 打印。材料可留空；供应商方向、支撑、薄壁、公差和强度仍需复核。','quiet-note'),field('材料',material),field('制造要求',requirements),field('依赖策略',policy),field('依赖说明',reason),field('任务说明',message));
    const submit=make('button','检查并准备结构资料','button primary') as HTMLButtonElement;submit.type='submit';
    form.append(submit,make('p','此操作只检查和打包本地原件，完成后可下载；平台计价另行接入。','field-note'));
    form.onsubmit=async event=>{
      event.preventDefault();submit.disabled=true;
      try{
        await api(`projects/${pid}/structural-preparations`,'POST',{expectedProjectVersion:draft.version,revisionId:draft.basis.revisionId,expectedDesignSha256:draft.basis.designSha256,
          dependencyPolicy:draft.policy,dependencyReason:draft.reason,parts:draft.parts.map(row=>({...row,expectedStepSha256:row.disposition==='fabricate'?row.expectedStepSha256:null})),
          process:'3d-print',material:draft.material.trim()||null,requirements:draft.requirements,message:draft.message,timeoutSeconds:180,requestId:requestId()});
        this.following.set(pid,true);this.notify('结构件核对已开始。');await this.refresh();
      }catch(error){this.notify((error as Error).message,true);}finally{submit.disabled=false;}
    };
    return form;
  }

  private history(context:Context,read:any,jobs:Job[],following:boolean,chosen?:Job){
    const pid=context.project.id,host=this.historyHost;host.replaceChildren(make('h3','结构件资料记录'));
    const bar=make('div','','manufacturing-row'),select=document.createElement('select');select.setAttribute('aria-label','结构件资料任务');
    for(const job of jobs){const option=document.createElement('option');option.value=job.id;option.textContent=`${job.id.slice(-8)} · ${states[job.status]||job.status} · ${new Date(job.createdAt).toLocaleString(locale(),{hour12:false})}`;select.append(option);}
    select.disabled=!chosen;if(chosen)select.value=chosen.id;select.onchange=()=>{this.selected.set(pid,select.value);this.following.set(pid,false);host.replaceChildren(make('p','正在读取所选任务…','field-note'));void this.render(context);};
    const follow=make('button',following?'正在跟随新任务':'跟随最新任务','text-button') as HTMLButtonElement;follow.onclick=()=>{this.following.set(pid,true);host.replaceChildren(make('p','正在读取最新任务…','field-note'));void this.render(context);};bar.append(select,follow);host.append(bar);
    if(!chosen){host.append(make('p','尚无结构件资料任务。','empty-small'));return;}
    host.append(make('p',read.job.phase,'field-note'));if(read.job.error)host.append(make('p',read.job.error,'connection-problem'));
    if(!read.inputIntegrityVerified)host.append(make('p',read.inputIssue||'原输入无法重新核对。','connection-problem'));
    if(read.requiresDependencyReview)host.append(make('p','当前上游与本记录不一致，保留当时的报告；使用前需复核。','connection-problem'));
    const result=read.result;
    if(result){
      const fileStatus=result.stlTopology?(result.fileChecksPassed?'STEP/STL 文件检查通过':'STEP/STL 文件检查有未通过项'):(result.fileChecksPassed?'STEP 文件回读检查通过':'STEP 文件检查有未通过项');
      host.append(make('p',fileStatus,result.fileChecksPassed?'notice':'connection-problem'),make('p',`机械版本 ${result.revisionId.slice(-8)} · 材料：${result.choices.material||'未明确'} · 3D 打印`,'quiet-note'));
      const table=document.createElement('table');table.className='structural-results';const head=document.createElement('thead');head.append(this.row(['零件','处置 / 数量','回读尺寸 mm','体积 mm³']));table.append(head);const body=document.createElement('tbody');
      for(const choice of result.choices.parts){const part=result.parts.find((p:any)=>p.partId===choice.partId);body.append(this.row([choice.partId,`${{fabricate:'制作',provided:'已有',exclude:'排除'}[choice.disposition as Choice['disposition']]} / ${choice.quantity??'—'}`,part?part.dimensionsMm.map((v:number)=>v.toFixed(3)).join(' × '):'不制作',part?part.measured.volumeMm3.toFixed(3):'—']));}table.append(body);host.append(table);
      const pending=make('ul','','structural-pending');for(const text of result.pending||[])pending.append(make('li',text));host.append(pending);
    }
    const docs=make('div','','document-downloads');
    for(const artifact of read.artifacts as Artifact[]){const link=make('a',`${artifact.name} ↗`,'text-button') as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';docs.append(link);}host.append(docs);
  }
  private row(values:string[]){const row=document.createElement('tr');for(const value of values)row.append(make('td',value));return row;}
}
