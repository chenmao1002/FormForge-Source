import {api, type Project} from './api';
import {locale} from './i18n';

const node=<K extends keyof HTMLElementTagNameMap>(tag:K,cls='',text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
const size=(value:number)=>{const u=['B','KiB','MiB','GiB'];let i=0;while(value>=1024&&i<u.length-1){value/=1024;i++;}return `${value.toLocaleString(locale(),{maximumFractionDigits:2})} ${u[i]}`;};

export function confirmMaintenance(title:string,description:string,label:string,name?:string):Promise<boolean>{
  return new Promise(resolve=>{
    const dialog=node('dialog','maintenance-dialog'),form=node('form');form.method='dialog';
    const heading=node('h2','',title);heading.id='maintenance-confirm-'+crypto.randomUUID();dialog.setAttribute('aria-labelledby',heading.id);
    form.append(heading,node('p','field-note',description));
    const actions=node('div','maintenance-actions'),cancel=node('button','button secondary','取消'),confirm=node('button','button maintenance-danger',label);
    cancel.type='button';confirm.type='submit';cancel.onclick=()=>dialog.close('cancel');
    if(name!==undefined)form.append(node('p','field-note',`工程：${name}`));
    form.onsubmit=e=>{e.preventDefault();dialog.close('confirm');};
    actions.append(cancel,confirm);form.append(actions);dialog.append(form);document.body.append(dialog);
    const focused=document.activeElement as HTMLElement|null;
    dialog.addEventListener('close',()=>{const approved=dialog.returnValue==='confirm';dialog.remove();focused?.focus();resolve(approved);},{once:true});
    dialog.showModal();cancel.focus();
  });
}

interface CleanupItem {id:string;category:string;categoryName:string;path:string;bytes:number;files:number}
interface CleanupResult {bytes:number;files:number;completed:number;total:number;errors:{path:string;reason:string}[]}
interface CleanupState {scanId?:string;operation?:'trash';status:string;items:CleanupItem[];skipped:{path:string;reason:string}[];inspected?:number;message?:string;finishedAt?:string;result:CleanupResult|null}
interface TrashedProject extends Project {deletedAt:string;purgeStartedAt?:string}

export class MaintenancePanel {
  private active=false;
  private timer:number|undefined;
  private pending=false;
  private polling=false;
  private generation=0;
  private state:CleanupState={status:'idle',items:[],skipped:[],result:null};
  private selected=new Set<string>();
  private groupOpen=new Set<string>();
  private status=node('p','field-note','扫描后查看可清理目录与预计大小。');
  private groups=node('div','maintenance-groups');
  private scan=node('button','button secondary','扫描可清理内容');
  private clean=node('button','button maintenance-danger','清理所选内容');
  private trash=node('div');
  private trashed:TrashedProject[]=[];
  private emptyTrash=node('button','button maintenance-danger','清空回收站');
  private trashStatus=node('p','field-note');
  private history=node('div');

  constructor(host:HTMLElement,private toast:(message:string,error?:boolean)=>void,private changed:()=>Promise<unknown>){
    this.scan.id='cleanup-scan';this.clean.id='cleanup-run';this.clean.disabled=true;
    this.emptyTrash.id='project-trash-empty';this.emptyTrash.disabled=true;this.emptyTrash.type='button';
    this.trashStatus.setAttribute('role','status');
    this.status.setAttribute('role','status');
    const controls=node('div','maintenance-actions');controls.append(this.scan,this.clean);
    for(const [id,title,description,content] of [
      ['settings-cleanup','缓存与临时文件','扫描下载缓存、测试临时副本、原生读取缓存，以及依赖核对通过的工程工作副本。仅处理超过 1 小时未变化的目录；版本源、登记产物、报告、失败记录和登录资料保留。',[controls,this.status,this.groups]],
      ['settings-project-trash','工程回收站','删除工程先移入这里，仍占用空间。可恢复，或彻底删除本地工程文件以释放空间。任务时钟与订单元数据保留；供应商订单不会被撤销。',[this.trashStatus,this.trash]],
      ['settings-cleanup-history','最近清理记录','显示实际删除的文件大小；被占用、变化或无法删除的目录会单独列出。',[this.history]],
    ] as const){
      const section=node('section','card settings-section');section.id=id;
      const heading=node('div','card-heading');heading.append(node('h2','',title));
      if(id==='settings-project-trash')heading.append(this.emptyTrash);
      section.append(heading,node('p','settings-description',description),...content);
      if(id==='settings-cleanup')section.append(node('p','field-note','清理预览和删除量显示文件总大小；压缩后的磁盘占用见上方存储概览。'));
      host.append(section);
    }
    this.scan.onclick=()=>void this.run(async()=>{this.selected.clear();this.state=await api<CleanupState>('settings/cleanup/scan','POST');this.paint();this.schedule();});
    this.clean.onclick=()=>void this.run(async()=>{
      const items=this.state.items.filter(i=>this.selected.has(i.category));
      if(!items.length)return;
      const bytes=items.reduce((n,i)=>n+i.bytes,0);
      if(!await confirmMaintenance('清理所选缓存',`将清理 ${items.length} 个目录，预计 ${size(bytes)}。目录若在扫描后变化或被占用，会跳过并显示原因。`,'确认清理'))return;
      this.state=await api<CleanupState>('settings/cleanup/run','POST',{scanId:this.state.scanId,itemIds:items.map(i=>i.id)});
      this.paint();this.schedule();
    });
    this.emptyTrash.onclick=()=>void this.run(async()=>{
      const projects=this.trashed.map(p=>({projectId:p.id,expectedVersion:p.version,confirmName:p.name}));
      if(!projects.length)return;
      if(!await confirmMaintenance('清空回收站',`将永久删除当前列表中 ${projects.length} 个工程的本地设计、资料和任务产物，无法恢复。任务时钟、失败记录和订单元数据保留。确认后在后台执行，可离开本页；新加入回收站的工程不包含在本次操作中。`,'确认清空回收站'))return;
      this.state=await api<CleanupState>('settings/project-trash/empty','POST',{projects});
      this.paint();this.schedule();
    });
  }

  activate(){if(this.active)return;this.active=true;void this.reload();}
  deactivate(){this.active=false;clearTimeout(this.timer);this.timer=undefined;}

  private busy(){return this.pending||['scanning','cleaning'].includes(this.state.status);}
  private buttons(){
    this.scan.disabled=this.busy();
    const selected=this.state.items.filter(i=>this.selected.has(i.category));
    this.clean.disabled=this.busy()||this.state.status!=='ready'||!selected.length;
    this.emptyTrash.disabled=this.busy()||!this.trashed.length;
    for(const button of this.trash.querySelectorAll<HTMLButtonElement>('button'))button.disabled=this.busy()||button.dataset.unavailable==='true';
    this.clean.textContent=selected.length?`清理所选内容（${size(selected.reduce((n,i)=>n+i.bytes,0))}）`:'清理所选内容';
  }

  private async run(action:()=>Promise<void>){
    if(this.pending)return;this.pending=true;this.buttons();
    try{await action();}catch(error){this.toast((error as Error).message,true);}
    finally{this.pending=false;this.buttons();this.schedule();}
  }

  async reload(){
    const generation=++this.generation;
    const results=await Promise.allSettled([
      api<CleanupState>('settings/cleanup'),api<TrashedProject[]>('settings/project-trash'),api<any[]>('settings/cleanup/history'),
    ]);
    if(generation!==this.generation)return;
    const [state,trash,history]=results;
    if(state.status==='fulfilled'){this.state=state.value;this.paint();}else this.status.textContent='清理状态暂不可读，请刷新重试。';
    if(trash.status==='fulfilled')this.paintTrash(trash.value);else this.trash.textContent='回收站暂不可读，请刷新重试。';
    if(history.status==='fulfilled')this.paintHistory(history.value);else this.history.textContent='清理记录暂不可读，请刷新重试。';
    this.schedule();
  }

  private schedule(){
    if(this.active&&['scanning','cleaning'].includes(this.state.status)&&this.timer===undefined&&!this.polling)
      this.timer=window.setTimeout(()=>{this.timer=undefined;void this.poll();},1000);
  }

  private async poll(){
    if(this.polling)return;this.polling=true;const generation=this.generation;
    try{
      const state=await api<CleanupState>('settings/cleanup');
      if(generation!==this.generation)return;
      this.state=state;this.paint();
      if(!['scanning','cleaning'].includes(state.status)){await this.changed();await this.reload();}
    }catch(error){this.status.textContent=`进度读取失败，可点击“刷新信息”重试：${(error as Error).message}`;}
    finally{this.polling=false;this.schedule();}
  }

  private paint(){
    const s=this.state,total=s.items.reduce((n,i)=>n+i.bytes,0);
    const trashOperation=s.operation==='trash';
    this.trashStatus.textContent=trashOperation?(s.status==='cleaning'?`正在清空回收站：${s.result?.completed||0} / ${s.result?.total||0} 个工程，已实际删除 ${size(s.result?.bytes||0)}。可离开本页，后台将继续执行。`:`${s.status==='completed'?'回收站清理完成':s.status==='failed'?'回收站清理未开始':'回收站部分清理完成'}：已清除 ${s.result?.completed||0} 个工程，实际删除 ${size(s.result?.bytes||0)}；${s.result?.errors.length||0} 项未完成。`):'';
    if(trashOperation)this.status.textContent=s.status==='cleaning'?'回收站正在清理，完成后可扫描缓存。':'扫描后查看可清理目录与预计大小。';
    else if(s.status==='scanning')this.status.textContent=`正在扫描，已检查 ${s.inspected||0} 个目录，已找到 ${size(total)}。可继续使用其他页面。`;
    else if(s.status==='ready')this.status.textContent=`可清理 ${size(total)}，共 ${s.items.length} 个目录。请选择类别，展开后可查看完整目录。`;
    else if(s.status==='cleaning')this.status.textContent=`正在清理：${s.result?.completed||0} / ${s.result?.total||0} 个目录，已实际删除 ${size(s.result?.bytes||0)}。`;
    else if(s.result)this.status.textContent=`${s.status==='completed'?'清理完成':'部分清理完成'}：实际删除 ${size(s.result.bytes)}、${s.result.files} 个文件；${s.result.errors.length} 项未完成。重新扫描可更新占用。`;
    else if(s.status==='failed')this.status.textContent=s.message||'扫描未完成，请重试。';
    else this.status.textContent='扫描后查看可清理目录与预计大小。';
    this.groups.replaceChildren();
    for(const category of ['downloads','reads','tests','workspaces']){
      const items=s.items.filter(i=>i.category===category);if(!items.length)continue;
      const group=node('div','maintenance-group'),label=node('label','maintenance-choice'),check=node('input');
      check.type='checkbox';check.checked=this.selected.has(category);check.disabled=s.status!=='ready';check.dataset.cleanupCategory=category;
      check.onchange=()=>{if(check.checked)this.selected.add(category);else this.selected.delete(category);this.buttons();};
      label.append(check,node('strong','',items[0].categoryName),node('span','',`${size(items.reduce((n,i)=>n+i.bytes,0))} · ${items.length} 个目录`));
      if(category==='workspaces')group.append(node('p','field-note','仅包含已完成 KiCad 检查的可再生成输入副本。已核对冻结源完整性、内容一致性和工程记录引用；清理前会再次核对依赖。'));
      const details=node('details');details.open=this.groupOpen.has(category);details.append(node('summary','','查看目录'));
      details.ontoggle=()=>{if(details.open)this.groupOpen.add(category);else this.groupOpen.delete(category);};
      details.append(node('pre','maintenance-paths',items.map(i=>`${size(i.bytes).padStart(12)}  ${i.path}`).join('\n')));group.append(label,details);this.groups.append(group);
    }
    const errors=s.result?.errors||s.skipped;
    if(errors.length){const detail=node('details','maintenance-errors');detail.append(node('summary','',`${errors.length} 项未清理或未纳入`),node('pre','maintenance-paths',errors.map(i=>`${i.path}\n${i.reason}`).join('\n\n')));this.groups.append(detail);}
    this.buttons();
  }

  private paintTrash(projects:TrashedProject[]){
    this.trashed=projects;
    this.trash.replaceChildren();
    if(!projects.length){this.trash.append(node('p','field-note','回收站为空。'));this.buttons();return;}
    for(const project of projects){
      const row=node('div','maintenance-trash-row'),body=node('div'),actions=node('div','maintenance-actions');
      const projectName=node('strong','',project.name);projectName.translate=false;
      body.append(projectName,node('p','field-note',`删除于 ${new Date(project.deletedAt).toLocaleString(locale())}${project.purgeStartedAt?' · 文件清除已开始，可重试清除剩余文件':''}`));
      const restore=node('button','button secondary','恢复工程'),purge=node('button','button maintenance-danger','彻底删除文件');
      restore.disabled=!!project.purgeStartedAt;
      restore.dataset.unavailable=String(!!project.purgeStartedAt);
      restore.onclick=()=>void this.run(async()=>{
        restore.disabled=true;
        try{await api(`projects/${project.id}/restore`,'POST',{expectedVersion:project.version,confirmName:project.name});this.toast('工程已恢复。');await this.changed();}
        finally{await this.reload();}
      });
      purge.onclick=()=>void this.run(async()=>{
        if(!await confirmMaintenance('彻底删除工程文件','将永久删除此工程的本地设计、资料和任务产物，无法恢复。任务时钟和订单元数据保留，供应商订单不会撤销。','彻底删除文件',project.name))return;
        purge.disabled=true;
        try{const result=await api<{bytes:number}>(`projects/${project.id}/purge`,'POST',{expectedVersion:project.version,confirmName:project.name});this.toast(`工程文件已清除，实际删除 ${size(result.bytes)}。`);await this.changed();}
        finally{await this.reload();}
      });
      actions.append(restore,purge);row.append(body,actions);this.trash.append(row);
    }
    this.buttons();
  }

  private paintHistory(rows:any[]){
    this.history.replaceChildren();
    if(!rows.length){this.history.append(node('p','field-note','暂无清理记录。'));return;}
    for(const record of rows){
      const detail=node('details','maintenance-history'),r=record.result;
      const incomplete=['cleaning','interrupted'].includes(record.status),label=record.status==='completed'?'完成':record.status==='cleaning'?'执行中':record.status==='interrupted'?'上次清理未结束':'部分完成';
      detail.append(node('summary','',`${new Date(record.finishedAt||record.startedAt).toLocaleString(locale())} · ${label} · ${incomplete?'至少 ':''}${size(r?.bytes||0)}`));
      detail.append(node('p','field-note',record.operation==='trash'?'清空回收站':'清理缓存'));
      detail.append(node('p','field-note',`${incomplete?'已记录删除':'实际删除'} ${r?.files||0} 个文件，${r?.errors?.length||0} 项未完成。${incomplete?'清理记录未结束，重新扫描可核对当前目录。':''}`));
      if(r?.errors?.length)detail.append(node('pre','maintenance-paths',r.errors.map((e:any)=>`${e.path}\n${e.reason}`).join('\n\n')));
      this.history.append(detail);
    }
  }
}
