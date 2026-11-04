import {api,fileUrl,requestId,type Context,type Artifact} from './api';
import {translateText} from './i18n';

const make=<K extends keyof HTMLElementTagNameMap>(tag:K,text='',cls='')=>{const el=document.createElement(tag);el.textContent=text;el.className=cls;return el;};
const control=(value:string,type='text')=>{const el=document.createElement('input');el.type=type;el.value=value;if(type==='number')el.step='any';return el;};
const choose=(options:[string,string][],value:string)=>{const el=document.createElement('select');for(const [id,label] of options){const opt=document.createElement('option');opt.value=id;opt.textContent=label;el.append(opt);}el.value=value;return el;};
const label=(name:string,input:HTMLElement)=>{const el=document.createElement('label');el.append(make('span',name),input);return el;};
const button=(title:string,action:()=>void)=>{const el=make('button',title,'button secondary') as HTMLButtonElement;el.type='button';el.onclick=action;return el;};
const num=(value:string)=>{const n=Number(value);if(value.trim()===''||!Number.isFinite(n))throw new Error('请填写有效数字。');return n;};
const transform=()=>({xSign:1,ySign:1,offsetXmm:0,offsetYmm:0,frameRotationDegrees:0,sourceAngleDirection:'ccw',sourceAngleZeroDegrees:0});
type TableDraft={key:string;format:string;sheet:string;encoding:string;delimiter:string;headerRow:string;headers:string[];sheets:string[];columns:Record<string,string>};
type Draft={archive:string;entries:any[];count:string;mapping:Record<string,{role:string;reason:string;unit:string;integer:string;decimal:string;zeros:string}>;mode:string;confirmed:boolean;version:number;bom:TableDraft;cpl:TableDraft;dnp:string;dnpReason:string;dnpTrue:string;dnpFalse:string;quantity:string;unit:string;footprintComparison:string;topValues:string;bottomValues:string;top:any;bottom:any;excluded:string;outside:string;message:string;pending?:{fingerprint:string;id:string}};
const tableDraft=():TableDraft=>({key:'',format:'csv',sheet:'',encoding:'utf-8-sig',delimiter:',',headerRow:'1',headers:[],sheets:[],columns:{}});
const fresh=(version:number):Draft=>({archive:'',entries:[],count:'2',mapping:{},mode:'pcb-only',confirmed:false,version,bom:tableDraft(),cpl:tableDraft(),dnp:'column',dnpReason:translateText('根据所选DNP列明确装配；空白表示装配。'),dnpTrue:'1,true,yes,dnp',dnpFalse:',0,false,no',quantity:'',unit:'mm',footprintComparison:'name-after-colon',topValues:'top,F.Cu,T',bottomValues:'bottom,B.Cu,B',top:transform(),bottom:transform(),excluded:'',outside:'',message:translateText('外部制造资料审核；工程与生产资格另列。')});

