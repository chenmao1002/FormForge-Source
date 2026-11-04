import {RulePresetPanel} from './rule-presets';
import {api,type Context} from './api';
import {numericEditor,projectRules,initialRules,type NumericRules} from './pcb-rule-settings';
import {MaintenancePanel} from './maintenance';
import {locale,currentLanguage,setLanguage} from './i18n';
import {renderModuleSettings,type ModuleDefaults,type ModuleValues} from './module-settings';

interface ToolStatus {id:string;name:string;path:string|null;version:string|null;available:boolean;reason:string|null;purpose:string;origin?:string}
interface PcbDefaults {numericRules:NumericRules;boardCorner:'rounded'|'square';cornerRadiusMm:number;squareCornerReason:string|null;routingStyle:'balanced'|'tidy'|'shortest';placementDensity:'balanced'|'compact'|'serviceable';silkscreenStyle:'functional'|'minimal'|'detailed';viaPreference:'project-rules'|'conservative'}
interface ManufacturingDefaults {provider:'jlc';shippingSource:'provider-default'|'confirm-at-checkout';shippingProfileLabel:string;invoiceSource:'provider-default'|'confirm-at-checkout';invoiceType:'confirm-at-checkout'|'none'|'personal'|'enterprise-general'|'enterprise-special';invoiceProfileLabel:string}
interface ToolProfile {id:string;kind:'kicad'|'arm-gcc';name:string;executable:string;version:string;fingerprint:string;validatedAt:string}
interface WorkspaceConfiguration {moduleDefaults:ModuleDefaults;version:string;updatedAt:string|null;pcbDefaults:PcbDefaults;manufacturingDefaults:ManufacturingDefaults;toolchains:{profiles:ToolProfile[];active:Record<'kicad'|'arm-gcc',string|null>;runtime:Record<'kicad'|'arm-gcc',string|null>;origins:Record<'kicad'|'arm-gcc',string>;restartRequired:Record<'kicad'|'arm-gcc',boolean>};privacy:{manufacturingStoresFullAddress:boolean;manufacturingStoresPhone:boolean;manufacturingStoresTaxIdentity:boolean}}
interface WorkspaceInfo {
  version:string;observedAt:string;serviceStartedAt:string;
  connection:{endpoint:string;transport:string;authentication:string;tokenFile:string;localOnly:boolean;httpExample:unknown;stdioAvailable:boolean;stdioExample:unknown};
  storage:{root:string;database:string;projects:string;runtime:string;projectStorage?:{enabled:boolean;mode:string;scope:string;reason:string|null}};tools:ToolStatus[];configuration:WorkspaceConfiguration;
}
interface Usage {id:string;name:string;description:string;path:string;bytes:number;files:number;allocatedBytes?:number;allocatedFiles?:number;allocationUnreadableEntries?:number;skippedLinks:number;unreadableEntries:number;status:string;reason:string|null}
interface StorageInfo {observedAt:string;cacheSeconds:number;scanning:boolean;locations:Usage[];volume:{totalBytes:number;freeBytes:number}|null}
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,cls='',value='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=value;return n;};
const date=(value:string)=>new Date(value).toLocaleString(locale(),{hour12:false});
const size=(value:number)=>{const units=['B','KiB','MiB','GiB','TiB'];let n=value,i=0;while(n>=1024&&i<units.length-1){n/=1024;i++;}return `${n.toLocaleString(locale(),{maximumFractionDigits:i?2:0})} ${units[i]}`;};

export class SettingsPanel {
  private maintenance:MaintenancePanel;
  private presets:RulePresetPanel;
  private loaded=false;
  private loading=false;
  private active=false;
  private pollTimer:number|undefined;
  private polling=false;
  private generation=0;
  private workspace:WorkspaceInfo|undefined;
  private usage:StorageInfo|undefined;
  private storageRoot:string|undefined;
  private storageBuilt=false;
  private disk=node('div','settings-disk');
  private usageList=node('div','settings-usage-list');
  private storageNote=node('p','field-note');
  private storagePolicy=node('p','field-note');
  private usageRows=new Map<string,{amount:HTMLElement;logical:HTMLElement;count:HTMLElement;reason:HTMLElement;skipped:HTMLElement}>();
  private connection=node('div','settings-loading','正在读取接入信息…');
  private pcbDefaults=node('div','settings-loading','正在读取 PCB 默认偏好…');
  private manufacturingDefaults=node('div','settings-loading','正在读取制造资料选择…');
  private moduleHosts=new Map<string,HTMLElement>(['pcb','schematic','mechanical','firmware','manufacturing'].map(key=>[key,node('div')]));
  private general=node('div');
  private storage=node('div','settings-loading','正在统计本机存储…');
  private tools=node('div','settings-loading','正在读取工具状态…');
  private status=node('p','field-note');
  private refresh=node('button','button secondary','刷新信息');

