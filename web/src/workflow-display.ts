import {locale} from './i18n';

export interface JobClock {status:string;createdAt:string;startedAt?:string;finishedAt?:string}
export function geometryStatus(result:any){
  return !result?.checksPassed?'几何需处理':result.coverage?.complete?'完整PCBA已核对':result.coverage?.selectedComponents===0?'仅板体已核对':'部分器件几何已核对';
}
export function applyJobPhase(jobs:{id:string;status:string;phase:string;cancelRequested:boolean}[],event:{type:string;jobId?:string;payload:any}){
  if(event.type!=='job.phase')return false;
  const job=jobs.find(j=>j.id===event.jobId);
  if(!job)return false;
  if(job.status==='queued'||job.status==='running'){
    if(typeof event.payload.phase==='string')job.phase=event.payload.phase;
    if(typeof event.payload.cancelRequested==='boolean')job.cancelRequested=event.payload.cancelRequested;
  }
  return true;
}
const seconds=(a:string,b:string)=>{const delta=(Date.parse(b)-Date.parse(a))/1000;return Number.isFinite(delta)&&delta>=0?delta:null;};
export function jobTiming(job:JobClock,now=Date.now()){
  const active=job.status==='queued'||job.status==='running';
  const end=job.finishedAt||(active?new Date(now).toISOString():undefined);
  return {active,queueSeconds:job.startedAt?seconds(job.createdAt,job.startedAt):job.status==='queued'&&end?seconds(job.createdAt,end):null,
    runSeconds:job.startedAt&&end?seconds(job.startedAt,end):null,finishedAt:job.finishedAt};
}
function duration(value:number){const n=Math.floor(value);return n<60?`${n} 秒`:n<3600?`${Math.floor(n/60)} 分 ${n%60} 秒`:`${Math.floor(n/3600)} 小时 ${Math.floor(n%3600/60)} 分`;}
export function jobTimingLabel(job:JobClock,now=Date.now()){
  const result=jobTiming(job,now),parts=[];
  if(result.queueSeconds!==null)parts.push('排队 '+duration(result.queueSeconds));
  if(result.runSeconds!==null)parts.push((result.active?'已运行 ':'运行 ')+duration(result.runSeconds));
  if(result.finishedAt)parts.push('结束 '+new Date(result.finishedAt).toLocaleTimeString(locale(),{hour12:false}));
  else if(!result.active)parts.push('结束时间未记录');
  return parts.join(' · ');
}

export function loadPreviewImage(url:string,createImage:()=>HTMLImageElement=()=>new Image()):Promise<void>{
  return new Promise((resolve,reject)=>{
    const image=createImage();
    image.onload=()=>{image.onload=null;image.onerror=null;resolve();};
    image.onerror=()=>{image.onload=null;image.onerror=null;reject(new Error('图纸读取失败，请重试或查看任务报告。'));};
    image.src=url;
  });
}
