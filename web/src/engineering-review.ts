import {fileUrl,type Artifact,type Context,type Job,type Revision} from './api';
import {locale} from './i18n';

export interface ReviewEntry {kind:'current'|'inherited';job:Job;artifacts:Artifact[]}

// A PCB's frozen handoff review remains provenance. A later review of that
// exact PCB revision is a separate result, including a completed failed review.
export function selectEngineeringReviews(context:Context,revision:Revision):ReviewEntry[]{
  const valid=(job:Job,id:string)=>job.projectId===context.project.id&&job.kind==='schematic.review'&&job.status==='completed'&&job.inputRevision===id&&job.result?.revisionId===id;
  const current=context.jobs.filter(j=>valid(j,revision.id)).sort((a,b)=>b.createdAt.localeCompare(a.createdAt)||b.id.localeCompare(a.id))[0];
  const inherited=revision.pcbDesign&&context.jobs.find(j=>j.id===revision.pcbDesign?.reviewJobId&&valid(j,revision.pcbDesign.schematicRevisionId));
  const entries:ReviewEntry[]=[];
  const add=(kind:ReviewEntry['kind'],job:Job)=>entries.push({kind,job,artifacts:context.artifacts.filter(a=>a.projectId===context.project.id&&a.revisionId===job.inputRevision&&a.jobId===job.id&&['design_review','design_review_evidence'].includes(a.kind))});
  if(current)add('current',current);
  if(inherited&&inherited.id!==current?.id)add('inherited',inherited);
  return entries;
}

const make=(tag:string,cls='',content='')=>{const element=document.createElement(tag);element.className=cls;element.textContent=content;return element;};

export function selectPinReview(context:Context,revision:Revision){
  const seen=new Set<string>();let id:string|undefined=revision.id;
  while(id&&!seen.has(id)){
    seen.add(id);
    const source=context.jobs.filter(j=>j.projectId===context.project.id&&j.kind==='schematic.review'&&j.status==='completed'&&j.inputRevision===id&&j.result?.revisionId===id&&j.result?.pinReview).sort((a,b)=>b.createdAt.localeCompare(a.createdAt)||b.id.localeCompare(a.id))[0];
    if(source)return {...source.result.pinReview,...(id===revision.id?{}:{recorded:0,open:0,needsReview:source.result.pinReview.entries.length}),entries:source.result.pinReview.entries.map((row:any)=>id===revision.id?row:{...row,status:'needs_review',invalidationReason:'revision_changed',currentRevisionId:revision.id})};
    id=context.revisions.find(r=>r.id===id)?.parentRevisionId;
  }
  return undefined;
}

function renderPinReview(parent:HTMLElement,pinReview:any){
  if(pinReview){
      const detail=make('details');detail.id='pin-review-records';detail.append(make('summary','','逐引脚复核记录'));
      detail.append(make('p','quiet-note','理由按修订和图页摘要保存；待重审或待调整不代表电气错误，也不会隐藏图面提示。'));
      for(const record of pinReview.entries){
        const row=make('p','field-note');row.append(make('span','',record.status==='recorded'?'已记录':record.status==='open'?'待调整':'需要重新复核'));
        const decision={keep_label:'保留标签',accepted_layout:'接受当前布局',needs_layout:'需要调整布局'}[record.decision as 'keep_label'|'accepted_layout'|'needs_layout'];
        row.append(make('span','',' · '),make('span','',decision));
        const identity=make('span','',` · ${record.reference}.${record.pin} · ${record.sheet}`);identity.translate=false;row.append(identity);detail.append(row);
        const reason=make('p','field-note',record.reason);reason.translate=false;detail.append(reason);
        if(record.invalidationReason){const cause=make('p','connection-problem',record.invalidationReason==='revision_changed'?'修订已变化':record.invalidationReason==='sheet_changed'?'图页内容已变化':'图页或引脚不可用');detail.append(cause);}
      }
      parent.append(detail);
    }
}

