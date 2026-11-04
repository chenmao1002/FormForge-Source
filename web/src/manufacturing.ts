import {api,fileUrl,requestId,type Context,type Job} from './api';
import {translateText} from './i18n';

const make=(tag:string,cls='',text='')=>{const e=document.createElement(tag);e.className=cls;e.textContent=text;return e;};
const states:Record<string,string>={queued:'等待执行',running:'正在准备',completed:'资料任务结束',failed:'任务失败',cancelled:'已取消',interrupted:'已中断'};
type Draft={revision:string;scope:string;sides:string;quantity:string;message:string};

export class ManufacturingPanel {
  private generation=0;
  private signature='';
  private selected=new Map<string,string>();
  private follow=new Map<string,boolean>();
  private drafts=new Map<string,Draft>();
  constructor(private host:HTMLElement,private refresh:()=>Promise<void>,private notify:(text:string,error?:boolean)=>void){}

  async render(context:Context|null){
    if(!context){this.signature='';this.generation++;this.host.replaceChildren(make('p','','先选择工程。'));return;}
    const pid=context.project.id,jobs=context.jobs.filter(j=>j.kind==='manufacturing.prepare');
    const following=this.follow.get(pid)!==false;
    const chosen=following?jobs[0]:jobs.find(j=>j.id===this.selected.get(pid))||jobs[0];
    const signature=JSON.stringify([pid,context.project.version,context.project.headRevisionId,chosen?.id,chosen?.status,chosen?.phase,chosen?.result,following,context.jobs.map(j=>[j.id,j.status,j.phase]),context.artifacts.filter(a=>a.kind.startsWith('manufacturing_')).map(a=>a.id)]);
    if(signature===this.signature)return;this.signature=signature;
    const token=++this.generation;
    let read:any=null;
    try{if(chosen)read=await api(`projects/${pid}/manufacturing?jobId=${encodeURIComponent(chosen.id)}`);}
    catch(error){if(token===this.generation){this.signature='';this.host.replaceChildren(make('p','connection-problem',(error as Error).message));}return;}
    if(token!==this.generation)return;
    this.host.replaceChildren();
    this.host.append(make('h2','','制造审核资料'),make('p','quiet-note','从固定原图与PCB版本生成文件。资料一致性、工程资格、采购和平台预览分别核对；尚未提供报价、提交或付款。'));
    const toolbar=make('div','revision-toolbar'),select=document.createElement('select');select.setAttribute('aria-label','制造资料场景');
    for(const job of jobs){const option=document.createElement('option');option.value=job.id;option.textContent=`${job.id.slice(-6)} · ${states[job.status]||job.status} · ${(job as any).params?.message||'制造资料'}`;select.append(option);}
    if(chosen)select.value=chosen.id;
    select.disabled=!jobs.length;select.onchange=()=>{this.selected.set(pid,select.value);this.follow.set(pid,false);this.signature='';void this.render(context);};
    const follow=make('button','text-button',following?'正在跟随后续任务':'跟随后续任务') as HTMLButtonElement;
    follow.onclick=()=>{this.follow.set(pid,true);this.signature='';void this.render(context);};toolbar.append(select,follow);this.host.append(toolbar);
    if(read){
      const job:Job=read.job,result=read.result;
      this.selected.set(pid,job.id);
      const heading=make('div','card-heading');heading.append(make('b','',`${states[job.status]||job.status} · ${job.id.slice(-6)}`),make('span','badge '+(result?.fileConsistencyPassed?'success':'warning'),result?.fileConsistencyPassed?'资料一致性通过':result?'资料需要处理':'尚无检查结论'));this.host.append(heading);
      this.host.append(make('p','field-note',job.phase));
      this.host.append(make('code','library-digest',`输入版本 ${job.inputRevision}`));
      if(job.error)this.host.append(make('pre','firmware-error',typeof job.error==='string'?job.error:JSON.stringify(job.error)));
      if(read.requiresDependencyReview)this.host.append(make('p','connection-problem','设计版本或需求已变化；本场景仍属于原输入，继续使用前需重新核对。'));
      if(result){
        this.host.append(make('p','connection-problem','仅供审核 · 尚未生产放行'));
        const n=result.counts,quality=result.nativeChecks;
        this.host.append(make('p','',`${n.electrical} 个电气器件 · ${n.boardOnly} 个原生机械对象 · 原始BOM ${n.bom} 项 · 原始坐标 ${n.positions} 项`),
          make('p','field-note',`本场景选择 ${n.selected} 个器件，省略 ${n.omitted} 项；数量 ${result.binding.parameters.boardQuantity??'未明确'}。`),
          make('p','field-note',quality.ercErrors===undefined
            ?`原生检查：ERC ${quality.ercFindings} 项 · DRC ${quality.drcFindings} 项（警告 ${quality.drcWarnings}） · 未连 ${quality.unconnected}。`
            :`原生检查：ERC 错误 ${quality.ercErrors} / 警告 ${quality.ercWarnings} / 已排除 ${quality.ercExcluded} · DRC 错误 ${quality.drcErrors} / 警告 ${quality.drcWarnings} / 已排除 ${quality.drcExcluded} · 未连 ${quality.unconnected} · 原图差异 ${quality.schematicParityChecked?quality.schematicParityFindings:'未检查'}。`),
          make('p','field-note',`完整引脚连接：${result.boardGraphHash===result.schematicGraphHash?'匹配':'需处理'}。坐标已按板原点、毫米、Y轴方向及背面角度核对，供应商装配方向另审。`));
        const fabrication=result.binding.parameters.fabrication||{};
        this.host.append(make('p','field-note',`此资料包工艺：板材 ${fabrication.material??'待定'} · 板厚 ${fabrication.thicknessMm??'待定'} mm · 表面处理 ${fabrication.finish??'待定'} · 阻焊 ${fabrication.solderMask??'待定'}。供应商可生产性另行复核。`));
        for(const problem of result.problems)this.host.append(make('p','connection-problem',`${problem.reference?problem.reference+' · ':''}${problem.message}`));
        const pending=make('details') as HTMLDetailsElement;pending.open=true;pending.append(make('summary','','未完成的工程与采购审核'));
        for(const note of result.pending)pending.append(make('p','field-note',note));
        if(result.missingProcurement.length)pending.append(make('p','field-note',`采购料号未齐：${result.missingProcurement.join('、')}`));
        this.host.append(pending);
        const list=make('details');list.append(make('summary','','本场景器件与省略原因'));
        for(const part of result.selectedParts.slice(0,200))list.append(make('p','field-note',`${part.reference} · ${part.value} · ${part.mounting} · ${part.layer} · 装配方向/采购未审核`));
        if(result.selectedParts.length>200)list.append(make('p','field-note','更多器件见完整报告。'));
        for(const omission of result.omissions.slice(0,200))list.append(make('p','field-note',`${omission.reference} · 省略：${omission.reason}`));this.host.append(list);
        const docs=make('div','document-downloads');
        for(const [kind,title] of [['manufacturing_project','下载审核资料包 ↗'],['manufacturing_report','下载完整预检报告 ↗'],['manufacturing_gerbers','Gerber与钻孔包（审核用）↗'],['manufacturing_bom','原始BOM ↗'],['manufacturing_positions','原始坐标 ↗'],['manufacturing_assembly_review','装配审核表 ↗'],['manufacturing_drc','原生DRC ↗'],['manufacturing_erc','原生ERC ↗']]){
          const artifact=context.artifacts.find(a=>a.projectId===pid&&a.jobId===job.id&&a.revisionId===job.inputRevision&&a.kind===kind);
          if(artifact){const link=make('a','text-button',title) as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';docs.append(link);}
        }
        this.host.append(docs,make('code','library-digest',`PCB SHA-256 ${result.boardSha256}`));
      }
    }else this.host.append(make('p','empty-small','还没有制造资料任务。可由Agent通过MCP提交，或在下面明确审核范围。'));
    this.form(context);
  }

  private form(context:Context){
    const pid=context.project.id;
    const revisions=context.revisions.filter(r=>(r.module||'eda')==='eda'&&r.entrypoints.board&&r.entrypoints.schematic
      &&(r.pcbDesign||r.entrypoints.board.replace(/\.kicad_pcb$/u,'.kicad_sch')===r.entrypoints.schematic));
    if(!revisions.length){this.host.append(make('p','field-note','此入口需要可追溯的原图与PCB。已有外部Gerber/BOM/坐标可切换到外部文件审核。'));return;}
    const moduleDefaults={...context.project.preferences.personalDefaultsSnapshot?.moduleDefaults?.manufacturing,...context.project.preferences.moduleDefaults?.manufacturing};
    const draft=this.drafts.get(pid)||{revision:context.project.headRevisionId||revisions[0].id,scope:'',sides:'',quantity:String(moduleDefaults.boardQuantity??''),message:translateText('固定版本制造资料审核')};this.drafts.set(pid,draft);
    if(!revisions.some(r=>r.id===draft.revision))draft.revision=revisions[0].id;
    const form=document.createElement('form');form.className='manufacturing-form';form.append(make('h3','','准备新的审核资料'));
    const choose=(label:string,items:[string,string][],value:string,onchange:(v:string)=>void)=>{
      const field=make('label','field-label',label),select=document.createElement('select');
      for(const [id,title] of items){const option=document.createElement('option');option.value=id;option.textContent=title;select.append(option);}
      select.value=value;select.required=true;select.onchange=()=>onchange(select.value);field.append(select);form.append(field);return select;
    };
    const revision=choose('设计版本',revisions.map(r=>[r.id,`${r.id.slice(-6)} · ${r.id===context.project.headRevisionId?'当前版本':'历史/候选'} · ${r.message||r.origin||''}`]),draft.revision,v=>draft.revision=v);
    const scope=choose('审核装配范围',[['','请选择'],['pcb-only','仅PCB资料'],['smd-only','仅SMD装配'],['all-electrical','全部电气器件（包含插件）']],draft.scope,v=>{draft.scope=v;side.disabled=v==='pcb-only';side.required=v!=='pcb-only';});
    const side=choose('装配面',[['','请选择'],['front','正面'],['back','背面'],['both','双面']],draft.sides,v=>draft.sides=v);
    side.disabled=draft.scope==='pcb-only';side.required=!side.disabled;
    const label=make('label','field-label','数量（可留空；未明确时仅作审核资料）'),quantity=document.createElement('input');quantity.type='number';quantity.min='1';quantity.max='1000000';quantity.step='1';quantity.value=draft.quantity;quantity.oninput=()=>draft.quantity=quantity.value;label.append(quantity);form.append(label);
    const messageLabel=make('label','field-label','审核说明'),message=document.createElement('input');message.required=true;message.maxLength=2000;message.value=draft.message;message.oninput=()=>draft.message=message.value;messageLabel.append(message);form.append(messageLabel);
    const button=make('button','primary-button','生成审核资料') as HTMLButtonElement;button.type='submit';button.disabled=context.jobs.some(j=>['queued','running'].includes(j.status))||!context.capabilities.manufacturingPreparation?.available;
    form.append(make('p','field-note','保留原始BOM和双面坐标，按所选范围另出审核表。工艺/料号/方向未核对，不上传平台或下单。'),button);
    form.onsubmit=async event=>{
      event.preventDefault();button.disabled=true;
      try{
        const input=context.revisions.find(r=>r.id===revision.value)!;
        const hash=input.manifest.find(f=>f.path===input.entrypoints.board)!.sha256;
        const job=await api(`projects/${pid}/manufacturing-preparations`,'POST',{
          expectedProjectVersion:context.project.version,revisionId:input.id,expectedBoardHash:hash,
          ...(!input.pcbDesign?{inputScope:'complete-native-import'}:{}),
          assemblyScope:scope.value,assembledSides:scope.value==='pcb-only'?[]:side.value==='both'?['F.Cu','B.Cu']:side.value==='front'?['F.Cu']:['B.Cu'],
          boardQuantity:quantity.value?Number(quantity.value):null,message:message.value,requestId:requestId()});
        this.selected.set(pid,job.id);this.follow.set(pid,true);this.signature='';this.notify('已开始准备制造审核资料。');await this.refresh();
      }catch(error){this.notify((error as Error).message,true);button.disabled=false;}
    };
    this.host.append(form);
  }
}
