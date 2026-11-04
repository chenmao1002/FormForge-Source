import {api,fileUrl} from './api';
import type {Context,Job} from './api';
import {locale} from './i18n';

const make=(tag:string,text='',cls='')=>{const e=document.createElement(tag);e.textContent=text;e.className=cls;return e;};
const field=(text:string,control:HTMLElement)=>{const label=make('label',text);label.append(control);return label;};
const when=(value:string)=>new Date(value).toLocaleString(locale(),{hour12:false});
const stage:Record<string,string>={prepared:'本地资料已准备','status-only':'记录平台状态',uploaded:'记录已上传',quoted:'记录平台报价',submitted:'订单已提交','under-review':'审核中',returned:'平台退回',cancelled:'已取消','awaiting-payment':'观察到待付款','unexpected-payment-or-production':'出现支付或生产状态'};
type Draft={version:number,basis:any,parts:any[],policy:string,reason:string,note:string,element?:HTMLElement};

export class StructuralQuotesPanel{
  private generation=0;
  private sourceHost=make('div');private formHost=make('div');private historyHost=make('div');
  private drafts=new Map<string,Draft>();private jobs=new Map<string,string>();private selected=new Map<string,string>();
  private following=new Map<string,boolean>();private pages=new Map<string,number>();private historyPages=new Map<string,number>();
  constructor(private host:HTMLElement,private refresh:()=>Promise<void>,private notify:(text:string,error?:boolean)=>void){
    host.append(make('h2','结构件报价与订单'),make('p','先建立绑定原始 STEP 的本地资料记录，再由 Agent 登记平台实际观察。此处不会上传文件、下单或付款。','field-note'),this.sourceHost,this.formHost,this.historyHost);
  }
  async render(context:Context|null){
    if(!context){++this.generation;this.sourceHost.replaceChildren();this.formHost.replaceChildren();this.historyHost.replaceChildren(make('p','先选择一个工程。','empty-small'));return;}
    const generation=++this.generation,pid=context.project.id,offset=this.pages.get(pid)||0;
    try{
      const [source,list]=await Promise.all([api(`projects/${pid}/structural`),api(`projects/${pid}/structural-quotes?offset=${offset}&limit=40`)]);
      if(generation!==this.generation)return;
      const jobs:Job[]=(source.jobs as Job[]).filter(j=>j.status==='completed').sort((a,b)=>b.createdAt.localeCompare(a.createdAt)||b.id.localeCompare(a.id));
      const chosenJob=jobs.find(j=>j.id===this.jobs.get(pid))||jobs[0];
      if(chosenJob)this.jobs.set(pid,chosenJob.id);
      if(!this.drafts.has(pid)&&chosenJob){
        const schema=await api(`structural-quote-schema?projectId=${pid}&jobId=${chosenJob.id}`);if(generation!==this.generation)return;
        this.drafts.set(pid,{version:schema.projectVersion,basis:schema.basis,parts:schema.basis.parts.map((p:any)=>({partId:p.partId,quantity:p.quantity,expectedStepSha256:p.step.sha256,material:null,supplierProcess:null,color:null,surfaceFinish:null})),policy:'require-current',reason:'',note:''});
      }
      this.sourceHost.replaceChildren();
      const select=document.createElement('select');select.setAttribute('aria-label','报价使用的结构资料');
      for(const job of jobs){const option=document.createElement('option');option.value=job.id;option.textContent=`${job.id.slice(-8)} · ${when(job.createdAt)}`;select.append(option);}select.disabled=!chosenJob;if(chosenJob)select.value=chosenJob.id;
      select.onchange=()=>{this.jobs.set(pid,select.value);this.drafts.delete(pid);this.formHost.replaceChildren(make('p','正在读取所选原件…','field-note'));void this.render(context);};
      const reset=make('button','按当前版本重新填写','text-button') as HTMLButtonElement;reset.onclick=()=>{this.drafts.delete(pid);void this.render(context);};this.sourceHost.append(field('已检查的结构资料',select),reset);
      const draft=this.drafts.get(pid);
      if(draft){
        if(!draft.element)draft.element=this.form(pid,draft);
        if(this.formHost.firstChild!==draft.element)this.formHost.replaceChildren(draft.element);
        const status=draft.element.querySelector('[data-quote-version]')!;status.textContent=`表单绑定工程 V${draft.version} · 机械 ${draft.basis.revisionId.slice(-8)}${draft.version!==context.project.version?'；工程已变化，提交前请明确重新填写。':''}`;
      }else this.formHost.replaceChildren(make('p','先完成一次结构件资料检查，再建立报价记录。','empty-small'));
      const following=this.following.get(pid)!==false;
      const chosen=following?list.quotes[0]:list.quotes.find((q:any)=>q.id===this.selected.get(pid))||list.quotes[0];
      const detail=chosen?await api(`projects/${pid}/structural-quotes?quoteId=${chosen.id}&offset=${this.historyPages.get(chosen.id)||0}&limit=40`):null;
      if(generation!==this.generation)return;
      if(chosen)this.selected.set(pid,chosen.id);
      this.history(context,list,detail,following,offset);
    }catch(error){if(generation===this.generation)this.historyHost.replaceChildren(make('p',(error as Error).message,'connection-problem'));}
  }
  private form(pid:string,draft:Draft){
    const form=document.createElement('form');form.className='structural-form';const version=make('p','','field-note');version.dataset.quoteVersion='';form.append(version);
    const parts=make('div','','structural-parts');
    for(const row of draft.parts){
      const group=document.createElement('fieldset');group.append(make('legend',`${row.partId} · ${row.quantity} 件`));
      for(const [key,title] of [['material','材料'],['supplierProcess','供应商工艺'],['color','颜色'],['surfaceFinish','表面处理']]){
        const input=document.createElement('input');input.maxLength=120;input.value=row[key]||'';input.placeholder='尚未明确';input.setAttribute('aria-label',`${row.partId} 报价${title}`);input.oninput=()=>{row[key]=input.value.trim()||null;};group.append(field(title,input));
      }
      group.append(make('p',`STEP ${row.expectedStepSha256.slice(0,16)}…；改数量请另建结构资料。`,'field-note'));parts.append(group);
    }
    const policy=document.createElement('select');policy.setAttribute('aria-label','报价依赖策略');
    for(const [value,title] of [['require-current','要求当前版本和依赖一致'],['historical-quote','明确跟踪历史模型报价']]){const option=document.createElement('option');option.value=value;option.textContent=title;policy.append(option);}policy.value=draft.policy;policy.onchange=()=>{draft.policy=policy.value;};
    const reason=document.createElement('input');reason.required=true;reason.maxLength=1000;reason.value=draft.reason;reason.setAttribute('aria-label','报价依赖说明');reason.oninput=()=>{draft.reason=reason.value;};
    const note=document.createElement('textarea');note.required=true;note.maxLength=2000;note.value=draft.note;note.setAttribute('aria-label','结构报价说明');note.oninput=()=>{draft.note=note.value;};
    const submit=make('button','建立本地报价资料记录','button primary') as HTMLButtonElement;submit.type='submit';
    form.append(parts,field('依赖策略',policy),field('依赖说明',reason),field('用途与说明',note),submit,make('p','材料等可留空，未知值会保留；建立记录不表示平台已经报价。','field-note'));
    form.onsubmit=async event=>{
      event.preventDefault();submit.disabled=true;
      try{
        await api(`projects/${pid}/structural-quotes`,'POST',{expectedProjectVersion:draft.version,structuralJobId:draft.basis.structuralJobId,expectedArchiveSha256:draft.basis.archive.sha256,
          dependencyPolicy:draft.policy,dependencyReason:draft.reason,provider:'jlc-3dp',parts:draft.parts,note:draft.note,requestId:crypto.randomUUID()});
        this.following.set(pid,true);this.pages.set(pid,0);this.notify('本地报价资料已记录，尚未上传平台。');await this.refresh();
      }catch(error){this.notify((error as Error).message,true);}finally{submit.disabled=false;}
    };
    return form;
  }
  private history(context:Context,list:any,detail:any,following:boolean,offset:number){
    const host=this.historyHost,pid=context.project.id;host.replaceChildren(make('h3','报价与订单记录'));
    const bar=make('div','','manufacturing-row'),select=document.createElement('select');select.setAttribute('aria-label','结构报价记录');select.disabled=!detail;
    for(const quote of list.quotes){const option=document.createElement('option');option.value=quote.id;option.textContent=`${quote.orderIdentity?.displayNumber||quote.id.slice(-8)} · ${stage[quote.stage]||quote.stage}`;select.append(option);}if(detail)select.value=detail.quote.id;
    select.onchange=()=>{this.selected.set(pid,select.value);this.following.set(pid,false);host.replaceChildren(make('p','正在读取所选报价记录…','field-note'));void this.render(context);};
    const follow=make('button',following?'正在跟随新记录':'跟随最新记录','text-button') as HTMLButtonElement;follow.onclick=()=>{this.following.set(pid,true);this.pages.set(pid,0);host.replaceChildren(make('p','正在读取最新报价记录…','field-note'));void this.render(context);};bar.append(select,follow);host.append(bar);
    if(list.total>40)host.append(this.pager(offset,list.total,next=>{this.pages.set(pid,next);void this.render(context);},'记录'));
    if(!detail){host.append(make('p','尚无记录。原始 STEP 准备完成后，可在上方建立本地资料记录。','empty-small'));return;}
    const quote=detail.quote,binding=quote.sourceBinding,obs=quote.latestObservation;
    host.append(make('strong',`嘉立创 3D · ${stage[quote.stage]||quote.stage}`),make('p',`记录版本 ${quote.version} · 机械 ${binding.revisionId.slice(-8)} · 结构任务 ${binding.structuralJobId.slice(-8)}`,'field-note'));
    if(!obs)host.append(make('p','仅本地资料：尚未上传平台，没有实际报价或订单。','notice'));
    if(quote.actionHalted)host.append(make('p','历史观察出现支付或生产状态；如实保留，请停止后续执行。','connection-problem'));
    if(!quote.inputIntegrityVerified)host.append(make('p',quote.inputIssue||'输入原件无法重新核对。','connection-problem'));
    if(quote.requiresDependencyReview)host.append(make('p','上游已变化；本记录仍绑定原始资料，使用前需复核。','connection-problem'));
    const table=document.createElement('table');table.className='structural-results';table.append(this.row(['零件 / 数量','材料','工艺','颜色 / 表面']));
    for(const p of quote.resolvedParts)table.append(this.row([`${p.partId} / ${p.quantity}`,p.material||'未明确',p.supplierProcess||'未明确',`${p.color||'未明确'} / ${p.surfaceFinish||'未明确'}`]));host.append(table,make('p',`创建时说明：${quote.request.note}`,'field-note'));
    const price=obs?.prices?obs:quote.lastPriceObservation;
    if(price){const p=price.prices;host.append(make('p',`${obs?.prices?'本次金额':'此前金额'}（${when(price.observedAt)}）：打印 ${this.money(p.partsAmount)} · 运费 ${this.money(p.shippingAmount)} · 税费 ${this.money(p.taxAmount)} · 优惠 ${this.money(p.discountAmount)} · 总额 ${this.money(p.totalAmount)}`),make('p',p.note,'quiet-note'));}
    if(obs)host.append(make('p',`${obs.providerStatusLabel} · ${obs.paymentStatusLabel} · ${obs.productionStatusLabel}`),make('p',`Agent 浏览器观察于 ${when(obs.observedAt)}；未独立认证供应商或平台文件字节。`,'quiet-note'));
    const downloads=make('div','','document-downloads');
    for(const artifact of [binding.archive,...binding.parts.map((p:any)=>p.step)]){const link=make('a',`${artifact.name} ↗`,'text-button') as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';downloads.append(link);}host.append(downloads);
    const official=make('a','打开嘉立创 3D 报价页 ↗','text-button') as HTMLAnchorElement;official.href='https://www.jlc-3dp.cn/placeOrder';official.target='_blank';official.rel='noopener noreferrer';host.append(official);
    for(const row of [...detail.observations].reverse()){
      const item=make('section','','order-observation');item.append(make('h3',`观察 ${row.quoteVersion} · ${when(row.observation.observedAt)}`),make('p',`${row.observation.providerStatusLabel} · ${row.observation.paymentStatusLabel}`,'field-note'),make('p',row.observation.note,'quiet-note'));
      for(const p of row.observation.parts)item.append(make('p',`${p.partId} / ${p.quantity} · 平台文件名 ${p.fileNameObserved||'本次未显示'} · ${p.material||'材料未显示'} · 金额 ${this.money(p.lineAmount)}`,'field-note'));
      const docs=make('div','','document-downloads');for(const source of row.evidence){const link=make('a',`${source.name} ↗`,'text-button') as HTMLAnchorElement;link.href=`/api/projects/${pid}/sources/${source.id}/file?download=true`;docs.append(link);}item.append(docs);host.append(item);
    }
    if(detail.total>40)host.append(this.pager(detail.offset,detail.total,next=>{this.historyPages.set(quote.id,next);void this.render(context);},'观察'));
  }
  private money(value:string|null){return value===null?'未明确':`${value} 元`;}
  private row(values:string[]){const row=document.createElement('tr');for(const value of values)row.append(make('td',value));return row;}
  private pager(offset:number,total:number,select:(offset:number)=>void,label:string){const host=make('div','','manufacturing-row');for(const [text,next] of [[`上一页${label}`,Math.max(0,offset-40)],[`下一页${label}`,offset+40]] as [string,number][]){const button=make('button',text,'text-button') as HTMLButtonElement;button.disabled=next===offset||next>=total;button.onclick=()=>select(next);host.append(button);}return host;}
}
