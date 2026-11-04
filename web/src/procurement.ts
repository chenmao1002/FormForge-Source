import {api,fileUrl,requestId,type Context,type Revision} from './api';
import {translateText} from './i18n';

const make=(tag:string,cls='',text='')=>{const e=document.createElement(tag);e.className=cls;e.textContent=text;return e;};
const dispositions:Record<string,string>={selected:'供应商选料', 'customer-supplied':'客供器件',unresolved:'尚未确定'};
const readable=(text:string)=>text.replace(/resistanceOhm|capacitanceF|tolerancePercent|ratedPowerW|ratedVoltageV|dielectric/g,key=>({resistanceOhm:'电阻值',capacitanceF:'电容量',tolerancePercent:'公差',ratedPowerW:'额定功率',ratedVoltageV:'额定电压',dielectric:'介质'}[key]||key));

export class ProcurementPanel {
  private signature='';private generation=0;private selected=new Map<string,string>();private following=new Map<string,boolean>();
  private drafts=new Map<string,{job:string;quantity:string;message:string}>();private lastContext:Context|null=null;
  constructor(private host:HTMLElement,private refresh:()=>Promise<void>,private notify:(text:string,error?:boolean)=>void){
    window.setInterval(()=>{if(!this.host.hidden&&this.lastContext)void this.render(this.lastContext);},60000);
  }

  async render(context:Context|null){
    this.lastContext=context;
    if(!context){this.signature='';this.generation++;this.host.replaceChildren();return;}
    const pid=context.project.id,versions=context.revisions.filter(r=>r.module==='manufacturing'&&r.procurementDesign);
    const follow=this.following.get(pid)!==false;
    const chosen=follow?versions[0]:versions.find(r=>r.id===this.selected.get(pid))||versions[0];
    const sig=JSON.stringify([pid,context.project.version,chosen?.id,follow,context.jobs.map(j=>[j.id,j.status,j.phase]),context.artifacts.filter(a=>a.kind.startsWith('procurement_')).map(a=>a.id),Math.floor(Date.now()/60000)]);
    if(sig===this.signature)return;this.signature=sig;const token=++this.generation;
    let read:any=null;
    try{if(chosen)read=await api(`projects/${pid}/procurement?revisionId=${encodeURIComponent(chosen.id)}`);}
    catch(error){if(token===this.generation){this.signature='';this.host.replaceChildren(make('p','connection-problem',(error as Error).message));}return;}
    if(token!==this.generation)return;
    this.host.replaceChildren(make('h2','','采购计划'));
    this.host.append(make('p','quiet-note','独立记录准确型号、资料依据与待确定项。服务核对声明数据和原生约束，工程、供应商平台匹配和生产资格另验。'));
    const toolbar=make('div','revision-toolbar'),select=document.createElement('select');select.setAttribute('aria-label','采购计划版本');
    for(const revision of versions){const option=document.createElement('option');option.value=revision.id;option.textContent=`${revision.id.slice(-6)} · ${revision.id===context.project.moduleHeads?.manufacturing?'当前计划 · ':''}${revision.validation?.adopted===false?'未采纳 · ':''}${revision.message||'采购计划'}`;select.append(option);}
    select.disabled=!versions.length;if(chosen)select.value=chosen.id;
    select.onchange=()=>{this.selected.set(pid,select.value);this.following.set(pid,false);this.signature='';void this.render(context);};
    const followButton=make('button','text-button',follow?'正在跟随采购更新':'跟随采购更新') as HTMLButtonElement;followButton.onclick=()=>{this.following.set(pid,true);this.signature='';void this.render(context);};toolbar.append(select,followButton);this.host.append(toolbar);
    if(read&&chosen){this.selected.set(pid,chosen.id);this.result(context,chosen,read,token);}
    else this.host.append(make('p','empty-small','还没有采购计划。Agent可通过MCP登记器件事实和原始依据，或先建立明确待选项的草案。'));
    this.form(context);
  }