class CamViewer {
  readonly host=make('section','','external-cam-viewer');
  private select=document.createElement('select');private image=document.createElement('img');private stage=make('div','','external-cam-stage');
  private scale=1;private x=0;private y=0;private projectJob='';private artifacts:Artifact[]=[];private selected='';private drag:{x:number;y:number}|null=null;
  constructor(){
    const bar=make('div','','viewer-toolbar');this.select.setAttribute('aria-label','制造层图');this.image.alt='实际Gerber、钻孔及装配锚点预览';
    this.select.onchange=()=>{this.selected=this.select.value;this.show();};
    bar.append(this.select,button('−',()=>this.zoom(1/1.25)),button('适应',()=>this.reset()),button('+',()=>this.zoom(1.25)));
    this.stage.append(this.image);this.host.append(bar,this.stage,make('p','拖动平移 · 滚轮缩放 · 所有层使用同一俯视坐标；箭头只表示声明的角度。','quiet-note'));
    this.stage.onwheel=event=>{event.preventDefault();this.zoom(event.deltaY>0?1/1.15:1.15);};
    this.stage.onpointerdown=event=>{this.drag={x:event.clientX-this.x,y:event.clientY-this.y};this.stage.setPointerCapture(event.pointerId);};
    this.stage.onpointermove=event=>{if(this.drag){this.x=event.clientX-this.drag.x;this.y=event.clientY-this.drag.y;this.paint();}};
    this.stage.onpointerup=this.stage.onpointercancel=()=>{this.drag=null;};
  }
  private paint(){this.image.style.transform=`translate(${this.x}px,${this.y}px) scale(${this.scale})`;}
  private zoom(factor:number){this.scale=Math.max(.2,Math.min(15,this.scale*factor));this.paint();}
  private reset(){this.scale=1;this.x=this.y=0;this.paint();}
  private show(){const a=this.artifacts.find(a=>a.id===this.selected);this.image.hidden=!a;if(a){if(this.image.getAttribute('src')!==fileUrl(a))this.image.src=fileUrl(a);}else this.image.removeAttribute('src');}
  update(job:string,artifacts:Artifact[],previews:any[]=[]){
    if(this.projectJob!==job){this.projectJob=job;this.selected='';this.reset();}
    this.artifacts=artifacts;
    this.select.replaceChildren();
    for(const a of artifacts){const opt=document.createElement('option');opt.value=a.id;const p=previews.find(p=>p.path===a.name);opt.textContent=a.name==='assembly-overlay.svg'?'装配锚点与正面铜层':p?.sourceFile||a.name;this.select.append(opt);}
    if(!artifacts.some(a=>a.id===this.selected))this.selected=artifacts.find(a=>a.name==='assembly-overlay.svg')?.id||artifacts[0]?.id||'';
    this.select.value=this.selected;this.host.hidden=!artifacts.length;this.show();
  }
}

export class ExternalManufacturingPanel {
  private context:Context|null=null;private pid='';private sources:any[]=[];private sourceSignature='';private generation=0;private reportSignature='';
  private drafts=new Map<string,Draft>();private selection=new Map<string,string>();private follow=new Map<string,boolean>();private offsets=new Map<string,number>();
  private history=make('div','','manufacturing-row');private results=make('div');private formHost=make('details');private form=document.createElement('form');private formStatus=make('p','','field-note');private viewer=new CamViewer();
  private busy=false;private submitButton=document.createElement('button');private confirmed=document.createElement('input');
  constructor(private host:HTMLElement,private refresh:()=>Promise<void>,private notify:(text:string,error?:boolean)=>void,private openSources:()=>void){}
  private draft(){return this.drafts.get(this.pid)!;}
  private ref(id:string){const source=this.sources.find(s=>s.id===id);if(!source)throw new Error('资料已变化，请重新选择已登记文件。');return source.bindings.externalManufacturing;}
  private changed(){this.draft().confirmed=false;this.confirmed.checked=false;this.setDisabled();}
  private setDisabled(){if(!this.pid)return;this.submitButton.disabled=this.busy||!this.draft().confirmed||!this.context?.capabilities.externalManufacturing?.available||!!this.context?.jobs.find(j=>['queued','running'].includes(j.status));}
  private onField<T extends HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement>(input:T,update:(value:string)=>void){input.oninput=()=>{update(input.value);this.changed();};return input;}

