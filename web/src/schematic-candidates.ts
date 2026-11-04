import {api,fileUrl,type Artifact,type Context} from './api';

type Review={projectId:string;jobId:string;state:string;message:string;canAdopt:boolean;expectedProjectVersion:number;beforeRevisionId?:string;afterRevisionId?:string;changes:any[];checks:any[];checksPassed:boolean;unmetConstraints:any[];reviewNotes:any[];fileChanges:any[];beforePreviews:Artifact[];afterPreviews:Artifact[];reportArtifacts:Artifact[]};
const node=(tag:string,cls='',text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};

export function changeCaption(change:any):string{
  if(change.kind==='auto_layout'){
    if(change.blockLayout){const b=change.blockLayout;return `整理 ${b.changedSheets.length} 张原有图页中的 ${b.groups.length} 个完整功能块；块内布局与连接保持。`;}
    if(change.organization)return '按功能页组织：'+change.organization.pages.map((p:any)=>p.name).join('、');
    if(change.branchLayout)return `整理 ${change.branchLayout.branches.length} 条声明支路与相关文字。`;
    return '按已提交的布局意图整理图页。';
  }
  if(change.reference)return change.reference+' · '+(change.kind==='set_property'?'更新 '+change.property:change.kind==='move_field'?'调整字段位置':'整理已声明对象');
  return '已声明的原理图修改，详见同版报告。';
}

