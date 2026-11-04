import {type Context,type Revision} from './api';

const el=(id:string)=>document.getElementById(id)!;
export class MechanicalWorkspace {
  render(view:string,context:Context|null,revision?:Revision){
    const active=view==='mechanical';
    for(const id of ['mechanical-overview','mechanical-view-heading','mechanical-manufacturing'])el(id).hidden=!active;
    if(!active)return;
    const result=context?.jobs.find(j=>j.id===revision?.createdByJobId)?.result;
    el('mechanical-part-count').textContent=result?.parts?String(result.parts.length):'—';
    el('mechanical-solid-count').textContent=result?.parts?String(result.parts.reduce((sum:number,p:any)=>sum+p.solidCount,0)):'—';
    el('mechanical-process').textContent=result?(result.engineering?.material||'尚未规划工艺'):'—';
    const notes:string[]=[];
    const integrity=result?.measures?.partIntegrity;
    if(result){
      if(!integrity)notes.push('此历史版本未执行一体零件连通性验收。');
      else if(!integrity.checksPassed)notes.push('一体零件存在断开实体，未通过验收：'+integrity.failures.join(', '));
      else notes.push('一体零件连通性通过；来源总成独立保留。');
    }
    if(result?.measures?.explosionDirectionReview?.findings?.length)notes.push('爆炸方向与紧固接收件装入方向不一致，请查看工程报告。');
    el('mechanical-overview-note').textContent=notes.join(' ');
    el('mechanical-overview-note').hidden=!el('mechanical-overview-note').textContent;
    (el('mechanical-manufacturing') as HTMLButtonElement).disabled=!revision;
  }
}
