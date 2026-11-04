import {fileUrl,type Context} from './api';

export interface ProcessReview {
  jobCount:number; attentionCount:number; repeatedAttemptCount:number; omittedGroupCount:number;
  outcomeCounts:Record<string,number>;
  repeatedAttempts:{kind:string;inputRevision:string;attemptCount:number;unsuccessfulCount:number;jobIds:string[];latestOutcome:string;needsAttention:boolean}[];
}

export interface AgentWorkflow {
  version:number; designBinding:string; requirementsChanged:boolean;
  reviewStatus:'not_started'|'needs_attention'|'recorded';
  decisions:{id:string;text:string;state:string;source:string;sourceText:string;reason:string;level:string}[];
  usageChecks:{id:string;aspect:string;scenario:string;action:string;expected:string;effectiveState:string;observation:string;method:string;limits:string;artifactIds:string[]}[];
  uncoveredDecisionIds:string[]; unreviewedAspects:string[]; nextAction:string; blocker:string;
}

const statuses:Record<string,string>={confirmed:'用户已确认',assumed:'Agent 暂定',open:'待明确',superseded:'已替代',pending:'待检查',issue:'发现问题',checked:'已记录检查',not_applicable:'不适用',stale:'设计变化，需复查'};
const aspects:Record<string,string>={visibility:'看得见、读得懂',reachability:'够得着、无阻挡',operation:'操作是否方便',feedback:'状态反馈是否明确',maintenance:'充电、清洁与维护',other:'其他使用检查'};
const node=(tag:string,text:string,cls='')=>{const e=document.createElement(tag);e.textContent=text;e.className=cls;return e;};
const userText=(text:string)=>{const e=node('p',text);e.translate=false;return e;};

export function renderAgentWorkflow(host:HTMLElement,context:Context|null){
  const expanded=new Set(host.dataset.project===context?.project.id?Array.from(host.querySelectorAll<HTMLDetailsElement>('details[open][data-key]'),d=>d.dataset.key):[]);
  host.replaceChildren();host.hidden=!context;
  if(!context)return;
  host.dataset.project=context.project.id;
  const section=(key:string)=>{const d=document.createElement('details');d.dataset.key=key;d.open=expanded.has(key);return d;};
  const workflow=context.agentWorkflow;
  host.append(node('h3','设计与使用检查'));
  if(!workflow||workflow.reviewStatus==='not_started'){
    host.append(node('p','尚未记录使用场景；功能检查通过不代表使用方便。','field-note'));
    if(!workflow)return;
  }
  if(workflow.requirementsChanged)host.append(node('p','需求已变化，Agent 需核对已有决定，无需重复确认未改变的选择。','workflow-attention'));
  if(workflow.nextAction){host.append(node('strong','接下来'),userText(workflow.nextAction));}
  if(workflow.blocker){host.append(node('strong','当前阻塞','workflow-attention'),userText(workflow.blocker));}
  if(context.processReview){
    const process=context.processReview,details=section('process-review');
    const title=node('summary','设计过程复盘');details.append(title);
    const count=(label:string,value:number)=>{const p=node('p','');p.append(node('span',label),document.createTextNode('：'+value));return p;};
    details.append(count('已记录任务',process.jobCount),count('当前需比较原因的重复尝试',process.attentionCount));
    details.append(node('p','任务执行完成不等于设计被采纳；历史问题不会自动成为当前阻塞。','field-note'));
    const outcomes:Record<string,string>={adopted:'已采纳',adopted_after_review:'审阅后已采纳',awaiting_review:'等待审阅',historical_candidate:'历史候选',not_adopted:'未采纳',check_failed:'检查未通过',failed:'执行失败',completed:'执行完成',active:'正在执行',stopped:'已停止',unknown:'未知'};
    const kinds:Record<string,string>={'pcb.route':'PCB 布线','pcb.edit':'PCB 修改','pcb.initialize':'PCB 初始化','pcb.handoff':'PCB 交接','schematic.edit':'原理图修改','schematic.review':'电气审查','mechanical.design':'机械建模','pcb.geometry_export':'PCBA 几何导出'};
    for(const row of process.repeatedAttempts){
      const item=section('attempt-'+row.kind+'-'+row.inputRevision);
      item.className='workflow-entry';
      const summary=node('summary','');summary.append(node('span',kinds[row.kind]||row.kind),document.createTextNode(' · '+row.attemptCount+' '),node('span','次尝试'),document.createTextNode(' · '),node('span',row.needsAttention?'继续前比较原因':'历史尝试记录'));
      item.append(summary,
        userText(row.kind+' · '+row.inputRevision),count('尝试次数',row.attemptCount),count('失败或未采纳次数',row.unsuccessfulCount),
        node('p',outcomes[row.latestOutcome]||row.latestOutcome),userText(row.jobIds.join('\n')));
      details.append(item);
    }
    if(process.omittedGroupCount)details.append(count('其余历史分组',process.omittedGroupCount));
    details.append(node('p','同一设计输入也可能采用不同参数；比较任务输入和反馈后再决定是否重试。','field-note'));
    host.append(details);
  }
  const active=workflow.decisions.filter(d=>d.state!=='superseded');
  if(active.length){
    const details=section('decisions');details.append(node('summary','已确认与暂定的决定'));
    for(const decision of active){
      const row=node('div','','workflow-entry');
      row.append(node('span',statuses[decision.state]||decision.state,'small-tag'),userText(decision.text));
      if(workflow.uncoveredDecisionIds.includes(decision.id))row.append(node('small','尚无当前使用检查','workflow-attention'));
      const source=section('source-'+decision.id);source.append(node('summary','原话与依据'),userText(decision.sourceText),userText(decision.reason));row.append(source);details.append(row);
    }
    host.append(details);
  }
  const outstanding=workflow.usageChecks.filter(c=>!['checked','not_applicable'].includes(c.effectiveState));
  if(outstanding.length)host.append(node('p','仍有使用问题或检查待完成。','workflow-attention'));
  for(const check of [...outstanding,...workflow.usageChecks.filter(c=>['checked','not_applicable'].includes(c.effectiveState))]){
    const details=section('check-'+check.id);details.className='workflow-entry';
    const summary=node('summary','');summary.append(node('span',aspects[check.aspect]||check.aspect),document.createTextNode(' · '),node('span',statuses[check.effectiveState]||check.effectiveState));details.append(summary);
    for(const [label,value] of [['场景',check.scenario],['操作',check.action],['预期',check.expected],['观察',check.observation],['检查方法',check.method],['证据局限',check.limits]]){
      if(value)details.append(node('strong',label),userText(value));
    }
    for(const id of check.artifactIds){
      const artifact=context.artifacts.find(a=>a.id===id);
      if(artifact){const link=document.createElement('a');link.href=fileUrl(artifact);link.textContent=artifact.name;link.translate=false;link.target='_blank';link.rel='noopener';details.append(link);}
    }
    host.append(details);
  }
  if(workflow.unreviewedAspects.length){
    const details=section('aspects');details.append(node('summary','还需考虑的使用角度'));
    for(const aspect of workflow.unreviewedAspects)details.append(node('p',aspects[aspect]||aspect));host.append(details);
  }
  host.append(node('p','以上为 Agent 的检查记录；不等于实物体验或制造验收。','field-note'));
}