export class SchematicCandidates {
  private select=document.createElement('select');
  private detail=node('div','schematic-candidate-detail');
  private context?:Context;
  private project='';
  private selected='';
  private key='';
  private ticket=0;
  private scale=1;
  constructor(private host:HTMLElement){
    const heading=node('div','card-heading');heading.append(node('h2','','排版候选'));
    const label=node('label','candidate-picker','选择候选');this.select.setAttribute('aria-label','选择排版候选');label.append(this.select);
    this.select.onchange=()=>{this.selected=this.select.value;this.key='';this.scale=1;if(this.context)this.render(this.context);};
    this.host.append(heading,node('p','field-note','对比同一任务的原生前后图，检查通过后再采纳。'),label,this.detail);
  }
  render(context:Context|null){
    this.host.hidden=!context;
    if(!context){this.ticket++;this.key='';return;}
    this.context=context;
    if(this.project!==context.project.id){this.project=context.project.id;this.selected='';this.key='';this.scale=1;}
    const jobs=context.jobs.filter(j=>j.kind==='schematic.edit');
    if(!jobs.some(j=>j.id===this.selected))this.selected=jobs[0]?.id||'';
    this.select.replaceChildren(...jobs.map(job=>{
      const option=document.createElement('option');option.value=job.id;
      const revision=context.revisions.find(r=>r.createdByJobId===job.id);
      const label=revision?.validation?.adopted?'已采纳':revision?.validation?.status==='ready'?(revision.parentRevisionId===context.project.headRevisionId?'待审阅':'历史候选'):['queued','running'].includes(job.status)?'检查中':job.result?.checksPassed?'已检查':'需处理';
      option.textContent=new Date(job.createdAt).toLocaleString()+' · '+label;return option;
    }));
    this.select.value=this.selected;this.select.disabled=!jobs.length;
    if(!jobs.length){this.detail.replaceChildren(node('p','field-note','尚无排版候选。Agent 提交候选后，可在这里对比并采纳。'));return;}
    const job=jobs.find(j=>j.id===this.selected)!;
    const key=[this.project,this.selected,job.status,context.project.version,context.project.headRevisionId,context.jobs.filter(j=>['queued','running'].includes(j.status)).map(j=>j.id).join(',')].join(':');
    if(key===this.key)return;
    this.key=key;const ticket=++this.ticket,pid=this.project,jid=this.selected;
    this.detail.replaceChildren(node('p','field-note','正在读取候选与同版检查…'));
    void api<Review>(`projects/${pid}/schematic-candidates/${jid}`).then(review=>{
      if(ticket!==this.ticket)return;this.paint(review);
    }).catch(error=>{if(ticket!==this.ticket)return;this.detail.replaceChildren(node('p','revision-finding failed',(error as Error).message));const retry=node('button','button secondary','重试加载') as HTMLButtonElement;retry.onclick=()=>{this.key='';if(this.context)this.render(this.context);};this.detail.append(retry);});
  }
  private paint(review:Review){
    this.detail.replaceChildren();
    const status=node('p','candidate-state '+review.state,review.message);status.setAttribute('role','status');this.detail.append(status);
    const changes=node('section','candidate-change-summary');changes.append(node('h3','','关键变化'));
    if(!review.changes.length)changes.append(node('p','field-note','尚无可用的修改结果。'));
    for(const change of review.changes){changes.append(node('p','',changeCaption(change)));const plan=change.organization?.pagePlanning;if(plan?.mode==='automatic')changes.append(node('p','field-note',`自动安排 ${plan.after.pageCount} 个功能页，跨页代价 ${plan.before.crossPageCost} → ${plan.after.crossPageCost}（估计）。`));}
    if(review.fileChanges.length)changes.append(node('p','field-note',`本次涉及 ${review.fileChanges.length} 个原生文件。`));
    this.detail.append(changes);
    const checks=node('section','candidate-checks');checks.append(node('h3','','同版检查'));
    for(const check of review.checks){const label=check.kind==='erc'?'ERC':check.kind==='connectivity'?'原生连接':check.kind==='preservation'?'数据保持':check.kind;checks.append(node('p','revision-finding '+(check.status==='passed'?'passed':'failed'),`${label} · ${check.status==='passed'?'通过':'未通过'} · ${check.errors??0} 错误 · ${check.warnings??0} 警告`));}
    if(!review.checks.length)checks.append(node('p','field-note',review.state==='checking'?'检查尚未完成。':'本任务未产生可用的原生检查结果。'));
    checks.append(node('p','field-note','原生检查不能代替图面可读性与硬件验证。'));this.detail.append(checks);
    const unmet=node('section','candidate-unmet');unmet.append(node('h3','','未满足项与待复核内容'));
    if(!review.unmetConstraints.length&&!review.reviewNotes.length)unmet.append(node('p','field-note','未报告阻止采纳的检查项；仍请复核原生图。'));
    for(const finding of review.unmetConstraints){unmet.append(node('p','revision-finding failed',finding.description));for(const item of (finding.items||[]).slice(0,6))unmet.append(node('p','field-note',item.description||'详见完整检查报告。'));}
    if(review.reviewNotes.length)unmet.append(node('p','revision-finding warning',`有 ${review.reviewNotes.length} 项文字或未覆盖对象需复核，详见同版报告。`));
    this.detail.append(unmet);
    const tools=node('div','candidate-zoom');tools.append(node('span','','原生前后图'));
    const images:HTMLImageElement[]=[];
    const fit=node('button','button secondary','适应图页') as HTMLButtonElement,zoom=node('button','button secondary','放大图纸') as HTMLButtonElement;
    const applyScale=()=>images.forEach(image=>image.style.width=this.scale===1?'100%':`${this.scale*100}%`);
    fit.onclick=()=>{this.scale=1;applyScale();};zoom.onclick=()=>{this.scale=Math.min(8,this.scale*1.5);applyScale();};tools.append(fit,zoom);this.detail.append(tools);
    const pair=node('div','candidate-comparison');
    const selectors:HTMLSelectElement[]=[];
    for(const [side,title,revision,previews] of [['before','优化前',review.beforeRevisionId,review.beforePreviews],['after','候选图',review.afterRevisionId,review.afterPreviews]] as const){
      const figure=node('figure','candidate-preview');figure.append(node('figcaption','',side==='after'&&previews.length&&!review.checksPassed?'候选诊断图（未通过检查）':title));
      const version=node('p','field-note',revision?'修订 '+revision.slice(-8):'尚无候选版本');figure.append(version);
      if(previews.length){
        const select=document.createElement('select');select.setAttribute('aria-label',title+'图页');
        const options=[...previews].sort((a,b)=>a.name.localeCompare(b.name));
        for(const artifact of options){const option=document.createElement('option');option.value=artifact.name;option.translate=false;option.textContent=artifact.name.replace(/\.svg$/,'');select.append(option);}
        const image=document.createElement('img');image.alt=title+'原生图';image.draggable=false;image.loading='eager';images.push(image);
        const viewport=node('div','candidate-preview-scroll');viewport.tabIndex=0;viewport.setAttribute('aria-label',title+'图纸，放大后可滚动');viewport.append(image);
        const load=()=>{const artifact=options.find(a=>a.name===select.value)!;image.src=fileUrl(artifact);};
        select.onchange=()=>{load();const other=selectors.find(s=>s!==select);if(other&&Array.from(other.options).some(o=>o.value===select.value)){other.value=select.value;other.dispatchEvent(new Event('candidate-page-sync'));}};
        select.addEventListener('candidate-page-sync',load);selectors.push(select);load();figure.append(select,viewport);
      }else figure.append(node('p','field-note',side==='before'?'此任务未导出修改前原生图。':'尚无原生候选图。'));
      pair.append(figure);
    }
    applyScale();this.detail.append(pair);
    const links=node('div','candidate-report-links');
    for(const artifact of review.reportArtifacts){const link=node('a','text-button',artifact.kind==='edit_report'?'完整检查报告':artifact.kind==='connectivity_report'?'连接核对报告':artifact.kind==='change_report'?'文件变化报告':artifact.kind==='preservation_report'?'数据保持报告':'原生规则报告') as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';links.append(link);}
    this.detail.append(links);
    const footer=node('div','candidate-adoption');const adopt=node('button','button primary',review.state==='adopted'?'此候选已采纳':'采纳此排版') as HTMLButtonElement;adopt.disabled=!review.canAdopt;
    const notice=node('p','field-note','采纳将更新当前原理图版本，下游设计需重新核对。');footer.append(adopt,notice);this.detail.append(footer);
    adopt.onclick=async()=>{
      if(!review.afterRevisionId||!review.beforeRevisionId)return;
      adopt.disabled=true;notice.textContent='正在核对候选与当前工程…';
      try{
        const receipt=await api(`projects/${review.projectId}/schematic-candidates/${review.afterRevisionId}/adopt`,'POST',{expectedProjectVersion:review.expectedProjectVersion,baseRevisionId:review.beforeRevisionId,jobId:review.jobId});
        notice.textContent='候选已采纳。';document.dispatchEvent(new CustomEvent('schematic-candidate-adopted',{detail:receipt}));
      }catch(error){notice.textContent=(error as Error).message;this.key='';document.dispatchEvent(new CustomEvent('schematic-candidate-refresh'));if(this.context)this.render(this.context);}
    };
  }
}