  async render(context:Context|null){
    this.context=context;if(!context){this.pid='';this.generation++;this.host.replaceChildren(make('p','先选择工程。'));return;}
    const pid=context.project.id,switched=pid!==this.pid;
    if(switched){this.pid=pid;this.generation++;this.sources=[];this.sourceSignature='';this.reportSignature='';if(!this.drafts.has(pid))this.drafts.set(pid,fresh(context.project.version));
      this.history=make('div','','manufacturing-row');this.results=make('div');this.formHost=make('details');this.viewer=new CamViewer();
      this.host.replaceChildren(make('h2','外部制造文件审核'),make('p','独立读取 Gerber ZIP、BOM 和坐标文件。无需原理图或 PCB；文件审核与工程、采购、平台生产审核分别记录。','quiet-note'),this.history,this.results,this.formHost);
    }
    if(this.draft().version!==context.project.version){this.draft().version=context.project.version;this.draft().confirmed=false;this.confirmed.checked=false;this.formStatus.textContent='工程条件已更新，请重新核对后提交。';}
    const sourceSignature=JSON.stringify([pid,context.sourceRegistry?.total,context.sourceRegistry?.sources.map(s=>s.id)]);
    if(sourceSignature!==this.sourceSignature){
      this.sourceSignature=sourceSignature;const token=++this.generation;let all:any[]=[];
      for(let offset=0;offset<300;offset+=100){const page=await api<any>(`projects/${pid}/sources?offset=${offset}&limit=100`);all.push(...page.sources);if(all.length>=page.total)break;}
      if(token!==this.generation||this.pid!==pid)return;this.sources=all;this.buildForm();
    }
    this.setDisabled();
    const jobs=context.jobs.filter(j=>j.kind==='manufacturing.external_review'),following=this.follow.get(pid)!==false;
    const chosen=following?jobs[0]:jobs.find(j=>j.id===this.selection.get(pid))||jobs[0];
    const select=choose(jobs.map(j=>[j.id,`${j.id.slice(-6)} · ${j.status==='completed'?'审核结束':j.status==='running'?'执行中':j.status==='cancelled'?'已取消':j.status==='failed'?'失败':j.status} · ${j.result?.checksPassed===false?'有待处理项':j.result?.checksPassed?'所列检查通过':''}`]),chosen?.id||'');select.setAttribute('aria-label','外部制造审核任务');
    select.onchange=()=>{this.selection.set(pid,select.value);this.follow.set(pid,false);this.offsets.set(pid,0);void this.render(this.context).catch(e=>this.notify(e.message,true));};
    this.history.replaceChildren(label('审核记录',select),button(following?'正在跟随最新任务':'跟随最新任务',()=>{this.follow.set(pid,true);this.offsets.set(pid,0);void this.render(this.context).catch(e=>this.notify(e.message,true));}));
    if(!chosen){this.results.replaceChildren(make('p','登记制造文件后，确认下方映射并开始审核；Agent 也可通过 MCP 提交相同任务。','empty-small'));return;}
    const artifacts=context.artifacts.filter(a=>a.jobId===chosen.id&&a.kind.startsWith('external_cam_'));
    const signature=JSON.stringify([pid,chosen.id,chosen.status,chosen.phase,chosen.result,this.offsets.get(pid)||0,artifacts.map(a=>a.id)]);
    if(signature===this.reportSignature)return;this.reportSignature=signature;const token=++this.generation;
    try{const read=await api<any>(`projects/${pid}/external-manufacturing?jobId=${chosen.id}&offset=${this.offsets.get(pid)||0}&limit=100`);if(token!==this.generation||pid!==this.pid)return;this.renderReport(read);}
    catch(error){if(token===this.generation){this.reportSignature='';this.results.replaceChildren(make('p',(error as Error).message,'connection-problem'));}}
  }

