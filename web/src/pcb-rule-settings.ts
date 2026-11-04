import {api,requestId,type Context} from './api';
export type NumericRules=Record<string,number>;
export const initialRules:NumericRules={clearance:.127,trackWidth:.127,viaDiameter:.45,viaDrill:.3,copperEdge:.3,holeClearance:.25};
const fields:[string,string,number,number][]=[['clearance','不同网络铜间最小净距',.1,5],['trackWidth','最小线宽',.1,5],['viaDiameter','过孔外径',.3,5],['viaDrill','过孔孔径',.15,3],['copperEdge','铜到板边最小距离',.1,5],['holeClearance','孔间最小净距',.1,5]];
const el=(tag:string,text='')=>{const e=document.createElement(tag);e.textContent=text;return e;};
export function numericEditor(values:NumericRules){
 const root=el('div');root.className='settings-form-grid';const inputs=new Map<string,HTMLInputElement>();
 const unit=el('select') as HTMLSelectElement;for(const text of ['mm','mil'])unit.add(new Option(text,text));
 const units=el('label','输入单位');units.append(unit);root.append(units);let previous=1;
 for(const [key,label,min,max] of fields){const row=el('label',label),input=el('input') as HTMLInputElement;input.type='number';input.step='any';input.required=true;input.min=String(min);input.max=String(max);input.value=String(values[key]??initialRules[key]);input.dataset.rule=key;row.append(input);root.append(row);inputs.set(key,input);}
 unit.onchange=()=>{const factor=unit.value==='mil'?.0254:1;for(const [key,,min,max] of fields){const i=inputs.get(key)!;i.value=String(Number((Number(i.value)*previous/factor).toFixed(9)));i.min=String(min/factor);i.max=String(max/factor);}previous=factor;};
 const read=(validate=true)=>{const result={...values};for(const [key,i] of inputs){if(validate?!i.reportValidity():!i.checkValidity())throw Error('请检查规则数值。');result[key]=Number((Number(i.value)*previous).toFixed(9));}if(result.viaDiameter-result.viaDrill<.15-1e-9)throw Error('过孔须保留至少0.075mm单边铜环。');return result;};
 return {root,read};
}
export function projectRules(host:HTMLElement,getContext:()=>Context|undefined,defaults:NumericRules,toast:(s:string,error?:boolean)=>void){
 const load=el('button','读取当前工程规则') as HTMLButtonElement;load.type='button';load.className='button secondary';const body=el('div');host.append(load,body);
 load.onclick=async()=>{load.disabled=true;try{
  const selected=getContext();if(!selected)throw Error('请先选择工程。');const c=await api<Context>(`projects/${selected.project.id}?compact=true`),p=c.project;
  const revision=c.revisions.find(r=>r.id===p.headRevisionId);const hasBoard=!!revision?.entrypoints?.board;
  const board=hasBoard?await api<any>(`projects/${p.id}/pcb?limit=1`):null;
  if(board&&!board.rules)throw Error('此导入板尚无受支持的工程规则，请先完成原生工程交接。');
  const baseline=board?.rules??{...initialRules,...p.preferences.personalDefaultsSnapshot?.pcbDefaults?.numericRules,...p.preferences.pcbRules};
  body.replaceChildren(el('h3',p.name),el('p',board?'当前PCB版本规则；修改后检查并生成新版本。':p.preferences.pcbRules?'工程覆盖值':'创建工程时保存的个人默认值；不会随全局设置变化。'));
  let editor=numericEditor(baseline);const holder=el('div');holder.append(editor.root);
  const use=el('button','填入当前个人默认值') as HTMLButtonElement;use.type='button';use.className='button secondary';use.onclick=()=>{editor=numericEditor({...baseline,...defaults});holder.replaceChildren(editor.root);holder.dispatchEvent(new Event("input"));};
  const summary=el('p'),save=el('button','应用到当前工程') as HTMLButtonElement;save.type='button';save.className='button primary';
  holder.oninput=()=>{try{const values=editor.read(false);summary.textContent=fields.filter(([k])=>values[k]!==baseline[k]).map(([k,label])=>`${label}：${baseline[k]} → ${values[k]} mm`).join('；');}catch{}};
  save.onclick=async()=>{save.disabled=true;load.disabled=true;use.disabled=true;try{
   const rules=editor.read();
   if(board){let j=await api<any>(`projects/${p.id}/pcb-edits`,'POST',{revisionId:p.headRevisionId,expectedProjectVersion:p.version,expectedBoardHash:board.boardSha256,operations:[{kind:'set_board_rules',rules,reason:'用户在设置中明确应用PCB数值规则'}],message:'更新工程PCB数值规则',include3d:false,timeoutSeconds:300,requestId:requestId()});summary.textContent='正在运行原生检查…';while(['queued','running'].includes(j.status)){await new Promise(r=>setTimeout(r,1000));j=await api<any>(`jobs/${j.id}`);}if(j.status!=='completed'||!j.result?.adopted)throw Error(j.error||'规则候选未采纳，旧版保持；请在任务记录查看检查结果。');summary.textContent='规则已采纳到新版本。'+(j.result.checksPassed?'原生检查通过。':'整板仍有待处理检查项，请查看任务报告。');}
   else{await api(`projects/${p.id}`,'PUT',{expectedVersion:p.version,specification:p.specification,preferences:{...p.preferences,pcbRules:rules}});summary.textContent='工程规则已保存，新建PCB时生效。';}
   toast(summary.textContent!);
  }catch(e){toast((e as Error).message,true);summary.textContent=(e as Error).message+' 请重新读取工程规则后重试。';}finally{load.disabled=false;}};
  body.append(el('p','这里设置通用下限；网络类别、局部铜区等更严格的规则仍然有效。'),use,holder,summary,save);
 }catch(e){toast((e as Error).message,true);}finally{load.disabled=false;}};
}
