import {api,requestId} from './api';
const el=(tag:string,text='')=>{const e=document.createElement(tag);e.textContent=text;return e;};
export function devicePanel(host:HTMLElement,project:string,history:any,current:()=>boolean,targets:Record<string,any>){
  const box=el('section');box.className='source-record';
  box.append(el('h3','设备识别与烧录'),el('p','识别会连接设备，可能暂停或复位芯片。仅在明确允许操作实物时使用。'));
  const target=document.createElement('select');target.setAttribute('aria-label','设备目标芯片');
  for(const value of Object.keys(targets)){const o=document.createElement('option');o.value=value;o.textContent=value;target.append(o);}
  const selector=document.createElement('select');selector.setAttribute('aria-label','选择设备');
  const plans=document.createElement('select');plans.setAttribute('aria-label','选择烧录计划');
  const empty=document.createElement('option');empty.value='';empty.textContent='选择检查通过的计划';plans.append(empty);
  for(const p of history.plans.filter((p:any)=>p.checksPassed)){const b=history.bundles.find((b:any)=>b.id===p.bundleId);const o=document.createElement('option');o.value=p.id;o.textContent=`${b?.name} · ${p.id}`;plans.append(o);}
  const consent=document.createElement('input');consent.type='checkbox';const label=el('label','允许本次操作连接实物');label.prepend(consent);
  const reset=document.createElement('input');reset.type='checkbox';const resetLabel=el('label','烧录校验成功后复位');resetLabel.prepend(reset);
  const info=el('p');info.className='field-note';info.setAttribute('role','status');
  const details=document.createElement('details');details.append(el('summary','设备检查详情'));
  const report=el('pre');report.dataset.deviceReport='';details.append(report);
  let identified:any=null,active:string|null=null;
  const clear=()=>{identified=null;info.textContent='设备选择已变化，请重新识别。';};target.onchange=()=>{selector.replaceChildren();clear();};selector.onchange=clear;
  const controls:HTMLButtonElement[]=[];
  const button=(title:string,action:string)=>{
    const b=document.createElement('button');b.type='button';b.className='button secondary';b.textContent=title;controls.push(b);
    b.onclick=async()=>{
      if(!consent.checked){info.textContent='请先明确允许连接实物。';return;}
      const plan=history.plans.find((p:any)=>p.id===plans.value);
      if(action!=='discover'&&!selector.value){info.textContent='请先枚举并选择设备。';return;}
      if(['program','verify'].includes(action)&&(!plan||!identified)){info.textContent='请先选择计划并识别设备。';return;}
      controls.forEach(b=>b.disabled=true);target.disabled=selector.disabled=plans.disabled=reset.disabled=true;
      try{
        const job=await api(`projects/${project}/flash-device-jobs`,'POST',{action,target:target.value,backend:targets[target.value].backend,deviceAccessConfirmed:true,selector:action==='discover'?null:selector.value,
          ...(['program','verify'].includes(action)?{planId:plan.id,expectedPlanSha256:plan.planSha256,identificationJobId:identified.id,expectedIdentitySha256:identified.result.identitySha256}:{}),resetAfter:action==='program'&&reset.checked,requestId:requestId()});
        active=job.id;cancel.disabled=false;info.textContent=job.phase;
        while(current()){
          const value=await api(`jobs/${job.id}`);if(!current())return;
          report.textContent=JSON.stringify({jobId:value.id,status:value.status,phase:value.phase,error:value.error,result:value.result},null,2);
          info.textContent=value.status==='completed'?(action==='discover'?'设备列表已更新':action==='identify'?'设备身份已核对':action==='verify'?'回读校验通过':'烧录与回读校验完成'):
            value.status==='failed'?(value.error||'设备任务失败'):value.status==='cancelled'?'设备任务已取消，请查看记录确认写入状态':value.phase;
          if(!['queued','running'].includes(value.status)){
            if(value.status==='completed'){
              if(action==='discover'){selector.replaceChildren();identified=null;for(const d of value.result.devices){const o=document.createElement('option');o.value=d.selector;o.textContent=`${d.selector} · ${d.description}`;selector.append(o);}}
              if(action==='identify')identified=value;
            }
            break;
          }
          await new Promise(resolve=>setTimeout(resolve,500));
        }
      }catch(error){if(current())info.textContent=(error as Error).message;}
      finally{active=null;cancel.disabled=true;consent.checked=false;controls.forEach(b=>b.disabled=false);target.disabled=selector.disabled=plans.disabled=reset.disabled=false;}
    };return b;
  };
  const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消设备任务';cancel.disabled=true;
  cancel.onclick=async()=>{if(active)try{await api(`jobs/${active}/cancel`,'POST');}catch(error){info.textContent=(error as Error).message;}};
  box.append(target,selector,plans,label,resetLabel,button('枚举设备','discover'),button('识别所选设备','identify'),button('烧录并校验','program'),button('仅回读校验','verify'),cancel,info,details);
  for(const job of history.deviceJobs||[]){const d=document.createElement('details');d.append(el('summary',`${job.id} · ${job.status}`));const data=el('pre',JSON.stringify({phase:job.phase,result:job.result,error:job.error},null,2));d.append(data);
    if(['queued','running'].includes(job.status)){const stop=document.createElement('button');stop.textContent='取消设备任务';stop.onclick=async()=>{try{await api(`jobs/${job.id}/cancel`,'POST');stop.disabled=true;}catch(error){info.textContent=(error as Error).message;}};d.append(stop);}
    box.append(d);
  }host.append(box);
}