  private renderReport(read:any){
    const job=read.job,result=read.result;this.results.replaceChildren();
    this.results.append(make('p',`${job.id.slice(-8)} · ${job.phase}`,'field-note'));
    if(read.requiresContextReview)this.results.append(make('p','此报告形成后需求或偏好已变化，需要重新核对。','connection-problem'));
    if(result){
      this.results.append(make('p',result.checksPassed?'所列文件检查通过；尚未生产放行':'文件存在待处理项；当前设计保持',result.checksPassed?'notice':'connection-problem'));
      if(result.outline)this.results.append(make('p',`外形 ${Number(result.outline.widthMm.toFixed(4))} × ${Number(result.outline.heightMm.toFixed(4))} mm · ${result.outline.boardIslands} 个板体区域 · ${result.outline.cutouts} 个外形开口`,'field-note'));
      this.results.append(make('p',`${result.fileCount} 份包内文件 · ${result.drillFeatureCount} 个孔/槽 · ${result.selectedReferences} 个选装位号`,'field-note'),make('p','孔与轮廓的曲线几何误差上限为 0.005mm。装配点和箭头来自明确的坐标约定，不代表供应商质心或一脚方向已经核实。','quiet-note'));
      for(const p of result.problems.slice(0,100))this.results.append(make('p',`${p.path||p.reference||''}${p.row?' · 第'+p.row+'行':''} · ${p.message}`,'connection-problem'));
      if(result.problemCount>100)this.results.append(make('p','其余问题见完整报告。','quiet-note'));
      const docs=make('div','','document-downloads');for(const [kind,title] of [['external_cam_package','下载完整审核包 ↗'],['external_cam_report','完整检查报告 ↗'],['external_cam_assembly','装配审核表 ↗']]){const a=read.artifacts.find((a:Artifact)=>a.kind===kind);if(a){const link=make('a',title,'text-button') as HTMLAnchorElement;link.href=fileUrl(a)+'?download=true';docs.append(link);}}this.results.append(docs);
      if(read.sources?.length){const details=make('details');details.append(make('summary',`冻结来源 · ${read.sources.length} 份`));for(const s of read.sources){details.append(make('p',`${s.name} · ${s.version} · ${s.origin}`,'field-note'),make('p',s.sha256,'source-hash'),make('p',s.note,'quiet-note'));}this.results.append(details);}
      if(read.layers?.length){const details=make('details');details.append(make('summary',`文件身份与解析 · ${read.layers.length} 项`));for(const row of read.layers)details.append(make('p',`${row.path} · ${row.parsed?'已解析':'有问题'} · ${row.fileFunction?.join(',')||'身份仅由调用者声明'}${row.objectCount!==undefined?' · '+row.objectCount+' 个对象':''}`,'field-note'));this.results.append(details);}
      if(read.parts?.length){
        const details=make('details');details.append(make('summary',`BOM与坐标 · ${read.offset+1}–${read.offset+read.parts.length} / ${read.partCount}`));
        for(const p of read.parts){const pos=p.placement;details.append(make('p',`${p.reference} · ${p.value||'未声明数值'} · ${p.dnp?'DNP':p.selected?'选装':'明确省略'}${pos?` · ${pos.side==='top'?'正面':'背面'} ${pos.xMm.toFixed(3)}, ${pos.yMm.toFixed(3)} mm / ${pos.rotationDegrees.toFixed(2)}°`:''}`,'field-note'));}
        const page=make('div','','manufacturing-row');page.append(button('上一页',()=>this.page(Math.max(0,read.offset-100))),button('下一页',()=>{if(read.offset+100<read.partCount)this.page(read.offset+100);}));details.append(page);this.results.append(details);
      }
    }else if(job.error)this.results.append(make('p',job.error,'connection-problem'));
    this.viewer.update(job.id,read.artifacts.filter((a:Artifact)=>a.kind==='external_cam_preview'),read.previews||[]);this.results.append(this.viewer.host);
  }
  private page(offset:number){this.offsets.set(this.pid,offset);void this.render(this.context).catch(e=>this.notify(e.message,true));}

