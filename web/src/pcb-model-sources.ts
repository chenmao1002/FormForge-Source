import {fileUrl,type Context,type PcbDependency} from './api';

const element=(tag:string,content:string,cls='')=>{const e=document.createElement(tag);e.textContent=content;e.className=cls;return e;};
const declared=(value:unknown)=>typeof value==='string'&&value.trim()?value:'未记录';

/** Show the exact frozen evidence selected by this mechanical revision. */
export function renderPcbModelSources(host:HTMLElement,context:Context,dependency:PcbDependency){
  const coverage=dependency.coverage;
  const counts=coverage&&Number.isInteger(coverage.selectedComponents)&&Number.isInteger(coverage.totalComponents)?`${coverage.selectedComponents}/${coverage.totalComponents} 个器件有模型`:'器件模型覆盖数量未记录';
  host.append(element('p',`本版记录已导入 PCB ${dependency.revisionId.slice(-8)} 的板体；${counts}。覆盖数量不表示原厂模型、精确选型或插拔资格。`,'field-note'));
  const details=document.createElement('details');details.className='source-details';
  const summary=element('summary','板上器件模型来源');details.append(summary);host.append(details);
  const job=context.jobs.find(j=>j.id===dependency.geometryJobId);
  const report=job?.result;
  const step=context.artifacts.find(a=>a.jobId===job?.id&&a.kind==='pcb_geometry_step'&&a.revisionId===dependency.revisionId);
  const bound=job?.kind==='pcb.geometry_export'&&job.status==='completed'&&report?.checksPassed&&report.revisionId===dependency.revisionId&&report.boardSha256===dependency.boardSha256&&typeof dependency.geometryStepSha256==='string'&&step?.sha256===dependency.geometryStepSha256;
  if(!bound||!Array.isArray(report?.models)){
    details.append(element('p','未取得与本机械版本完全对应的冻结模型来源。请核对原机械包和几何交接报告；这里不会用较新任务替代历史依据。','field-note'));return;
  }
  summary.textContent=`板上器件模型来源 · ${report.models.length} 个冻结文件`;
  details.append(element('p','以下来源、版本、许可和说明是任务冻结的声明；内容摘要及名义几何回读不认证厂家身份、材料公差、可购性或真实配合。工程包络仍只用于评估。','field-note'));
  const footprints=Array.isArray(report.geometry?.footprints)?report.geometry.footprints:[];
  const list=element('div','','source-list');details.append(list);
  for(const model of report.models){
    const card=element('section','','source-record');list.append(card);
    const footprint=footprints.find((f:any)=>f.id===model?.footprintId&&f.sha256===model?.expectedFootprintHash);
    const reference=footprint?declared(footprint.reference):'位号依据未取得';
    card.append(element('h3',reference+' · 冻结模型'));
    const source=model?.source;
    for(const [label,value] of [['来源',source?.uri],['版本',source?.version],['许可声明',source?.license],['原来源说明',source?.note]])card.append(element('p',label+'：'+declared(value),'field-note'));
    const digest=element('code','SHA-256：'+declared(source?.sha256),'library-digest');card.append(digest);
  }
  const artifact=context.artifacts.find(a=>a.jobId===job.id&&a.kind==='pcb_geometry_report'&&a.revisionId===dependency.revisionId);
  if(artifact){const a=element('a','下载本次几何来源报告 ↗','text-button') as HTMLAnchorElement;a.href=fileUrl(artifact)+'?download=true';details.append(a);}
}
