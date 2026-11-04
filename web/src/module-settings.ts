import {api,requestId,type Context} from './api';

type Value=string|number|null;
export type ModuleValues=Record<string,Value>;
export type ModuleDefaults=Record<string,ModuleValues>;
type Field={key:string;label:string;initial:Value;min?:number;max?:number;options?:string[];color?:boolean;optional?:boolean;text?:boolean;step?:string};
export const moduleFields:Record<string,Field[]>={
 pcb:[{key:'clearance',label:'新铺铜净距（mm）',initial:.2,min:.1,max:5,step:'any'},{key:'minimumThickness',label:'新铺铜最小厚度（mm）',initial:.2,min:.1,max:2,step:'any'},{key:'thermalGap',label:'热焊间隙（mm）',initial:.2,min:.1,max:5,step:'any'},{key:'thermalSpoke',label:'热焊辐条宽度（mm）',initial:.2,min:.1,max:5,step:'any'}],
 schematic:[{key:'paper',label:'默认图纸大小',initial:'A3',options:['A4','A3','A2','A1','A0']},{key:'maxSymbolsPerSheet',label:'每页器件上限',initial:40,min:1,max:500},{key:'layoutStrategy',label:'排版策略',initial:'readable',options:['readable','balanced','fast']}],
 mechanical:[{key:'interferencePolicy',label:'新特征模型干涉检查',initial:'model',options:['model','check']},{key:'screwColor',label:'螺钉默认颜色（可沿用模型）',initial:null,color:true,optional:true},{key:'timeoutSeconds',label:'建模任务时限（秒）',initial:300,min:30,max:10800}],
 firmware:[{key:'template',label:'默认固件模板',initial:'blink',options:['blink','uart_echo','i2c_probe_uart']},{key:'periodMs',label:'闪灯模板周期（毫秒）',initial:1000,min:2,max:60000},{key:'baud',label:'串口模板波特率',initial:115200,min:1200,max:1000000},{key:'timeoutSeconds',label:'构建任务时限（秒）',initial:180,min:10,max:600}],
 manufacturing:[{key:'material',label:'审核资料默认板材（留空待定）',initial:null,text:true,optional:true},{key:'thicknessMm',label:'审核资料默认板厚（mm）',initial:null,min:.1,max:10,step:'any',optional:true},{key:'finish',label:'审核资料默认表面处理',initial:null,text:true,optional:true},{key:'solderMask',label:'审核资料默认阻焊颜色',initial:null,text:true,optional:true},{key:'boardQuantity',label:'资料包计划数量（留空待定）',initial:null,min:1,max:1000000,optional:true},{key:'timeoutSeconds',label:'资料包任务时限（秒）',initial:180,min:30,max:600}],
};
const notes:Record<string,string>={
 pcb:'仅用于新铺铜操作；已有铜区保持。工程通用最小规则仍约束这些值，原生填充及DRC仍检查。',
 schematic:'纸张用于新建与自动排版；每页上限和策略在自动排版任务生效。易读优先增加间距与就近短线；平衡适度连线；快速生成使用标签。功能组超限须先拆组。',
 mechanical:'默认干涉检查只用于新特征模型；模型明确参数优先，工程计划要求的检查仍执行。螺钉颜色用于特征模型与总成。',
 firmware:'省略模板时使用已保存的默认模板；实际引脚绑定仍须匹配硬件。周期和波特率仅用于相应模板，编辑源码不替换模板参数。构建不执行烧录。',
 manufacturing:'工艺与数量仅进入制造审核资料包；板厚与真实PCB冲突会报告问题，不修改PCB。供应商下单参数仍另行核对。',
};
const el=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
function editor(module:string,values:ModuleValues){
 const root=el('div');root.className='settings-form-grid';const controls=new Map<string,HTMLInputElement|HTMLSelectElement>();
 for(const field of moduleFields[module]){
  const row=el(field.color?'div':'label',field.label);row.className='settings-control';let input:HTMLInputElement|HTMLSelectElement;
  if(field.options){input=el('select');for(const value of field.options)input.add(new Option(({readable:'易读优先',balanced:'平衡',fast:'快速生成',model:'按模型声明',check:'默认启用',blink:'闪灯',uart_echo:'串口回显',i2c_probe_uart:'I²C探测与串口输出'} as Record<string,string>)[value]||value,value));}
  else{input=el('input');input.type=field.color?'color':field.text?'text':'number';if(field.color){const enabled=el('input');enabled.type='checkbox';enabled.checked=values[field.key]!=null;enabled.dataset.settingEnabled=field.key;enabled.onchange=()=>{input.disabled=!enabled.checked;};input.disabled=!enabled.checked;const toggle=el('label','自定义颜色');toggle.className='settings-color-toggle';toggle.prepend(enabled);row.append(toggle);}else{input.min=String(field.min);input.max=String(field.max);input.step=field.step||'1';if(field.text)input.maxLength=100;}input.required=!field.optional;}
  input.setAttribute('aria-label',field.label);input.value=String(values[field.key]??(field.color?'#8c8c8c':''));input.dataset.setting=field.key;controls.set(field.key,input);row.append(input);root.append(row);
 }
 return {root,read:()=>{const result:ModuleValues={...values};for(const field of moduleFields[module]){const input=controls.get(field.key)!;if(!input.reportValidity())throw Error('请检查设置值。');result[field.key]=input.value===''||input.disabled?null:field.color||field.options||field.text?input.value:Number(input.value);}return result;}};
}
export function renderModuleSettings(host:HTMLElement,module:string,personal:ModuleValues,getContext:()=>Context|undefined,savePersonal:(values:ModuleValues)=>Promise<void>,toast:(s:string,error?:boolean)=>void){
 host.replaceChildren();const form=el('form'),heading=el('h3','个人默认'),e=editor(module,personal);form.append(heading,el('p','仅复制到以后新建的工程；已有工程须单独应用。'),el('p',notes[module]),e.root);
 const save=el('button','保存此模块默认值');save.type='submit';save.className='button primary';
 form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{await savePersonal(e.read());}catch(err){toast((err as Error).message,true);}finally{save.disabled=false;}};form.append(save);
 const project=el('section'),load=el('button','读取工程覆盖');load.type='button';load.className='button secondary';const body=el('div');project.append(el('h3','当前工程覆盖'),load,body);
 load.onclick=async()=>{load.disabled=true;try{
  const selected=getContext();if(!selected)throw Error('请先选择工程。');const c=await api<Context>(`projects/${selected.project.id}?compact=true`),p=c.project;
  const effective=await api<any>(`projects/${p.id}/module-settings`);if(effective.projectVersion!==p.version)throw Error('工程已更新，请重新读取。');
  let current=editor(module,effective.modules[module].values);const holder=el('div');holder.append(current.root);
  const fill=el('button','填入个人默认'),inherit=el('button','恢复工程创建时默认'),apply=el('button','保存工程覆盖'),summary=el('p');for(const b of [fill,inherit,apply]){b.type='button';b.className='button secondary';}
  const sourceLabel=(value:string)=>value==='project-override'?'当前来源：工程覆盖':value==='system-default'?'当前来源：系统默认值':'当前来源：工程创建时默认值';
  const source=el('p',sourceLabel(effective.modules[module].source));
  let remove=false;
  fill.onclick=()=>{remove=false;current=editor(module,personal);holder.replaceChildren(current.root);summary.textContent='将应用当前个人默认值。';};
  inherit.onclick=()=>{remove=true;current=editor(module,effective.modules[module].inherited);holder.replaceChildren(current.root);summary.textContent='将移除此模块覆盖，恢复工程创建时默认值。';};
  holder.oninput=()=>{remove=false;summary.textContent='有未保存的工程设置。';};
  apply.onclick=async()=>{apply.disabled=true;load.disabled=true;try{
   const values=current.read(),overrides={...p.preferences.moduleDefaults};if(remove)delete overrides[module];else overrides[module]=values;
   await api(`projects/${p.id}`,'PUT',{expectedVersion:p.version,specification:p.specification,preferences:{...p.preferences,moduleDefaults:overrides}});
   source.textContent=sourceLabel(remove?effective.modules[module].inheritedSource:'project-override');
   summary.textContent='工程设置已保存，从后续新任务生效；历史版本不变。';toast(summary.textContent);fill.disabled=true;inherit.disabled=true;
  }catch(err){toast((err as Error).message,true);apply.disabled=false;}finally{load.disabled=false;}};
  body.replaceChildren(el('h4',p.name),source,fill,inherit,holder,summary,apply);
 }catch(err){toast((err as Error).message,true);}finally{load.disabled=false;}};
 if(module==='schematic'){
  const run=el('button','按工程设置自动分页与排版'),status=el('p');run.type='button';run.className='button primary';
  run.onclick=async()=>{run.disabled=true;try{
   const selected=getContext();if(!selected)throw Error('请先选择工程。');
   const c=await api<Context>(`projects/${selected.project.id}?compact=true`),p=c.project;
   if(!p.headRevisionId)throw Error('请先创建包含器件的原理图。');
   let job=await api<any>(`projects/${p.id}/schematic-edits`,'POST',{revisionId:p.headRevisionId,expectedProjectVersion:p.version,operations:[{kind:'auto_layout'}],message:'按工程设置自动分页与排版',timeoutSeconds:300,requestId:requestId()});
   status.textContent='正在排版并检查原生连接…';
   while(['queued','running'].includes(job.status)){await new Promise(r=>setTimeout(r,1000));job=await api<any>(`jobs/${job.id}`);}
   if(job.status!=='completed'||!job.result?.adopted)throw Error(job.error||'排版候选未采纳，请查看任务报告。');
   status.textContent='排版版本已采纳，完整连接检查通过；请查看原理图与任务报告。';toast(status.textContent);
  }catch(err){status.textContent=(err as Error).message;toast(status.textContent,true);}finally{run.disabled=false;}};
  project.append(el('p','使用已保存的工程规则生成新版本。当前支持单页、普通单单元符号；分组超限或不支持对象会明确拒绝。'),run,status);
 }
 host.append(form,project);
}