export function renderEngineeringReview(context:Context,revision:Revision,host:HTMLElement,title:HTMLElement,content:HTMLElement){
  const entries=selectEngineeringReviews(context,revision);
  const pinReview=selectPinReview(context,revision);
  host.hidden=!entries.length&&!pinReview;content.replaceChildren();
  if(!entries.length){if(pinReview){title.textContent='逐引脚复核记录';renderPinReview(content,pinReview);}return;}
  const historical=revision.id!==context.project.headRevisionId;
  const label=(entry:ReviewEntry)=>entry.kind==='inherited'?'来源原理图电气审查':revision.pcbDesign?'本版电气复核':'电气约束审查';
  const status=(entry:ReviewEntry)=>`${label(entry)} · ${entry.job.result.checksPassed?'所列检查通过':'存在待处理项'}${historical?' · 历史版本':''}`;
  title.textContent=status(entries[0]);
  const append=(parent:HTMLElement,entry:ReviewEntry)=>{
    const review=entry.job.result;
    parent.append(make('p','field-note',entry.kind==='inherited'?`本板创建时冻结了来源原理图 ${entry.job.inputRevision?.slice(-8)} 的电气审查；这是交接依据，当前 PCB 检查另列。`:`本次复核绑定所选设计版本 ${revision.id.slice(-8)}；${new Date(entry.job.finishedAt||entry.job.createdAt).toLocaleString(locale(),{hour12:false})}。`));
    if(entry.kind==='current'&&!historical&&review.projectVersion!==context.project.version)parent.append(make('p','connection-problem','复核后工程版本已变化；继续交接前需要重新核对当前条件。'));
    parent.append(make('p','field-note',review.scope),make('p','quiet-note','资料解读和单位由设计方审查；服务核对版本、属性、连接并重算。通过表示可继续 PCB 设计，实物与制造仍待验证。'));
    const calculations=review.checks.filter((c:any)=>c.kind==='calculation');
    const featured=calculations.filter((c:any,index:number)=>index<4||c.title.includes('剩余预算'));
    const rest=calculations.filter((c:any)=>!featured.includes(c));
    const row=(c:any)=>make('p','field-note',`${c.passed?'✓':'!'} ${c.title}：${Number(c.value.toFixed(4))} ${c.unit} ${c.operator==='le'?'≤':'≥'} ${Number(c.limitValue.toFixed(4))} ${c.unit}${c.qualifications.length?'（含工程预算或典型值）':''}`);
    for(const c of featured)parent.append(row(c));
    if(rest.length){const more=make('details');more.append(make('summary','',`其余 ${rest.length} 项器件裕量与计算`));for(const c of rest)more.append(row(c));parent.append(more);}
    const connections=review.checks.filter((c:any)=>c.kind==='connection');
    const groups=review.checks.filter((c:any)=>c.kind==='layout_group');
    if(groups.length){
      const detail=make('details');detail.id='layout-group-checks';detail.append(make('summary','','功能组与重复通道'));
      detail.append(make('p','quiet-note','仅检查声明的布局关系与接口引脚存在性，不证明电路功能或接口完整性。'));
      for(const group of groups){
        const row=make('p','field-note');row.append(make('span','',group.passed?'通过':'需处理'));
        const title=make('span','',` · ${group.title}`);title.translate=false;row.append(title);detail.append(row);
        const intent=make('p','field-note',group.intent);intent.translate=false;detail.append(intent);
        for(const issue of group.issues){
          const labels:Record<string,string>={missing_symbol:'成员不存在',unsupported_pin_geometry:'引脚几何不可用',fixed_position:'固定位置偏离',members_span_sheets:'成员跨图页',unknown_interface_pin:'接口引脚不存在',invalid_reference_group:'基准组检查未通过',role_set_mismatch:'角色不一致',relative_position:'相对位置不一致',orientation:'方向不一致',pin_geometry:'引脚几何不一致',overlapping_members:'重复通道复用了同一器件'};
          const line=make('p','connection-problem');line.append(make('span','',labels[issue.reason]||'布局检查异常'));
          const values=make('span','',` · ${issue.role||issue.reference||group.groupId}${issue.expected?` · ${JSON.stringify(issue.expected)} → ${JSON.stringify(issue.actual)}`:''}`);values.translate=false;line.append(values);detail.append(line);
        }
      }
      parent.append(detail);
    }
    if(connections.length){
      const detail=make('details');detail.append(make('summary','','连接断言'));
      for(const c of connections){
        const item=make('p','field-note');item.append(make('span','',c.passed?'通过':'需处理'));
        item.append(make('span','',c.mode==='different_nets'?' · 两两异网':c.mode==='exact_net'?' · 精确网络成员':' · 同网'));
        const text=make('span','',` · ${c.title} · ${c.pins.map((p:any)=>`${p.reference}.${p.pin}`).join(', ')}`);text.translate=false;item.append(text);
        for(const [key,label] of [['unknownPins','未知引脚'],['extraPins','额外引脚'],['missingPins','缺失引脚']]){
          if(c[key]?.length){item.append(make('span','',` · ${label}`));const pins=make('span','',': '+c[key].map((p:any)=>`${p.reference}.${p.pin}`).join(', '));pins.translate=false;item.append(pins);}
        }
        detail.append(item);
      }
      parent.append(detail);
    }
    const conditions=make('details');conditions.append(make('summary','',`配置条件及待实测项 · ${review.conditions.length+review.hardwareVerification.length}`));
    for(const value of review.conditions)conditions.append(make('p','field-note',value));
    for(const value of review.hardwareVerification)conditions.append(make('p','field-note','待实测：'+value));
    parent.append(conditions);
    for(const artifact of entry.artifacts){const link=make('a','text-button',artifact.kind==='design_review'?'下载电气审查报告 ↗':'下载审查资料包 ↗') as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';parent.append(link);}
    const handoffJob=entry.kind==='inherited'?revision.pcbDesign?.handoffJobId:!revision.entrypoints.board?context.jobs.find(j=>j.projectId===context.project.id&&j.kind==='pcb.handoff'&&j.inputRevision===revision.id&&j.status==='completed'&&j.result?.checksPassed)?.id:undefined;
    const handoff=handoffJob&&context.artifacts.find(a=>a.projectId===context.project.id&&a.jobId===handoffJob&&a.revisionId===entry.job.inputRevision&&a.kind==='pcb_handoff');
    if(handoff){const link=make('a','text-button','下载 PCB 连接交接包 ↗') as HTMLAnchorElement;link.href=fileUrl(handoff)+'?download=true';parent.append(link);}
  };
  append(content,entries[0]);
  renderPinReview(content,pinReview);
  for(const entry of entries.slice(1)){const detail=make('details','more-changes');detail.append(make('summary','',status(entry)+' · 创建 PCB 时冻结'));append(detail,entry);content.append(detail);}
}