  private result(context:Context,revision:Revision,read:any,token:number){
    const result=read.result,plan=read.plan,job=context.jobs.find(j=>j.id===revision.createdByJobId);
    const heading=make('div','card-heading');heading.append(make('b','',`采购版本 ${revision.id.slice(-6)}`),make('span','badge '+(result?.procurementReviewed&&result?.adopted?'success':'warning'),!result?'候选 · '+(job?.phase||'等待检查'):!result.adopted?'未采纳':result.procurementReviewed?'料号资料检查通过':'采购草案'));this.host.append(heading);
    if(read.requiresDependencyReview)this.host.append(make('p','connection-problem','原图/PCB版本或需求已变化，当前采购依据需要复核。'));
    this.host.append(make('p','field-note',`基准制造任务 ${plan.manufacturingDependency.jobId.slice(-8)} · 数量 ${plan.boardQuantity??'未明确'}`),
      make('p','field-note',plan.reviewStatement),make('p','connection-problem','外部调用方的审核声明 · 采购检查不包含平台下单或生产放行，订单状态请另行查看。'));
    if(result){
      this.host.append(make('p','',`已选 ${result.resolvedReferenceCount} / ${result.selectedReferenceCount} 个器件 · ${result.unresolvedReferences.length} 项未确定`));
      if(result.matchingFilesReady)this.host.append(make('p','notice',`已生成平台选型资料，保留全部 ${result.selectedReferenceCount} 个器件。未确定项仍需选料；${result.matchingUnreviewedCenters.length} 项坐标沿用未审核的原生锚点。工程中心、CAD角度和平台贴装方向需要分别核对，当前资料不用于生产放行。`));
      if(result.matchingExport?.referenceCoverageVerified)this.host.append(make('p','field-note',`选型 BOM：${result.matchingExport.bomLineCount} 组 · 坐标：${result.matchingExport.cplReferenceCount} 个位号。相同料号及全部选型字段一致的器件合并，数量按单板计；位号覆盖和逐件坐标回读已核对。`));
      for(const p of result.problems)this.host.append(make('p','connection-problem',`${p.reference?p.reference+' · ':''}${readable(p.message)}`));
      if(result.problemCount>result.problems.length)this.host.append(make('p','field-note','更多问题见完整报告。'));
      const commercial=make('details');commercial.append(make('summary','','材料费快照与时效'));
      const totals=Object.entries(result.materialsSubtotalsByCurrency);
      commercial.append(make('p','field-note',totals.length?totals.map(([currency,value])=>`${currency} ${value}`).join(' · '):'没有可用的材料小计。'),
        make('p','field-note',result.indicativeMaterialsEstimateComplete?'所选采购行的声明快照可计算；不含PCB、装配、税费、运费及平台最终损耗。':'小计仅来自可计算行；料号、数量、价格或库存仍有缺项，不能作为整机报价。'));
      for(const line of result.commercial){const snapshot=line.snapshot;const current=read.currentOfferFreshness[line.partSourceId];commercial.append(make('p','field-note',`${line.references.join('、')} · 所需 ${line.requiredUnits??'未明确'} · 采购倍数后 ${snapshot.purchaseUnits??'未计算'} · ${current?.fresh?'资料时效内':current?.state==='missing'?'无商业快照':'需刷新资料'} · ${snapshot.stockSufficient===true?'声明库存覆盖':snapshot.stockSufficient===false?'声明库存不足':'库存未知'}`));}
      commercial.append(make('p','field-note','快照不是预留库存或正式平台报价；币种分别计算。页面每分钟重查已登记快照时效，不联网更新库存。'));this.host.append(commercial);
      const rows=make('div','procurement-groups'),resolved=make('div','procurement-lines'),pending=make('details'),pendingRows=make('div','procurement-lines');
      if(result.resolvedReferenceCount)resolved.append(make('h3','','已选器件与来源'));
      pending.hidden=!result.unresolvedReferences.length;pending.append(make('summary','',`待确定器件 · ${result.unresolvedReferences.length} 项`),pendingRows);
      rows.append(resolved,pending);this.host.append(rows);
      const showRows=(values:any[])=>{for(const row of values){const item=make('div','procurement-line');item.append(make('strong','',`${row.reference} · ${dispositions[row.disposition]||row.disposition}`),make('p','field-note',`${row.nativeValue||''} · ${row.footprint||''}`));
        if(row.mpn)item.append(make('p','',`${row.manufacturer} · ${row.mpn}`),make('p','field-note',`${row.supplier} · ${row.supplierPartNumber||'未给供应商编码'}`));
        item.append(make('p','field-note',row.reason));
        item.append(make('p','field-note',row.supplierPosition?`已声明质心/方向：${row.supplierPosition.xMm}, ${row.supplierPosition.yMm} mm · ${row.supplierPosition.rotationDegrees}°`:'供应商质心和方向尚未审核'));
        if(row.partSourceId){const link=make('a','text-button','器件事实原件 ↗') as HTMLAnchorElement;link.href=`/api/projects/${context.project.id}/sources/${row.partSourceId}/file`;item.append(link);}(row.disposition==='unresolved'?pendingRows:resolved).append(item);}};
      showRows(read.rows);let next=read.nextOffset;
      const more=make('button','text-button','继续读取器件') as HTMLButtonElement;more.hidden=next===null;more.onclick=async()=>{more.disabled=true;try{const page=await api(`projects/${context.project.id}/procurement?revisionId=${revision.id}&offset=${next}`);if(token!==this.generation)return;showRows(page.rows);next=page.nextOffset;more.hidden=next===null;}catch(error){this.notify((error as Error).message,true);}finally{more.disabled=false;}};this.host.append(more);
      const sources=make('details');sources.append(make('summary','',`冻结资料与来源（${plan.sourceCount} 份）`));
      for(const source of read.sourceRecords){const line=make('p','field-note',`${source.name} · ${source.version} · ${source.origin==='project-authored'?'调用方整理':'来源性质由调用方声明'}`);const link=make('a','text-button','原件 ↗') as HTMLAnchorElement;link.href=`/api/projects/${context.project.id}/sources/${source.id}/file`;line.append(link);sources.append(line,make('code','library-digest',source.sha256),make('p','field-note',source.uri));}if(read.sourceRecordsTruncated)sources.append(make('p','field-note','更多来源见完整采购计划或工程资料。'));this.host.append(sources);
    }
    if(job?.error)this.host.append(make('pre','firmware-error',String(job.error)));
    const docs=make('div','document-downloads');
    for(const [kind,title] of [['procurement_project',result?.adopted?'下载采购计划包 ↗':'下载候选诊断包 ↗'],['procurement_report','采购检查报告 ↗'],['procurement_review','完整采购审核表 ↗'],['procurement_plan','完整计划与来源 ↗'],['procurement_jlc_bom','JLCPCB BOM 审核副本 ↗'],['procurement_jlc_cpl','JLCPCB CPL 审核副本 ↗'],['procurement_matching_bom','嘉立创选型 BOM（非生产）↗'],['procurement_matching_cpl','嘉立创选型坐标（非生产）↗'],['procurement_matching_notes','逐件选型与坐标待核说明 ↗']]){
      const artifact=context.artifacts.find(a=>a.projectId===context.project.id&&a.revisionId===revision.id&&a.jobId===revision.createdByJobId&&a.kind===kind);
      if(artifact){const link=make('a','text-button',title) as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';docs.append(link);}
    }
    this.host.append(docs);
  }

  private form(context:Context){
    const bases=context.jobs.filter(j=>j.kind==='manufacturing.prepare'&&j.status==='completed'&&j.result?.fileConsistencyPassed&&j.result?.selectedParts?.length);
    if(!bases.length){this.host.append(make('p','field-note','先准备包含待装器件且资料一致的制造场景。仅PCB场景不需要采购计划。'));return;}
    const pid=context.project.id,draft=this.drafts.get(pid)||{job:bases[0].id,quantity:'',message:translateText('等待准确料号、原始依据及质心/方向审核')};this.drafts.set(pid,draft);
    if(!bases.some(j=>j.id===draft.job))draft.job=bases[0].id;
    const form=document.createElement('form');form.className='manufacturing-form';form.append(make('h3','','建立新的待选草案'));
    const label=make('label','field-label','基准制造资料'),basis=document.createElement('select');basis.setAttribute('aria-label','采购基准制造资料');
    for(const job of bases){const option=document.createElement('option');option.value=job.id;option.textContent=`${job.id.slice(-6)} · ${job.result.selectedParts.length} 个待装器件 · ${job.inputRevision===context.project.headRevisionId?'当前设计':'历史设计'}`;basis.append(option);}basis.value=draft.job;basis.onchange=()=>draft.job=basis.value;label.append(basis);form.append(label);
    const qtyLabel=make('label','field-label','计划数量（可留空）'),qty=document.createElement('input');qty.type='number';qty.min='1';qty.max='1000000';qty.step='1';qty.value=draft.quantity;qty.oninput=()=>draft.quantity=qty.value;qtyLabel.append(qty);form.append(qtyLabel);
    const noteLabel=make('label','field-label','待选原因'),note=document.createElement('input');note.required=true;note.maxLength=2000;note.value=draft.message;note.oninput=()=>draft.message=note.value;noteLabel.append(note);form.append(noteLabel);
    const submit=make('button','','创建待选采购草案') as HTMLButtonElement;submit.type='submit';submit.disabled=context.jobs.some(j=>['queued','running'].includes(j.status));
    form.append(make('p','field-note','新草案把该场景全部器件明确标为未确定，原采购版本保留。随后由Agent登记事实和依据，再生成新的采购版本。'),submit);
    form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{
      const job=bases.find(j=>j.id===basis.value)!;const artifact=context.artifacts.find(a=>a.jobId===job.id&&a.revisionId===job.inputRevision&&a.kind==='manufacturing_project');if(!artifact)throw new Error('该场景缺少完整制造审核包。');
      await api(`projects/${pid}/procurement-plans`,'POST',{expectedProjectVersion:context.project.version,expectedManufacturingRevisionId:context.project.moduleHeads?.manufacturing||null,
        manufacturingJobId:job.id,expectedPackageSha256:artifact.sha256,mode:'draft',targetFormat:'review-only',boardQuantity:qty.value?Number(qty.value):null,sources:[],placements:[],
        lines:[{references:job.result.selectedParts.map((p:any)=>p.reference),disposition:'unresolved',partSourceId:null,extraUnits:0,reason:note.value}],reviewStatement:note.value,message:'待选采购草案：'+note.value.slice(0,100),requestId:requestId()});
      this.following.set(pid,true);this.signature='';this.notify('已提交待选采购草案。');await this.refresh();
    }catch(error){this.notify((error as Error).message,true);submit.disabled=false;}};
    this.host.append(form);
  }
}
