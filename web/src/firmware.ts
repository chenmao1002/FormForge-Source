import {api,fileUrl,type Context,type Revision} from './api';
import {FirmwareSourceView,memoryUsage} from './firmware-source';
import {FirmwareFlashPanel} from './firmware-flash';

const make=(tag:string,cls='',text='')=>{const element=document.createElement(tag);element.className=cls;element.textContent=text;return element;};
const roles:Record<string,string>={led:'状态灯',tx:'串口发送',rx:'串口接收',scl:'I²C 时钟',sda:'I²C 数据'};

export class FirmwarePanel {
  private flash=new FirmwareFlashPanel();
  private signature='';
  private generation=0;
  private files=new Map<string,string>();
  private tab='overview';
  constructor(private host:HTMLElement){}
  deactivate(){this.generation++;this.flash.deactivate();this.signature='';}

  async render(context:Context|null,revision?:Revision){
    const job=context?.jobs.find(j=>j.id===revision?.createdByJobId);
    const signature=JSON.stringify([context?.project.id,context?.project.version,revision?.id,revision?.validation,job?.status,job?.result,context?.artifacts.filter(a=>a.revisionId===revision?.id).map(a=>a.id)]);
    if(this.signature===signature)return;
    this.signature=signature;const generation=++this.generation;
    this.host.replaceChildren();
    this.flash.deactivate();
    if(!context||!revision){
      this.host.append(make('h2','','还没有固件版本'),make('p','field-note','由 Agent 绑定明确的原理图版本，提交行为需求和源码后，这里会同步显示构建过程、应用源码与检查结果。'),make('p','quiet-note','可独立从原理图开始，无需先制作 PCB。当前已支持 G030、G071、F103、F401、F411、F407 的已登记精确型号。'));
      if(context){const panel=make('section','firmware-tabpanel');this.host.append(panel);void this.flash.render(panel,context.project.id);}
      return;
    }
    this.host.append(make('p','field-note','正在读取此版本的源码与检查…'));
    let read:any;
    try{read=await api(`projects/${context.project.id}/firmware?revisionId=${encodeURIComponent(revision.id)}`);}
    catch(error){if(generation===this.generation){this.signature='';this.host.replaceChildren(make('p','connection-problem',(error as Error).message));}return;}
    if(generation!==this.generation)return;
    this.host.replaceChildren();
    const design=read.design,result=read.result;
    const target=design.targetProfile||{cpu:'cortex-m0plus',clockHz:16000000,flashSize:32768,ramSize:8192};
    const heading=make('div','card-heading firmware-hero'),identity=make('div','firmware-identity');
    identity.append(make('h2','',design.profile),make('span','small-tag',design.hardwareDependency.targetReference));
    heading.append(identity);this.host.append(heading);
    const tabs=make('nav','firmware-tabs');tabs.setAttribute('aria-label','固件工作区');tabs.setAttribute('role','tablist');
    const panels=new Map<string,HTMLElement>();
    for(const [id,title] of [['overview','项目概述'],['pins','引脚绑定'],['build','构建配置'],['downloads','资源与下载'],['flash','烧录与设备']]){
      const button=make('button','',title) as HTMLButtonElement;button.type='button';button.dataset.firmwareTab=id;button.id='firmware-tab-'+id;button.setAttribute('role','tab');button.setAttribute('aria-controls','firmware-tabpanel-'+id);
      const panel=make('section','firmware-tabpanel');panel.id='firmware-tabpanel-'+id;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);panels.set(id,panel);
      button.onclick=()=>{this.tab=id;showTab();};tabs.append(button);
    }
    const showTab=()=>{for(const [id,panel] of panels){panel.hidden=id!==this.tab;const button=tabs.querySelector<HTMLButtonElement>(`[data-firmware-tab="${id}"]`)!;button.setAttribute('aria-selected',String(id===this.tab));button.tabIndex=id===this.tab?0:-1;}};
    tabs.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const buttons=Array.from(tabs.querySelectorAll<HTMLButtonElement>('button'));let index=buttons.indexOf(document.activeElement as HTMLButtonElement);index=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[index].click();buttons[index].focus();};
    this.host.append(tabs,...panels.values());showTab();
    void this.flash.render(panels.get('flash')!,context.project.id);
    const overview=panels.get('overview')!,pins=panels.get('pins')!,build=panels.get('build')!,downloads=panels.get('downloads')!;
    if(read.requiresDependencyReview)this.host.insertBefore(make('p','connection-problem','原理图版本或需求已变化，继续使用前需要重新核对硬件依赖。'),tabs);
    const description=make('p','firmware-requirements',design.requirements);description.translate=false;identity.append(description);
    const specs=make('div','firmware-specs');
    for(const value of ['ARM '+target.cpu,`HSI · ${target.clockHz/1000000} MHz`,`Flash ${target.flashSize/1024} KB · RAM ${target.ramSize/1024} KB`,String(design.ioVoltage)+' V'])if(value)specs.append(make('span','',value));identity.append(specs);
    pins.append(make('h3','','引脚与硬件依赖'));
    const table=document.createElement('table');table.className='firmware-pin-table';const head=document.createElement('tr');for(const label of ['功能角色','物理脚'])head.append(make('th','',label));const thead=document.createElement('thead');thead.append(head);table.append(thead);const tbody=document.createElement('tbody');
    for(const [role,pad] of Object.entries(design.pinBindings)){const row=document.createElement('tr');row.append(make('td','',roles[role]||role),make('td','',String(pad)));tbody.append(row);}table.append(tbody);pins.append(table,make('p','field-note','此表仅显示显式绑定的角色；完整应用映射以 board.h 和原理图为准。'));
    const hardware=make('div','firmware-binding');
    hardware.append(make('b','',`硬件 ${design.hardwareDependency.revisionId.slice(-8)} · ${design.hardwareDependency.targetReference}`),make('p','field-note',`3.3V 输入假设 · 正常复位与内部 Flash 启动 · 内部 HSI 名义 ${target.clockHz/1000000}MHz`));
    const assumptions=make('details') as HTMLDetailsElement;assumptions.append(make('summary','','设计条件与来源'));
    const evidence=design.hardwareDependency.nativeEvidence;
    if(evidence){
      assumptions.append(make('p','field-note',`原生检查依据 · ${evidence.checkJobId.slice(-8)} · ERC 与网表已固定`),make('code','library-digest',`ERC SHA-256 ${evidence.expectedErcSha256}`),make('code','library-digest',`网表 SHA-256 ${evidence.expectedNetlistSha256}`));
      for(const [id,kind,hash,title] of [[evidence.ercArtifactId,'report',evidence.expectedErcSha256,'下载绑定 ERC ↗'],[evidence.netlistArtifactId,'netlist',evidence.expectedNetlistSha256,'下载绑定网表 ↗']]){
        const artifact=context.artifacts.find(a=>a.id===id&&a.projectId===context.project.id&&a.revisionId===evidence.revisionId&&a.jobId===evidence.checkJobId&&a.kind===kind&&a.sha256===hash);
        if(artifact){const link=make('a','text-button',title) as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';assumptions.append(link);}
      }
    }else assumptions.append(make('p','field-note','历史版本保留原网表身份，尚未记录显式 ERC 产物绑定。'));
    for(const value of design.assumptions)assumptions.append(make('p','field-note',value));
    assumptions.append(make('code','library-digest',`硬件 SHA-256 ${design.hardwareDependency.hardwareHash}`),make('code','library-digest',`工具链 SHA-256 ${design.toolchainHash}`));
    for(const [name,record] of Object.entries(design.applicationProvenance) as [string,any][]){
      const origin=record.source?.registeredSource;
      assumptions.append(make('p','field-note',`${name} · ${origin?'登记源码 · '+origin.id:'模板起点 · '+record.template}`),make('code','library-digest',record.sha256));
      if(origin)assumptions.append(make('p','field-note',`${origin.provenance.uri} · ${origin.provenance.version}`));
    }
    pins.append(hardware);build.append(make('h3','','构建配置'),make('p','field-note','源码修改与构建通过 Agent 提交，保留版本及原生检查。'),make('p','field-note','设备连接与烧录须在“烧录与设备”页单独授权。'),assumptions);assumptions.open=true;
    const toolchain=make('details','firmware-toolchain');toolchain.append(make('summary','','工具链档案'));
    for(const [name,record] of Object.entries(design.toolchain||{}) as [string,any][]){const p=make('p','',name+' · '+record.path);p.translate=false;toolchain.append(p,make('code','library-digest',record.sha256));}build.append(toolchain);
    const metrics=make('div','firmware-metrics');overview.append(make('h3','','硬件资源'),metrics);
    for(const [label,used,capacity] of [['Flash 映像',result?.imageChecks?.flashImageBytes,target.flashSize],['RAM 分配（含栈预留）',result?.imageChecks?.ramAllocatedBytes,target.ramSize]] as const){
      const item=make('div','firmware-resource'),usage=memoryUsage(used,capacity);item.append(make('b','',label),make('p','',usage?`${usage.used} / ${usage.capacity} 字节`:'尚无此版本的内存检查结果'));
      if(usage){const progress=document.createElement('progress');progress.max=100;progress.value=usage.bar;progress.setAttribute('aria-label',label);item.append(progress,make('span','firmware-percent',usage.percent.toFixed(1)+'%'));}metrics.append(item);
    }
    const vector=make('div','firmware-resource');vector.append(make('b','','向量表'),make('strong','',result?.imageChecks?String(result.imageChecks.vectorWords):'—'),make('p','field-note','启动与内存回读'));metrics.append(vector);
    if(result?.imageChecks)build.append(make('p','field-note','ELF、HEX、BIN 的完整装载字节已核对。'));
    if(result?.error){const error=make('details') as HTMLDetailsElement;error.open=true;error.append(make('summary','','构建未通过的原因'),make('pre','firmware-error',result.error));build.append(error);}
    build.append(make('p','quiet-note','构建检查不证明行为、时序或实物运行。实际选项字节、供电、外设波形及上板验证仍需完成。'));
    const documents=make('div','document-downloads');
    for(const [kind,title] of [['firmware_project',result?.checksPassed?'下载固件工程包 ↗':'下载候选诊断包 ↗'],['firmware_elf','下载 ELF ↗'],['firmware_hex','下载 HEX ↗'],['firmware_bin','下载 BIN ↗'],['firmware_report','下载构建报告 ↗']]){
      if(!result?.checksPassed&&['firmware_elf','firmware_hex','firmware_bin'].includes(kind))continue;
      const artifact=context.artifacts.find(a=>a.projectId===context.project.id&&a.revisionId===revision.id&&a.jobId===revision.createdByJobId&&a.kind===kind);
      if(artifact){const link=make('a','text-button',title) as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';documents.append(link);}
    }
    downloads.append(make('h3','','所选版本产物'),documents,make('p','field-note','仅通过构建检查的版本提供 ELF、HEX、BIN；诊断包保留失败信息。'));if(!documents.children.length)documents.append(make('p','field-note','此版本尚无可下载产物。'));overview.append(make('h3','','应用源码'));
    const toolbar=make('div','firmware-source-toolbar'),select=document.createElement('select');select.setAttribute('aria-label','应用源码文件');
    for(const file of read.files){const option=document.createElement('option');option.value=file.file;option.textContent=`${file.file} · ${file.size} 字节`;select.append(option);}
    select.value=this.files.get(revision.id)||read.files[0]?.file||'';
    if(!select.value&&read.files.length)select.value=read.files[0].file;
    const sha=make('code','library-digest'),viewer=new FirmwareSourceView();
    const sourcePath=make('span','firmware-source-path');sourcePath.translate=false;const retry=make('button','text-button','重新读取') as HTMLButtonElement;retry.type='button';toolbar.append(select,sourcePath,retry);overview.append(toolbar,sha,viewer.host);
    let fileGeneration=0;
    const load=async()=>{
      const token=++fileGeneration,name=select.value;this.files.set(revision.id,name);viewer.setLoading();sha.textContent='';sourcePath.textContent='app/'+name;
      try{
        let offset:number|null=0,source='';
        while(offset!==null){
          const item:any=await api(`projects/${context.project.id}/firmware?revisionId=${encodeURIComponent(revision.id)}&file=${encodeURIComponent(name)}&offset=${offset}&limit=32000`);
          if(generation!==this.generation||token!==fileGeneration||select.value!==name)return;
          source+=item.file.text;offset=item.file.nextOffset;sha.textContent=`SHA-256 ${item.file.sha256}`;
        }
        viewer.setSource(name,source);
      }catch(error){if(generation===this.generation&&token===fileGeneration)viewer.setError((error as Error).message);}
    };
    select.onchange=()=>{void load();};retry.onclick=()=>{void load();};retry.disabled=!read.files.length;
    if(read.files.length)await load();else viewer.setError('此候选没有可读取的应用源码。');
  }
}