  constructor(private host:HTMLElement,private toast:(message:string,error?:boolean)=>void,changed:()=>Promise<unknown>=async()=>{},private getContext:()=>Context|undefined=()=>undefined){
    const lead=node('div','settings-lead');
    lead.append(this.refresh);this.refresh.onclick=()=>{void this.load();void this.maintenance.reload();};
    this.status.setAttribute('role','status');
    host.append(lead,this.status);
    const language=this.select(currentLanguage(),[['zh-CN','中文'],['en','English']]);language.id='settings-language';language.setAttribute('aria-label','界面语言');language.onchange=()=>setLanguage(language.value==='en'?'en':'zh-CN');window.addEventListener('formforge:languagechange',()=>{language.value=currentLanguage();});
    this.general.append(this.control('界面语言',language,'在此浏览器立即生效，与顶部语言选择同步。'));
    const presets=node('section');presets.id='settings-rule-presets';this.general.append(presets);this.presets=new RulePresetPanel(presets,this.getContext,this.toast,async()=>{await this.load();await changed();});
    for(const [id,title,description,content] of [
      ['settings-general','通用设置','',this.general],
      ['settings-schematic','原理图设置','',this.moduleHosts.get('schematic')!],
      ['settings-mechanical','机械设计设置','',this.moduleHosts.get('mechanical')!],
      ['settings-firmware','固件设置','',this.moduleHosts.get('firmware')!],
      ['settings-manufacturing-tasks','制造任务默认值','',this.moduleHosts.get('manufacturing')!],
      ['settings-connection','Agent / MCP 接入','',this.connection],
      ['settings-pcb-defaults','PCB 设置','个人默认用于新工程；当前工程的数值规则需要单独应用，并由原生检查确认。',this.pcbDefaults],
      ['settings-manufacturing-defaults','制造账户与资料选择','仅保存供应商资料来源和脱敏别名；地址、电话、税号及银行资料仍留在供应商账户。',this.manufacturingDefaults],
      ['settings-storage','存储概览','',this.storage],
      ['settings-tools','工具链档案','选择并校验不同的本机工具链；FormForge 不移动、复制或删除外部工具。',this.tools],
    ] as const){
      const card=node('section','card settings-section');card.id=id;
      const heading=node('div','card-heading');heading.append(node('h2','',title));
      card.append(heading);if(description)card.append(node('p','settings-description',description));card.append(content);host.append(card);
    }
    this.maintenance=new MaintenancePanel(host,toast,async()=>{await changed();await this.load();});
    const toolsSection=host.querySelector('#settings-tools')!;
    for(const id of ['settings-cleanup','settings-project-trash','settings-cleanup-history'])host.insertBefore(host.querySelector('#'+id)!,toolsSection);
    const tabs=node('nav','settings-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','设置分区');
    const panels=new Map<string,HTMLElement>();
    for(const [id,label,sections] of [
      ['storage','通用',['settings-general','settings-storage','settings-cleanup','settings-project-trash','settings-cleanup-history']],
      ['schematic','原理图',['settings-schematic']],
      ['design','PCB',['settings-pcb-defaults']],
      ['mechanical','机械设计',['settings-mechanical']],
      ['firmware','固件',['settings-firmware']],
      ['manufacturing','制造设置',['settings-manufacturing-tasks','settings-manufacturing-defaults']],
      ['tools','工具与集成',['settings-tools']],
      ['connection','Agent / MCP',['settings-connection']],
    ] as const){
      const button=node('button','',label);button.type='button';button.id='settings-tab-'+id;button.dataset.settingsTab=id;button.setAttribute('role','tab');button.setAttribute('aria-controls','settings-tabpanel-'+id);
      const panel=node('div','settings-tabpanel');panel.id='settings-tabpanel-'+id;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);
      for(const section of sections)panel.append(host.querySelector('#'+section)!);
      panels.set(id,panel);tabs.append(button);host.append(panel);
      button.onclick=()=>select(id);
    }
    const select=(id:string)=>{
      for(const [key,panel] of panels){panel.hidden=key!==id;const button=tabs.querySelector<HTMLButtonElement>(`[data-settings-tab="${key}"]`)!;button.setAttribute('aria-selected',String(key===id));button.tabIndex=key===id?0:-1;}
    };
    tabs.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;event.preventDefault();const buttons=Array.from(tabs.querySelectorAll<HTMLButtonElement>('button'));const index=buttons.indexOf(document.activeElement as HTMLButtonElement),next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(['ArrowRight','ArrowDown'].includes(event.key)?1:-1)+buttons.length)%buttons.length;buttons[next].click();buttons[next].focus();};
    const layout=node('div','settings-layout'),contents=node('div','settings-contents');for(const panel of panels.values())contents.append(panel);layout.append(tabs,contents);host.append(layout);tabs.setAttribute('aria-orientation','vertical');select('design');
  }

  render(){this.active=true;this.maintenance.activate();if(!this.loaded&&!this.loading)void this.load();else this.schedule();}
  deactivate(){this.active=false;this.maintenance.deactivate();clearTimeout(this.pollTimer);this.pollTimer=undefined;}

  private schedule(){
    if(this.active&&this.usage?.scanning&&!this.loading&&!this.polling&&this.pollTimer===undefined)
      this.pollTimer=window.setTimeout(()=>{this.pollTimer=undefined;void this.pollStorage();},1000);
  }

  private async pollStorage(){
    if(this.polling)return;this.polling=true;const generation=this.generation;let success=false;
    try{
      const usage=await api<StorageInfo>('settings/storage');
      if(generation!==this.generation)return;
      this.usage=usage;this.showStorage(usage,this.workspace);const message=usage.scanning?'存储统计进行中，可继续使用其他页面。':`存储统计更新于 ${date(usage.observedAt)}`;if(this.status.textContent!==message)this.status.textContent=message;success=true;
    }catch{if(generation===this.generation)this.status.textContent='存储统计暂不可读，请点击“刷新信息”重试。';}
    finally{this.polling=false;if(success)this.schedule();}
  }

  private async copy(value:string,label:string){
    try{await navigator.clipboard.writeText(value);this.toast(`${label}已复制。`);}
    catch{this.toast('无法访问剪贴板，请选中文本后手动复制。',true);}
  }

  private copyButton(value:string,label:string){
    const button=node('button','button secondary',`复制${label}`);button.type='button';
    button.onclick=()=>void this.copy(value,label);return button;
  }

  private field(label:string,value:string,copyLabel?:string){
    const row=node('div','settings-field'),body=node('div');
    body.append(node('span','settings-label',label),node('code','settings-path',value));row.append(body);
    if(copyLabel)row.append(this.copyButton(value,copyLabel));return row;
  }

  private example(title:string,value:unknown,description:string,expanded=false){
    const detail=node('details','settings-example');detail.open=expanded;
    detail.append(node('summary','',title),node('p','field-note',description));
    const json=JSON.stringify(value,null,2),pre=node('pre','settings-code',json);
    pre.tabIndex=0;pre.setAttribute('aria-label',title+'示例');detail.append(pre,this.copyButton(json,title));return detail;
  }

  private control(label:string,control:HTMLElement,note?:string){
    const row=node('label','settings-control');row.append(node('span','settings-label',label),control);
    if(note)row.append(node('small','field-note',note));return row;
  }

  private select<T extends string>(value:T,options:Array<[T,string]>){
    const select=node('select') as HTMLSelectElement;
    for(const [key,label] of options){const option=document.createElement('option');option.value=key;option.textContent=label;select.append(option);}select.value=value;return select;
  }

  private async saveDefaults(pcb:PcbDefaults,manufacturing:ManufacturingDefaults,button:HTMLButtonElement,modules?:ModuleDefaults){
    if(!this.workspace)return;button.disabled=true;
    try{
      const configuration=await api<WorkspaceConfiguration>('settings/defaults','PUT',{expectedVersion:this.workspace.configuration.version,pcbDefaults:pcb,manufacturingDefaults:manufacturing,...(modules?{moduleDefaults:modules}:{})});
      this.workspace.configuration=configuration;this.showPcbDefaults(this.workspace);this.showManufacturingDefaults(this.workspace);this.showModules(this.workspace);this.showTools(this.workspace);this.toast('本机默认设置已保存。');
    }finally{button.disabled=false;}
  }

  private showModules(info:WorkspaceInfo){
    for(const [module,host] of this.moduleHosts){
      if(!info.configuration.moduleDefaults?.[module]){host.replaceChildren(node('p','field-note','本地服务尚未加载模块设置；当前任务结束并更新服务后，请刷新。'));continue;}
      renderModuleSettings(host,module,info.configuration.moduleDefaults[module],this.getContext,async(values:ModuleValues)=>{
        if(!this.workspace)return;
        const c=this.workspace.configuration;
        await this.saveDefaults(c.pcbDefaults,c.manufacturingDefaults,node('button'),{...c.moduleDefaults,[module]:values});
      },this.toast);
    }
  }

  private showPcbDefaults(info:WorkspaceInfo){
    const current=info.configuration.pcbDefaults,form=node('form','settings-form') as HTMLFormElement;
    const corner=this.select(current.boardCorner,[['rounded','默认圆角'],['square','默认直角（需说明原因）']]);
    const radius=node('input') as HTMLInputElement;radius.type='number';radius.min='0';radius.max='25';radius.step='0.1';radius.value=String(current.cornerRadiusMm);
    const reason=node('input') as HTMLInputElement;reason.maxLength=300;reason.value=current.squareCornerReason||'';reason.placeholder='例如：明确的机加工或拼板边界要求';
    const routing=this.select(current.routingStyle,[['balanced','平衡'],['tidy','整齐优先'],['shortest','短路径优先']]);
    const density=this.select(current.placementDensity,[['balanced','平衡'],['compact','紧凑'],['serviceable','装配与维修空间优先']]);
    const silkscreen=this.select(current.silkscreenStyle,[['functional','功能标识优先'],['minimal','精简'],['detailed','详细']]);
    const via=this.select(current.viaPreference,[['project-rules','按项目工艺规则'],['conservative','保守尺寸优先']]);
    const sync=()=>{const square=corner.value==='square';radius.disabled=square;reason.disabled=!square;reason.required=square;if(square)radius.value='0';else{if(Number(radius.value)<=0)radius.value='3';reason.value='';}};corner.onchange=sync;sync();
    const grid=node('div','settings-form-grid');grid.append(this.control('板框',corner),this.control('默认圆角半径（mm）',radius),this.control('直角原因',reason),this.control('布线风格',routing),this.control('摆放密度',density),this.control('丝印风格',silkscreen),this.control('过孔偏好',via));
    const actions=node('div','settings-form-actions'),save=node('button','button primary','保存 PCB 默认偏好') as HTMLButtonElement;save.type='submit';actions.append(save);
    form.append(grid,node('p','field-note','这些值会复制到新工程的结构化软规则，并记录设置版本。焊盘中心出线、锐角限制、原生 DRC 等工程硬约束不能在这里关闭。'),actions);
    form.onsubmit=async event=>{event.preventDefault();try{await this.saveDefaults({numericRules:current.numericRules,boardCorner:corner.value as PcbDefaults['boardCorner'],cornerRadiusMm:Number(radius.value),squareCornerReason:corner.value==='square'?reason.value.trim():null,routingStyle:routing.value as PcbDefaults['routingStyle'],placementDensity:density.value as PcbDefaults['placementDensity'],silkscreenStyle:silkscreen.value as PcbDefaults['silkscreenStyle'],viaPreference:via.value as PcbDefaults['viaPreference']},info.configuration.manufacturingDefaults,save);}catch(error){this.toast((error as Error).message,true);}};
    this.pcbDefaults.className='settings-defaults';this.pcbDefaults.replaceChildren(form);
    const numerical=node('section'),heading=node('h3','','PCB 数值规则 · 个人默认'),editor=numericEditor(current.numericRules||initialRules);
    const saveNumbers=node('button','button primary','保存数值默认值');saveNumbers.type='button';
    const reset=node('button','button secondary','恢复系统默认值');reset.type='button';
    let activeEditor=editor;const holder=node('div');holder.append(editor.root);
    reset.onclick=()=>{activeEditor=numericEditor(initialRules);holder.replaceChildren(activeEditor.root);};
    saveNumbers.onclick=async()=>{try{await this.saveDefaults({...current,numericRules:activeEditor.read()},info.configuration.manufacturingDefaults,saveNumbers);}catch(e){this.toast((e as Error).message,true);}};
    numerical.append(heading,node('p','field-note','最小值是允许的下限，不要求所有走线取最小值。仅影响以后新建的工程。'),holder,reset,saveNumbers);
    const project=node('section');project.append(node('h3','','当前工程规则'));projectRules(project,this.getContext,current.numericRules||initialRules,this.toast);
    const style=node('details');style.append(node('summary','','其他 PCB 默认偏好'),form);const zones=node('section');zones.append(node('h3','','PCB 新铺铜默认参数'),this.moduleHosts.get('pcb')!);this.pcbDefaults.replaceChildren(numerical,project,zones,style);
  }

  private showManufacturingDefaults(info:WorkspaceInfo){
    const current=info.configuration.manufacturingDefaults,form=node('form','settings-form') as HTMLFormElement;
    const shipping=this.select(current.shippingSource,[['provider-default','使用供应商账户默认资料'],['confirm-at-checkout','每次结算时选择并核对']]);
    const shippingLabel=node('input') as HTMLInputElement;shippingLabel.maxLength=80;shippingLabel.required=true;shippingLabel.value=current.shippingProfileLabel;shippingLabel.placeholder='例如：上海默认收货资料';
    const invoice=this.select(current.invoiceSource,[['provider-default','使用供应商账户默认资料'],['confirm-at-checkout','每次结算时选择并核对']]);
    const invoiceType=this.select(current.invoiceType,[['confirm-at-checkout','结算时确认'],['none','不开票'],['personal','个人发票'],['enterprise-general','企业增值税普通发票'],['enterprise-special','企业增值税专用发票']]);
    const invoiceLabel=node('input') as HTMLInputElement;invoiceLabel.maxLength=80;invoiceLabel.required=true;invoiceLabel.value=current.invoiceProfileLabel;invoiceLabel.placeholder='例如：默认企业开票资料';
    const grid=node('div','settings-form-grid');grid.append(this.control('收货资料来源',shipping),this.control('收货资料脱敏别名',shippingLabel,'不要填写完整地址、姓名或电话。'),this.control('开票资料来源',invoice),this.control('默认发票类型',invoiceType),this.control('开票资料脱敏别名',invoiceLabel,'不要填写税号、银行账号或开户地址。'));
    const actions=node('div','settings-form-actions'),save=node('button','button primary','保存制造资料选择') as HTMLButtonElement;save.type='submit';actions.append(save);
    form.append(grid,node('p','field-note','正式准备和待付款提交会绑定这份设置的版本与脱敏选择；供应商结算返回仍是实际地址、发票和运费的唯一依据。'),actions);
    form.onsubmit=async event=>{event.preventDefault();try{await this.saveDefaults(info.configuration.pcbDefaults,{provider:'jlc',shippingSource:shipping.value as ManufacturingDefaults['shippingSource'],shippingProfileLabel:shippingLabel.value.trim(),invoiceSource:invoice.value as ManufacturingDefaults['invoiceSource'],invoiceType:invoiceType.value as ManufacturingDefaults['invoiceType'],invoiceProfileLabel:invoiceLabel.value.trim()},save);}catch(error){this.toast((error as Error).message,true);}};
    this.manufacturingDefaults.className='settings-defaults';this.manufacturingDefaults.replaceChildren(form);
  }

  private showConnection(info:WorkspaceInfo){
    const opened=[...this.connection.querySelectorAll('details')].map(detail=>detail.open);
    const c=info.connection;this.connection.className='settings-connection';this.connection.replaceChildren();
    const badges=node('div','settings-badges');badges.append(node('span','small-tag',c.transport),node('span','small-tag',`${c.authentication} 认证`),node('span','small-tag','仅本机访问'));
    this.connection.append(badges,this.field('MCP 服务地址',c.endpoint,'MCP 地址'),this.field('令牌文件',c.tokenFile,'令牌文件路径'));
    this.connection.append(node('p','field-note','令牌内容不在此页展示。HTTP 客户端需从上述本机文件取得令牌，再填写到 Authorization 请求头。'));
    const examples=node('div','settings-examples');
    examples.append(this.example('stdio 配置',c.stdioExample,'适用于本机 stdio 客户端；代理自动读取令牌。先启动 FormForge，外层字段按客户端要求调整。',opened[0]??false),
      this.example('HTTP 配置',c.httpExample,'适用于支持 Streamable HTTP 的客户端。将 <本机令牌> 替换为本机文件中的内容，字段按客户端要求调整。',opened[1]??false));
    this.connection.append(examples);
    if(!c.stdioAvailable)this.connection.append(node('p','settings-warning','stdio 代理或本项目 Python 文件缺失，请先修复本地环境。'));
    this.connection.append(node('p','field-note','云端 Agent 无法直接连接此电脑的 127.0.0.1。此页不修改访问权限。'));
  }

  private showStorage(info:StorageInfo,workspace?:WorkspaceInfo){
    if(!this.storageBuilt||this.storageRoot!==workspace?.storage.root){
      this.storage.className='settings-storage';this.storage.replaceChildren();this.usageRows.clear();this.usageList.replaceChildren();
      this.storageRoot=workspace?.storage.root;this.storageBuilt=true;
      if(workspace)this.storage.append(this.field('工作台根目录',workspace.storage.root,'根目录'),
          this.field('工程快照与产物',workspace.storage.projects,'工程目录'),this.field('数据库文件',workspace.storage.database,'数据库路径'));
      this.storage.append(this.disk,this.storagePolicy,this.usageList,this.storageNote,node('p','field-note','可在下方扫描并清理缓存。工程文件通过回收站管理；目录迁移尚未提供。'));
    }
    this.storagePolicy.id='project-storage-policy';
    this.storagePolicy.textContent=workspace?.storage.projectStorage?.enabled?'新工程默认使用无损压缩存储。格式、内容和版本历史保持完整；已有工程文件保持原样。':'新工程使用普通存储；当前平台或目录未启用无损压缩。';
    this.disk.hidden=!info.volume;
    if(info.volume)this.disk.replaceChildren(node('strong','',`所在磁盘可用 ${size(info.volume.freeBytes)}`),node('span','',`磁盘总容量 ${size(info.volume.totalBytes)}，包含其他应用和文件`));
    for(const usage of info.locations){
      let fields=this.usageRows.get(usage.id);
      if(!fields){
        const row=node('div','settings-usage-row'),body=node('div'),metric=node('div','settings-usage-metric');
        body.append(node('strong','',usage.name),node('span','settings-path',usage.path),node('small','',usage.description));
        fields={amount:node('strong'),logical:node('p','field-note'),count:node('span'),reason:node('p','settings-warning'),skipped:node('p','field-note')};
        metric.append(fields.amount,fields.logical,fields.count);row.append(body,metric,fields.reason,fields.skipped);this.usageList.append(row);this.usageRows.set(usage.id,fields);
      }
      const measured=['complete','partial','scanning'].includes(usage.status);
      const storedKnown=typeof usage.allocatedBytes==='number'&&usage.allocatedFiles===usage.files;
      fields.amount.textContent=usage.status==='scanning'&&!usage.files?'统计中…':measured?(storedKnown?`磁盘占用 ${usage.status!=='complete'?'至少 ':''}${size(usage.allocatedBytes!)}`:'磁盘占用未完整统计'):usage.status==='missing'?'未创建':'未取得';
      fields.logical.textContent=measured?`文件总大小 ${usage.status!=='complete'?'至少 ':''}${size(usage.bytes)}`:'';
      fields.count.textContent=usage.status==='scanning'?`统计中 · 已读 ${usage.files.toLocaleString()} 个文件`:measured?`${usage.files.toLocaleString()} 个文件`:'此位置未计入';
      fields.reason.hidden=!usage.reason;fields.reason.textContent=usage.reason;
      fields.skipped.hidden=!usage.skippedLinks;fields.skipped.textContent=`跳过 ${usage.skippedLinks} 个链接或重解析入口。`;
    }
    this.storageNote.textContent=`${info.scanning?'后台统计进行中，页面会自动更新；每个目录最多扫描60秒。':'统计于 '+date(info.observedAt)+'，完成结果最多复用 '+info.cacheSeconds+' 秒。'}文件总大小与磁盘占用分别统计，排除链接；环境、依赖与其他目录不计入。压缩后的实际占用以落盘结果为准，节省比例因文件而异。“至少”表示尚未统计完整。`;
  }

  private showTools(info:WorkspaceInfo){
    this.tools.className='settings-tools';this.tools.replaceChildren();
    const grid=node('div','settings-tool-grid');
    for(const tool of info.tools){
      const card=node('article','settings-tool'),heading=node('div','card-heading');
      heading.append(node('h3','',tool.name),node('span',`badge ${tool.available?'success':'warning'}`,tool.available?'可用':'不可用'));
      const origin:Record<string,string>={explicit:'启动参数',environment:'环境变量','saved-profile':'已选档案',automatic:'自动发现','managed-environment':'项目管理环境'};
      card.append(heading,node('p','',tool.purpose),node('code','settings-path',tool.path||'尚未配置'),node('p','field-note',`${tool.version||'版本未记录'} · ${origin[tool.origin||'automatic']||tool.origin}`));
      if(tool.reason)card.append(node('p','settings-warning',tool.reason));grid.append(card);
    }
    this.tools.append(grid,node('p','field-note',`FormForge ${info.version} · 服务初始化于 ${date(info.serviceStartedAt)}。状态基于服务启动时的检测与当前工具文件是否存在；工具可用不代表具体工程检查通过。`));
    const configuration=info.configuration,profiles=node('div','settings-profile-list');
    for(const profile of configuration.toolchains.profiles){
      const active=configuration.toolchains.active[profile.kind]===profile.id,row=node('article','settings-profile'),body=node('div');
      body.append(node('strong','',profile.name),node('span','settings-path',profile.executable),node('small','field-note',`${profile.kind==='kicad'?'KiCad':'ARM GNU'} · ${profile.version} · 校验于 ${date(profile.validatedAt)}`));row.append(body);
      if(active)row.append(node('span','badge success',configuration.toolchains.restartRequired[profile.kind]?'待重启生效':'当前档案'));
      else{const activate=node('button','button secondary','激活') as HTMLButtonElement;activate.type='button';activate.onclick=async()=>{activate.disabled=true;try{info.configuration=await api<WorkspaceConfiguration>('settings/toolchains/active','PUT',{expectedVersion:info.configuration.version,kind:profile.kind,profileId:profile.id});this.showTools(info);this.toast('工具链档案已选择；按提示重启服务后生效。');}catch(error){this.toast((error as Error).message,true);}finally{activate.disabled=false;}};row.append(activate);}profiles.append(row);
    }
    if(!configuration.toolchains.profiles.length)profiles.append(node('p','field-note','尚未保存工具链档案；当前服务继续使用启动参数、环境变量或自动发现结果。'));
    const form=node('form','settings-form settings-tool-form') as HTMLFormElement,kind=this.select<'kicad'|'arm-gcc'>('kicad',[['kicad','KiCad 10'],['arm-gcc','ARM GNU']]);
    const name=node('input') as HTMLInputElement;name.required=true;name.maxLength=80;name.placeholder='例如：KiCad 10.0.6';
    const executable=node('input') as HTMLInputElement;executable.required=true;executable.maxLength=1000;executable.placeholder='选择已有工具的主程序绝对路径';
    const activate=node('input') as HTMLInputElement;activate.type='checkbox';const check=node('label','checkbox-label');check.append(activate,document.createTextNode(' 校验后设为激活档案'));
    const save=node('button','button primary','校验并保存档案') as HTMLButtonElement;save.type='submit';
    const formGrid=node('div','settings-form-grid');formGrid.append(this.control('工具类型',kind),this.control('档案名称',name),this.control('主程序路径',executable,'只读取并校验所选工具，不移动或复制。'));form.append(formGrid,check,node('p','field-note','KiCad 会同时核对同安装目录的 Python 与 share/kicad；ARM GNU 会核对同根目录的 objcopy、readelf、size、子工具及 libgcc。'),save);
    form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{info.configuration=await api<WorkspaceConfiguration>('settings/toolchains','POST',{expectedVersion:info.configuration.version,kind:kind.value,name:name.value.trim(),executable:executable.value.trim(),activate:activate.checked});this.showTools(info);this.toast(activate.checked?'工具链已校验并选择；重启服务后生效。':'工具链档案已校验并保存。');}catch(error){this.toast((error as Error).message,true);}finally{save.disabled=false;}};
    const restart=Object.entries(configuration.toolchains.restartRequired).filter(([,required])=>required).map(([kind])=>kind==='kicad'?'KiCad':'ARM GNU');
    this.tools.append(node('h3','settings-subheading','已验证档案'),profiles,node('h3','settings-subheading','添加工具链档案'),form);
    for(const kind of ['kicad','arm-gcc'] as const)if(configuration.toolchains.active[kind]){const automatic=node('button','button secondary',`取消 ${kind==='kicad'?'KiCad':'ARM GNU'} 档案选择`) as HTMLButtonElement;automatic.type='button';automatic.onclick=async()=>{automatic.disabled=true;try{info.configuration=await api<WorkspaceConfiguration>('settings/toolchains/active','PUT',{expectedVersion:info.configuration.version,kind,profileId:null});this.showTools(info);this.toast('已取消档案选择；重启后按启动参数、环境变量或自动发现选择工具。');}catch(error){this.toast((error as Error).message,true);}finally{automatic.disabled=false;}};this.tools.append(automatic);}
    if(restart.length)this.tools.append(node('p','settings-restart',`${restart.join('、')} 已选择新档案。当前任务仍使用启动时工具；正常重启 FormForge 后生效。`));
  }

  private async load(){
    if(this.loading)return;this.loading=true;this.generation++;clearTimeout(this.pollTimer);this.pollTimer=undefined;this.refresh.disabled=true;this.status.textContent='正在读取本机信息与存储统计…';
    const infoRequest=api<WorkspaceInfo>('settings').then(async info=>{this.workspace=info;this.showConnection(info);this.showPcbDefaults(info);this.showManufacturingDefaults(info);this.showModules(info);this.showTools(info);await this.presets.reload();return info;});
    const storageRequest=api<StorageInfo>('settings/storage').then(async usage=>{this.usage=usage;this.showStorage(usage,await infoRequest.catch(()=>undefined));return usage;});
    const [info,usage]=await Promise.allSettled([infoRequest,storageRequest]);
    if(info.status==='rejected'){this.connection.className='settings-warning';this.connection.textContent=`接入信息暂不可读：${info.reason.message}`;this.pcbDefaults.replaceChildren(node('p','settings-warning','未取得 PCB 默认偏好。'));this.manufacturingDefaults.replaceChildren(node('p','settings-warning','未取得制造资料选择。'));this.tools.replaceChildren(node('p','settings-warning','未取得工具状态，请稍后刷新。'));}
    if(usage.status==='rejected'){this.storageBuilt=false;this.storage.className='settings-warning';this.storage.textContent=`存储统计暂不可读：${usage.reason.message}`;}
    this.status.textContent=info.status==='fulfilled'?`信息读取于 ${date(info.value.observedAt)}${usage.status==='rejected'?' · 存储统计失败，可重新刷新。':usage.value.scanning?' · 存储正在后台统计。':''}`:'读取未完成，请检查本地服务后重试。';
    this.loaded=true;this.loading=false;this.refresh.disabled=false;this.schedule();
  }
}