  private buildForm(){
    const d=this.draft(),initial=!this.formHost.querySelector('form'),wasOpen=this.formHost.open;
    this.formHost.replaceChildren(make('summary','开始新的文件审核'));this.formHost.open=initial?!this.context?.jobs.some(j=>j.kind==='manufacturing.external_review'):wasOpen;
    this.form=document.createElement('form');this.form.className='external-cam-form';this.formHost.append(this.form);this.formStatus=make('p','','field-note');
    const archive=choose([['','选择已登记ZIP'],...this.sources.filter(s=>s.name.toLowerCase().endsWith('.zip')).map(s=>[s.id,s.name] as [string,string])],d.archive);archive.setAttribute('aria-label','Gerber ZIP资料');
    archive.onchange=()=>{d.archive=archive.value;d.entries=[];d.mapping={};this.changed();this.buildForm();};
    this.form.append(make('p','先在工程资料登记原件及来源，再明确本次使用的文件。原件会保留，审核不会修改设计。','quiet-note'),button('添加或查看工程资料',this.openSources),label('Gerber ZIP',archive),button('读取ZIP文件清单',()=>void this.discover()));
    if(!this.context?.capabilities.externalManufacturing?.available)this.form.append(make('p',this.context?.capabilities.externalManufacturing?.issue||'独立解析组件尚未就绪，请让Agent核对安装状态。','connection-problem'));
    if(d.entries.length){
      const count=this.onField(control(d.count,'number'),v=>{d.count=v;});count.min='1';count.max='32';count.onchange=()=>{this.changed();this.buildForm();};this.form.append(label('已确认的铜层数量',count));
      const list=make('div','','external-cam-mapping');
      for(const entry of d.entries){const item=d.mapping[entry.path];const options:[string,string][]=[['','请选择用途'],...Array.from({length:Math.min(32,Math.max(1,Number(d.count)||1))},(_,i)=>['copper:'+(i+1),'第 '+(i+1)+' 铜层'] as [string,string]),['profile','成品外形'],['drill:plated','镀通钻孔/槽'],['drill:non-plated','非镀通钻孔/槽'],['mask:top','正面阻焊开窗'],['mask:bottom','背面阻焊开窗'],['paste:top','正面锡膏'],['paste:bottom','背面锡膏'],['legend:top','正面丝印'],['legend:bottom','背面丝印'],['job','Gerber作业元数据'],['table','装配表（下方选择）'],['ignore','明确忽略']];
        const select=choose(options,item.role);select.setAttribute('aria-label','文件用途 '+entry.path);select.onchange=()=>{item.role=select.value;this.changed();this.buildForm();};const row=make('div','','external-cam-file');row.append(label(entry.path,select),make('p',entry.fileFunction?'元数据建议：'+entry.fileFunction.join(','):'无明确层身份，请核对原始资料。','quiet-note'));
        if(item.role==='ignore')row.append(label('忽略原因',this.onField(control(item.reason),v=>item.reason=v)));
        if(item.role.startsWith('drill:')){const extra=make('details');extra.append(make('summary','缺少钻孔单位/数字格式时补充'));const unit=choose([['','使用文件内明确声明'],['mm','毫米'],['inch','英寸']],item.unit);this.onField(unit,v=>item.unit=v);extra.append(label('补充单位',unit),label('整数位',this.onField(control(item.integer,'number'),v=>item.integer=v)),label('小数位',this.onField(control(item.decimal,'number'),v=>item.decimal=v)),label('省略的零位',this.onField(choose([['leading','前导零'],['trailing','尾随零']],item.zeros),v=>item.zeros=v)));row.append(extra);}
        list.append(row);
      }this.form.append(list);
    }
    const mode=this.onField(choose([['pcb-only','仅检查裸板文件'],['assembly','同时检查BOM与装配坐标']],d.mode),v=>d.mode=v);mode.onchange=()=>{d.mode=mode.value;this.changed();this.buildForm();};this.form.append(label('审核范围',mode));
    if(d.mode==='assembly'){
      this.form.append(this.tableForm('bom','BOM器件清单'),this.tableForm('cpl','装配坐标'));
      this.form.append(make('p','位号可按逗号、分号或空格拆分，并展开同前缀范围（如R1-R3）。每个位号每板1件；选择数量列时必须与位号数量相符。','quiet-note'),label('封装名称核对',this.onField(choose([['exact','文本完全相同'],['name-after-colon','忽略库名前缀，只比较封装名称']],d.footprintComparison),v=>d.footprintComparison=v)));
      const dnp=this.onField(choose([['column','按明确DNP列'],['none-declared','明确声明没有DNP']],d.dnp),v=>d.dnp=v);this.form.append(label('不装件规则',dnp),label('DNP规则说明',this.onField(control(d.dnpReason),v=>d.dnpReason=v)),label('表示不装的列值（逗号分隔）',this.onField(control(d.dnpTrue),v=>d.dnpTrue=v)),label('表示装配的列值（首个逗号表示空白）',this.onField(control(d.dnpFalse),v=>d.dnpFalse=v)));
      this.form.append(label('坐标单位',this.onField(choose([['mm','毫米'],['inch','英寸'],['mil','mil']],d.unit),v=>d.unit=v)),label('正面列值（逗号分隔）',this.onField(control(d.topValues),v=>d.topValues=v)),label('背面列值（逗号分隔）',this.onField(control(d.bottomValues),v=>d.bottomValues=v)),this.transformForm('top','正面坐标约定'),this.transformForm('bottom','背面坐标约定'));
      for(const [key,title] of [['excluded','明确省略的位号'],['outside','允许锚点在板外的位号']]){const field=document.createElement('textarea');field.value=(d as any)[key];field.placeholder='每行：位号 = 原因';this.onField(field,v=>(d as any)[key]=v);this.form.append(label(title,field));}
    }
    this.form.append(label('计划板数（可空，保持未明确）',this.onField(control(d.quantity,'number'),v=>d.quantity=v)),label('审核说明',this.onField(control(d.message),v=>d.message=v)));
    this.confirmed=control('','checkbox');this.confirmed.checked=d.confirmed;this.confirmed.onchange=()=>{d.confirmed=this.confirmed.checked;this.setDisabled();};this.form.append(label('已核对文件用途、单位、DNP及正反面坐标约定',this.confirmed));
    this.submitButton=make('button','开始文件审核','button primary') as HTMLButtonElement;this.submitButton.type='submit';this.form.append(this.formStatus,this.submitButton);this.form.onsubmit=event=>{event.preventDefault();void this.submit();};this.setDisabled();
  }

