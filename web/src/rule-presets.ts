import {api,type Context} from './api';
import {moduleFields} from './module-settings';
const el=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
const shown=(value:unknown)=>value==null?'待定':({readable:'易读优先',balanced:'平衡',fast:'快速生成',model:'按模型声明',check:'默认启用',blink:'闪灯',uart_echo:'串口回显',i2c_probe_uart:'I²C探测与串口输出'} as Record<string,string>)[String(value)]||String(value);
const names:Record<string,string>={schematic:'原理图',pcb:'PCB新铺铜',mechanical:'机械',firmware:'固件',manufacturing:'制造审核'};
export class RulePresetPanel{
 constructor(private host:HTMLElement,private context:()=>Context|undefined,private toast:(s:string,error?:boolean)=>void,private changed:()=>Promise<void>){}
 async reload(){
  try{
   const read=await api<any>('settings/rule-presets');this.host.replaceChildren(el('h3','常用规则预设（名称 · 应用次数）'),el('p','按本机工程中登记的实际应用次数排序；仅影响后续任务。已有PCB规则仍须单独修改并检查。'));
   const select=el('select');select.setAttribute('aria-label','规则预设');select.add(new Option('请选择预设',''));
   for(const row of read.presets)select.add(new Option(`${row.name} · ${row.usageCount}`,row.id));
   const preview=el('button','预览应用到当前工程'),body=el('div');preview.type='button';preview.className='button secondary';
   select.onchange=()=>{const row=read.presets.find((r:any)=>r.id===select.value);body.replaceChildren(...(row?[el('p',row.description)]:[]));};
   preview.onclick=async()=>{preview.disabled=true;try{
    const selected=this.context();if(!selected)throw Error('请先选择工程。');
    const preset=read.presets.find((r:any)=>r.id===select.value);if(!preset)throw Error('请选择预设。');
    const c=await api<Context>(`projects/${selected.project.id}?compact=true`),effective=await api<any>(`projects/${c.project.id}/module-settings`);
    if(select.value!==preset.id)return;
    body.replaceChildren(el('h4',c.project.name),el('p',preset.description));
    for(const [module,fields] of Object.entries(preset.modules)){
     body.append(el('h4',names[module]));
     for(const [key,value] of Object.entries(fields as Record<string,unknown>)){
      const label=moduleFields[module]?.find(f=>f.key===key)?.label||key;
      body.append(el('p',`${label}：${shown(effective.modules[module].values[key])} → ${shown(value)}`));
     }
    }
    const apply=el('button','应用预设到当前工程');apply.type='button';apply.className='button primary';
    apply.onclick=async()=>{apply.disabled=true;try{
     if(select.value!==preset.id)throw Error('预设已切换，请重新预览。');
     if(this.context()?.project.id!==c.project.id)throw Error('工程已切换，请重新预览。');
     await api(`projects/${c.project.id}/rule-presets/apply`,'POST',{presetId:preset.id,expectedProjectVersion:c.project.version});
     this.toast('预设已应用，后续任务使用新默认值；历史版本保持。');await this.changed();
    }catch(err){this.toast((err as Error).message,true);apply.disabled=false;}};body.append(apply);
   }catch(err){this.toast((err as Error).message,true);}finally{preview.disabled=false;}};
   const details=el('details');details.append(el('summary','将当前工程覆盖保存为新预设'));
   const name=el('input');name.maxLength=120;name.placeholder='预设名称';name.setAttribute('aria-label','预设名称');
   const description=el('input');description.maxLength=2000;description.placeholder='适用场景与限制';description.setAttribute('aria-label','预设说明');
   const save=el('button','保存工程覆盖为预设');save.type='button';save.className='button secondary';
   save.onclick=async()=>{save.disabled=true;try{
    const selected=this.context();if(!selected)throw Error('请先选择工程。');if(!name.value.trim())throw Error('请填写预设名称。');
    const c=await api<Context>(`projects/${selected.project.id}?compact=true`),fresh=await api<any>('settings/rule-presets');
    const modules=c.project.preferences.moduleDefaults||{};if(!Object.keys(modules).length)throw Error('当前工程尚未设置模块覆盖。');
    await api('settings/rule-presets','POST',{expectedSettingsVersion:fresh.settingsVersion,name:name.value.trim(),description:description.value,modules});
    this.toast('规则预设已保存。');await this.changed();
   }catch(err){this.toast((err as Error).message,true);}finally{save.disabled=false;}};
   details.append(name,description,save);this.host.append(select,preview,body,details);
  }catch(err){this.host.replaceChildren(el('p','规则预设暂不可读：'+(err as Error).message));}
 }
}