  private async discover(){
    const pid=this.pid,d=this.draft();this.busy=true;this.setDisabled();
    try{const response=await api<any>(`projects/${pid}/external-manufacturing-schema`,'POST',{archive:this.ref(d.archive)});d.entries=response.archive.entries;d.mapping={};
      for(const entry of d.entries){const m=entry.suggestedMapping;const role=m?.role==='copper'?'copper:'+m.copperLayer:m?.role==='drill'?'drill:'+m.plating:m?.side?m.role+':'+m.side:m?.role||(/\.(csv|xlsx)$/i.test(entry.path)?'table':'');d.mapping[entry.path]={role,reason:'',unit:'',integer:'',decimal:'',zeros:'leading'};}
      d.count=String(Math.max(1,...d.entries.map(e=>e.suggestedMapping?.copperLayer||0)));d.confirmed=false;if(pid===this.pid)this.buildForm();
    }catch(error){if(pid===this.pid)this.notify((error as Error).message,true);}finally{this.busy=false;this.setDisabled();}
  }
  private tableOptions():[string,string][]{return [['','选择表格'],...this.sources.filter(s=>/\.(csv|xlsx)$/i.test(s.name)).map(s=>['source:'+s.id,s.name] as [string,string]),...this.draft().entries.filter(e=>/\.(csv|xlsx)$/i.test(e.path)).map(e=>['zip:'+e.path,'ZIP内 · '+e.path] as [string,string])];}
  private tableForm(key:'bom'|'cpl',title:string){
    const d=this.draft(),t=d[key];const host=make('fieldset');host.append(make('legend',title));const select=choose(this.tableOptions(),t.key);select.onchange=()=>{t.key=select.value;t.headers=[];t.sheet='';t.sheets=[];this.changed();this.buildForm();};host.append(label('原始表格',select));
    if(t.key){const name=t.key.startsWith('source:')?this.sources.find(s=>s.id===t.key.slice(7))?.name:t.key.slice(4);t.format=/\.xlsx$/i.test(name||'')?'xlsx':'csv';
      if(t.format==='xlsx'){const sheet=choose([['','先读取工作表'],...t.sheets.map(s=>[s,s] as [string,string])],t.sheet);this.onField(sheet,v=>{t.sheet=v;t.headers=[];});host.append(label('工作表',sheet));}
      else host.append(label('文字编码',this.onField(choose([['utf-8-sig','UTF-8'],['gb18030','中文GB18030'],['utf-16','UTF-16']],t.encoding),v=>t.encoding=v)),label('分隔符',this.onField(choose([[',','逗号'],[';','分号'],['\t','制表符']],t.delimiter),v=>t.delimiter=v)));
      host.append(label('表头行号',this.onField(control(t.headerRow,'number'),v=>t.headerRow=v)),button('读取'+title+'表头',()=>void this.probeTable(key)));
      const fields=key==='bom'?[['reference','位号列'],['quantity','每板数量列'],['dnp','DNP列'],['value','数值列（可空）'],['footprint','封装列（可空）'],['mpn','制造商料号列（可空）'],['supplierPart','供应商编码列（可空）']]:[['reference','位号列'],['x','X坐标列'],['y','Y坐标列'],['rotation','角度列'],['side','装配面列'],['value','数值列（可空）'],['footprint','封装列（可空）']];
      for(const [field,text] of fields)host.append(label(text,this.onField(choose([['','不使用'],...t.headers.map(h=>[h,h] as [string,string])],t.columns[field]||''),v=>t.columns[field]=v)));
    }return host;
  }
  private location(t:TableDraft){if(!t.key)throw new Error('请选择BOM和坐标表。');return {source:t.key.startsWith('source:')?this.ref(t.key.slice(7)):null,archivePath:t.key.startsWith('zip:')?t.key.slice(4):null,format:t.format,encoding:t.encoding,delimiter:t.delimiter,headerRow:num(t.headerRow),sheet:t.sheet||null};}
  private async probeTable(key:'bom'|'cpl'){
    const pid=this.pid,d=this.draft(),t=d[key];this.busy=true;this.setDisabled();
    try{const location=this.location(t);const read=await api<any>(`projects/${pid}/external-manufacturing-schema`,'POST',{archive:location.archivePath?this.ref(d.archive):null,table:location});t.headers=read.table.headers;t.sheets=read.table.sheets||[];
      const aliases:Record<string,string[]>={reference:['Reference','Ref','Designator','Designators'],quantity:['Quantity','Qty','QTY'],dnp:['DNP','DNP?'],value:['Value','Val','Comment'],footprint:['Footprint','Package'],mpn:['MPN','Manufacturer Part Number'],supplierPart:['LCSC','LCSC Part #'],x:['PosX','Mid X','X'],y:['PosY','Mid Y','Y'],rotation:['Rot','Rotation'],side:['Side','Layer']};
      const allowed=key==='bom'?['reference','quantity','dnp','value','footprint','mpn','supplierPart']:['reference','x','y','rotation','side','value','footprint'];
      for(const field of allowed)if(!t.headers.includes(t.columns[field]))t.columns[field]=t.headers.find(h=>aliases[field].some(name=>name.toLowerCase()===h.toLowerCase()))||'';
      d.confirmed=false;if(pid===this.pid)this.buildForm();
    }catch(error){if(pid===this.pid)this.notify((error as Error).message,true);}finally{this.busy=false;this.setDisabled();}
  }
  private transformForm(key:'top'|'bottom',title:string){
    const t=this.draft()[key],host=make('details');host.append(make('summary',title));const fields=make('div','','external-transform');
    fields.append(label('本文件X正方向',this.onField(choose([['1','图中向右'],['-1','图中向左']],String(t.xSign)),v=>t.xSign=Number(v))),label('本文件Y正方向',this.onField(choose([['1','图中向上'],['-1','图中向下']],String(t.ySign)),v=>t.ySign=Number(v))));
    for(const [field,name] of [['offsetXmm','原点平移X / mm'],['offsetYmm','原点平移Y / mm'],['frameRotationDegrees','坐标整体旋转 / °'],['sourceAngleZeroDegrees','零角度方向补偿 / °']])fields.append(label(name,this.onField(control(String(t[field]),'number'),v=>t[field]=v)));
    fields.append(label('源表角度正方向',this.onField(choose([['ccw','逆时针'],['cw','顺时针']],t.sourceAngleDirection),v=>t.sourceAngleDirection=v)));host.append(fields);return host;
  }
  private reasons(text:string){return text.split('\n').filter(v=>v.trim()).map(line=>{const index=line.indexOf('=');if(index<1||!line.slice(index+1).trim())throw new Error('位号说明请使用“位号 = 原因”。');return {reference:line.slice(0,index).trim(),reason:line.slice(index+1).trim()};});}
  private async submit(){
    const pid=this.pid,d=this.draft();this.busy=true;this.setDisabled();
    try{
      if(!d.confirmed||!d.entries.length)throw new Error('请读取文件清单并确认映射。');const layers:any[]=[],ignoredFiles:any[]=[];
      for(const entry of d.entries){const m=d.mapping[entry.path];const [role,detail]=m.role.split(':');if(!role)throw new Error('请选择全部文件的用途。');if(role==='table')continue;if(role==='ignore'){if(!m.reason.trim())throw new Error('请填写忽略原因。');ignoredFiles.push({path:entry.path,reason:m.reason});continue;}
        const row:any={path:entry.path,role};if(role==='copper')row.copperLayer=num(detail);if(['mask','paste','legend'].includes(role))row.side=detail;if(role==='drill'){row.plating=detail;if(m.unit)row.drillFormat={unit:m.unit,integerDigits:m.integer?num(m.integer):null,decimalDigits:m.decimal?num(m.decimal):null,zerosSuppressed:m.integer?m.zeros:null};}layers.push(row);
      }
      const request:any={expectedProjectVersion:d.version,archive:this.ref(d.archive),copperLayerCount:num(d.count),layers,ignoredFiles,drillDeclaration:layers.some(l=>l.role==='drill')?'files-listed':'none-declared',drillDeclarationReason:layers.some(l=>l.role==='drill')?'已明确选择本次使用的全部钻孔文件。':'已核对本包没有独立钻孔文件；未证明外部设计绝无其他孔。',assemblyMode:d.mode,boardQuantity:d.quantity?num(d.quantity):null,message:d.message,footprintComparison:d.footprintComparison};
      if(d.mode==='assembly'){
        if(!d.bom.headers.length||!d.cpl.headers.length)throw new Error('请先读取两份表格的表头并确认列映射。');const mapped=(t:TableDraft)=>Object.fromEntries(Object.entries(t.columns).filter(([_,v])=>v));const cols=mapped(d.bom);if(d.dnp==='none-declared')delete cols.dnp;
        request.bom={file:this.location(d.bom),columns:cols,quantityMode:cols.quantity?'column':'one-per-reference',referenceSeparator:'combined',expandRanges:true,dnpMode:d.dnp,dnpDeclaration:d.dnpReason,dnpTrueValues:d.dnpTrue.split(','),dnpFalseValues:d.dnpFalse.split(',')};
        const readTransform=(t:any)=>({...t,...Object.fromEntries(['offsetXmm','offsetYmm','frameRotationDegrees','sourceAngleZeroDegrees'].map(k=>[k,num(String(t[k]))]))});
        request.placements={file:this.location(d.cpl),columns:mapped(d.cpl),unit:d.unit,topValues:d.topValues.split(','),bottomValues:d.bottomValues.split(','),topTransform:readTransform(d.top),bottomTransform:readTransform(d.bottom)};request.excludedReferences=this.reasons(d.excluded);request.outsidePlacementExceptions=this.reasons(d.outside);
      }
      const fingerprint=JSON.stringify(request);if(d.pending?.fingerprint!==fingerprint)d.pending={fingerprint,id:requestId()};
      await api(`projects/${pid}/external-manufacturing-reviews`,'POST',{...request,requestId:d.pending!.id});d.pending=undefined;this.follow.set(pid,true);this.offsets.set(pid,0);this.notify('外部文件审核已开始；原始资料和设计保持。');await this.refresh();
    }catch(error){if(pid===this.pid){this.formStatus.textContent=(error as Error).message;this.notify((error as Error).message,true);}}finally{this.busy=false;this.setDisabled();}
  }
}
