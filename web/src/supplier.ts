import {api,requestId,type Context} from './api';
import {locale} from './i18n';
const make=(tag:string,text='',cls='')=>{const e=document.createElement(tag);e.textContent=text;e.className=cls;return e;};
const originalReview=(text:string)=>{const p=make('p',text,'field-note');p.translate=false;return p;};
const when=(value:string)=>new Date(value).toLocaleString(locale(),{hour12:false});
const states:Record<string,string>={'not-checked':'登录尚未核实','authenticated':'已检测到登录','login-required':'需要登录','browser-closed':'浏览器已关闭'};

export class SupplierPanel{
  private generation=0;
  private busy=false;
  private context:Context|null=null;
  private selected=new Map<string,string>();
  private expanded=new Set<string>();
  constructor(private host:HTMLElement,private notify:(text:string,error?:boolean)=>void){}
  async render(context:Context|null){
    this.context=context;
    const generation=++this.generation;
    try{
      const session=await api('supplier/session');if(generation!==this.generation)return;
      this.host.replaceChildren(make('h2','本地下单执行器'),make('p','通过 FormForge 专用浏览器操作嘉立创。首次在新窗口登录，之后尝试复用保存的会话。','field-note'));
      const status=make('div','','supplier-session-status');status.append(make('strong',states[session.loginStatus]||session.loginStatus),make('span',session.accountLabel||session.cachedAccountLabel||'尚未核实账号','field-note'));this.host.append(status);
      this.host.append(make('p',session.cacheSaved?`本机登录缓存已保存${session.savedAt?' · '+when(session.savedAt):''}；是否仍然有效以平台检查为准。`:'还没有保存登录缓存。','field-note'));
      if(session.issue)this.host.append(make('p',session.issue,'connection-problem'));
      const buttons=make('div','','document-downloads');
      for(const [label,action,disabled] of [['打开登录页面','open-login',!session.available],['检查登录状态','check',!session.browserRunning],['保存登录缓存','save',!session.browserRunning||session.loginStatus!=='authenticated'],['关闭浏览器','close',!session.browserRunning],['清除本机会话','forget',!session.browserRunning&&!session.cacheSaved]] as [string,string,boolean][]){
        const button=make('button',label,'text-button') as HTMLButtonElement;button.disabled=this.busy||disabled;
        button.onclick=async()=>{if(action==='forget'&&!window.confirm('清除此电脑中 FormForge 专用浏览器的登录会话和缓存？下次需要重新登录。'))return;
          this.busy=true;for(const b of buttons.querySelectorAll('button'))b.disabled=true;
          try{await api('supplier/session','POST',{action,requestId:requestId()});this.notify(action==='open-login'?'已打开专用浏览器，请在新窗口登录。':'会话状态已更新。');}
          catch(error){this.notify((error as Error).message,true);}finally{this.busy=false;await this.render(this.context);}
        };buttons.append(button);
      }
      this.host.append(buttons,make('p','密码、短信验证码和扫码操作请在专用浏览器完成。登录快照按当前 Windows 账号加密保存，Agent 只能读取状态。','field-note'));
      if(!session.available)this.host.append(make('p','需要本机 Edge 或 Chrome，以及浏览器执行依赖；请运行环境安装脚本。','connection-problem'));
      const attempts=make('div','','supplier-attempts');this.host.append(attempts);
      if(context){await this.renderApprovals(attempts,context,generation);if(generation!==this.generation)return;await this.renderProcurement(attempts,context,generation);if(generation!==this.generation)return;await this.renderAttempts(attempts,context,generation);}
      else attempts.append(make('p','选择工程后查看下单执行记录。','field-note'));
    }catch(error){if(generation===this.generation)this.host.replaceChildren(make('p',(error as Error).message,'connection-problem'));}
  }

  private async renderProcurement(host:HTMLElement,context:Context,generation:number){
    const state=await api<any>('procurement/browser');if(generation!==this.generation)return;
    const panel=make('section','','supplier-attempts');panel.append(make('h3','后台采购与审核'));
    panel.append(make('p',state.available?`${state.browserMode==='headless'?'无头运行':'可见或最小化窗口'} · ${state.mode==='human'?'人工接管':state.busy?'正在执行':'空闲'}`:'独立采购执行器尚未启动。','field-note'));
    const refresh=make('button','刷新后台状态','text-button');refresh.onclick=()=>void this.render(this.context);panel.append(refresh);
    if(state.available)for(const [label,action] of [['登录 / 人工接管','handoff'],['交回后台执行','resume']]){
      const b=make('button',label,'text-button') as HTMLButtonElement;b.disabled=state.busy;
      b.onclick=async()=>{b.disabled=true;try{await api('procurement/browser','POST',{action,requestId:requestId()});await this.render(this.context);}catch(error){this.notify((error as Error).message,true);b.disabled=false;}};panel.append(b);
    }
    const names:Record<string,string>={scheduled:'等待审核，下次自动查询',running:'正在查询',ready:'审核状态已变化',failed:'查询暂停，需要处理'};
    for(const r of (state.reviews||[]).filter((r:any)=>r.projectId===context.project.id)){
      panel.append(make('p',`工程版本 ${r.projectVersion} · ${names[r.status]||r.status}${r.nextCheckAt?' · '+when(new Date(r.nextCheckAt).toISOString()):''}`,'field-note'));
      if(r.error)panel.append(make('p',r.error,'connection-problem'));
      if(r.status==='failed'){
        const retry=make('button','重试审核查询','text-button') as HTMLButtonElement;retry.disabled=state.busy;
        retry.onclick=async()=>{retry.disabled=true;try{await api('procurement/browser','POST',{action:'review-retry',orderBinding:r.binding,requestId:requestId()});await this.render(this.context);}catch(error){this.notify((error as Error).message,true);retry.disabled=false;}};panel.append(retry);
      }
      if(r.result?.requiresResume)panel.append(make('p','审核结果已更新；Agent核对资料并重新复核后继续。','field-note'));
      if(r.result?.awaitingPaymentObserved)panel.append(make('p',`${r.result.smtOrderNumber} 已待付款，SMT金额 ${r.result.assemblyTotal} 元。`));
    }
    host.append(panel);
    const print=state.printSubmission;
    const printTask=state.printTask;
    if(printTask?.projectId===context.project.id){
      const task=make('section','','supplier-attempts');
      task.append(make('p',`3D后台任务：${printTask.status==='running'?'正在执行':printTask.status==='completed'?'已返回结果':printTask.status==='interrupted'?'已中断':printTask.status==='failed'?'执行失败':printTask.status}`,'field-note'));
      if(printTask.error)task.append(make('p',printTask.error.message,'connection-problem'));
      host.append(task);
    }
    if(print?.projectId===context.project.id){
      const phases:Record<string,string>={'new':'尚未准备','quoted':'报价已核对','checkout-preparing':'正在准备结算','checkout-prepared':'结算已核对，尚未提交','submission-started':'正在核对提交结果','submitted-awaiting-observation':'平台已响应，待独立查单','result-needs-reconciliation':'需要核对平台结果','submitted-unpaid-observed':'已核对未付款订单'};
      const status=make('section','','supplier-attempts');status.append(make('h3','3D下单状态'),make('p',`${print.parts}种零件，每种${print.copiesPerPart}件 · ${phases[print.phase]||print.phase}`));
      if(print.orderNumbers?.length)status.append(make('p',`订单：${print.orderNumbers.join('、')}`));
      if(print.payableCny!=null)status.append(make('p',`应付金额 ${print.payableCny} 元；未执行付款。`,'field-note'));
      if(print.quoteExcludingTaxCny!=null)status.append(make('p',`未税报价 ${print.quoteExcludingTaxCny} 元。`,'field-note'));
      host.append(status);
    }
  }

  private async renderApprovals(host:HTMLElement,context:Context,generation:number){
    const approvals=await api<any[]>(`procurement/approvals?projectId=${encodeURIComponent(context.project.id)}`);
    if(generation!==this.generation)return;
    const policy=await api<any>('procurement/browser/schema');if(generation!==this.generation)return;
    if(policy.submissionReviewMode==='agent-review'){
      const heading=make('h3','Agent复核与待付款提交');heading.id='submission-approvals';host.append(heading,make('p','默认由Agent完成对抗性复核后直接提交至待付款，无需点击确认卡。付款和生产放行不自动执行；供应商备注不填写、不修改。','field-note'));
      const refresh=make('button','刷新执行状态','text-button');refresh.onclick=()=>void this.render(this.context);host.append(refresh);
      const records=approvals.filter(a=>a.decisionSource==='agent-review').sort((a,b)=>Date.parse(b.createdAt)-Date.parse(a.createdAt)).slice(0,5);
      if(!records.length)host.append(make('p','等待Agent完成复核并执行；实际结果见后台任务和平台订单回执。','field-note'));
      for(const a of records){const card=make('section','','supplier-attempts');const scope=a.intent.kind==='3d-print'?`3D ${a.intent.printOrder.files.length}种零件 × ${a.intent.printOrder.copiesPerPart}件`:`PCB ${a.intent.quantity}片 / SMT ${a.intent.assemblyQuantity??a.intent.quantity}片`;card.append(make('strong',`工程版本 ${a.intent.projectVersion} · ${scope}`),make('small','复核原文','field-note'),originalReview(a.review.summary),make('p',`复核已记录 · ${a.review.evidence.length}份证据；已授权本次待付款提交，实际订单状态另列。`,'field-note'));host.append(card);}
      return;
    }
    const heading=make('h3','提交至待付款：用户确认');heading.id='submission-approvals';
    host.append(heading,make('p','Agent先准备本次提交资料；请核对工程、工艺、物料和文件后确认。确认允许本次PCB及SMT提交，不包含付款或生产放行。','field-note'));
    const reload=make('button','刷新提交确认','text-button');reload.onclick=()=>void this.render(this.context);host.append(reload);
    const current=approvals.filter(a=>['pending','approved'].includes(a.status)).sort((a,b)=>Date.parse(b.createdAt)-Date.parse(a.createdAt)).slice(0,5);
    if(!current.length)host.append(make('p','暂无待确认的提交。','field-note'));
    for(const a of current){
      const expired=Date.parse(a.expiresAt)<=Date.now(),assembled=a.intent.assemblyQuantity??a.intent.quantity;
      const card=make('section','','supplier-attempts');card.style.overflowWrap='anywhere';card.style.minWidth='0';
      card.append(make('strong',`${a.projectName} · 工程版本 ${a.intent.projectVersion} · PCB ${a.intent.quantity}片 / SMT ${assembled}片`),
        make('p',`范围：双层1.6mm、顶面；${a.intent.assemblyScope==='smd-only'?'仅SMT贴片，通孔件自行焊接':'按列出的全部物料装配'}。不填写或修改供应商备注；平台最终费用及DFM仍待核对。`,'field-note'),
        make('p',`状态：${expired?'已过期，需重新准备提交确认':a.intent.projectVersion!==context.project.version?'旧工程版本，不能提交':a.status==='approved'?'已确认，等待Agent执行':'等待用户确认'} · 有效至 ${when(a.expiresAt)}`,'field-note'));
      if(a.intent.board)card.append(make('p',`板框 ${a.intent.board.widthMm} × ${a.intent.board.heightMm} mm · ${a.intent.board.layers} 层 · ${a.intent.board.thicknessMm} mm`,'field-note'));
      if(a.priorSubmissionAttempt)card.append(make('p','此资料已有提交尝试。请先核对平台已有订单；仅在确认恢复范围后继续。','connection-problem'));
      card.append(make('p',`装配清单：${a.intent.parts.length}组 · 每板${a.intent.parts.reduce((sum:number,p:any)=>sum+p.perBoard,0)}个器件。`,'field-note'));
      const details=document.createElement('details');details.open=false;details.append(make('summary','展开核对文件与逐项物料'));
      for(const [label,file] of [['Gerber',a.intent.gerber],['BOM/CPL 1',a.intent.files[0]],['BOM/CPL 2',a.intent.files[1]]] as [string,any][]){details.append(make('p',`${label}: ${file.path}`,'field-note'),make('p',`SHA-256 ${file.sha256}`,'artifact-digest'));}
      for(const p of a.intent.parts)details.append(make('p',`${p.designators.join(', ')} · ${p.supplierCode} · ${p.mpn} · 每板${p.perBoard} · ${p.mounting==='through-hole'?'通孔':'贴片'}`,'field-note'));
      card.append(details);
      if(a.status==='pending')for(const [label,decision] of [['确认本次提交至待付款','approve'],['拒绝本次提交','deny']]){
        const b=make('button',label,'text-button') as HTMLButtonElement;
        b.disabled=expired||a.intent.projectVersion!==context.project.version;
        b.onclick=async()=>{
          if(decision==='approve'&&!window.confirm(`确认 ${a.projectName} 版本${a.intent.projectVersion}，PCB${a.intent.quantity}片、SMT${assembled}片，${a.intent.assemblyScope==='smd-only'?'仅SMT贴片，通孔自焊':'按列出物料装配'}，提交至待付款？不付款，不放行生产。`))return;
          b.disabled=true;
          try{await api(`procurement/approvals/${a.id}/${decision}`,'POST');this.notify(decision==='approve'?'已确认，Agent可执行本次提交。':'已拒绝本次提交。');await this.render(this.context);}
          catch(error){this.notify((error as Error).message,true);b.disabled=false;}
        };card.append(b);
      }
      host.append(card);
    }
  }

  private async renderAttempts(host:HTMLElement,context:Context,generation:number){
    const pid=context.project.id;
    const [list,orders]=await Promise.all([api(`projects/${pid}/supplier-attempts`),api(`projects/${pid}/orders?limit=100`)]);
    if(generation!==this.generation)return;
    host.append(make('h3','可恢复的执行任务'),make('p','执行记录与设计版本分开保存。相同资料已有订单时，只核对该订单，避免重复下单。','field-note'));
    const prepare=make('div','','manufacturing-row'),orderSelect=document.createElement('select');orderSelect.setAttribute('aria-label','从已有订单建立执行任务');
    for(const o of orders.orders){const option=document.createElement('option');option.value=o.id;option.textContent=`${o.identity.provider==='jlc-smt'?'SMT':'PCB'} · ${o.identity.displayNumber}`;orderSelect.append(option);}
    prepare.append(orderSelect,this.button('建立只读核对任务',async()=>{
      const o=orders.orders.find((o:any)=>o.id===orderSelect.value);if(!o)return;
      const binding=o.sourceBinding,smt=o.identity.provider==='jlc-smt';
      const body:any={expectedProjectVersion:context.project.version,manufacturingJobId:binding.manufacturingJobId,expectedGerberSha256:binding.gerber.sha256,provider:o.identity.provider,parameters:{...o.parameters},requestId:requestId()};
      if(smt){body.procurementRevisionId=binding.procurement.revisionId;body.expectedBomSha256=binding.procurement.bom.sha256;body.expectedCplSha256=binding.procurement.cpl.sha256;body.parameters.pcbOrderNumber=o.pcbOrderNumber;}
      const result=await api(`projects/${pid}/supplier-attempts`,'POST',body);this.selected.set(pid,result.attempt.id);
    },context,!orders.orders.length));host.append(prepare);
    host.append(make('p','新资料可由 Agent 通过 MCP 创建执行任务。当前支持专用浏览器、固定资料与只读订单核对；上传、工艺、匹配和提交暂留人工检查点。','field-note'));
    const chosen=list.attempts.find((a:any)=>a.id===this.selected.get(pid))||list.attempts[0];
    if(!chosen){host.append(make('p','还没有执行任务。','empty-small'));return;}
    const select=document.createElement('select');select.setAttribute('aria-label','下单执行任务');
    for(const a of list.attempts){const option=document.createElement('option');option.value=a.id;option.textContent=`${a.provider==='jlc-smt'?'SMT':'PCB'} · ${a.existingOrder?.displayNumber||a.id} · ${this.stage(a.status)}`;select.append(option);}
    select.value=chosen.id;select.onchange=()=>{this.selected.set(pid,select.value);void this.render(context);};host.append(select);
    const detail=await api(`projects/${pid}/supplier-attempts/${chosen.id}`);if(generation!==this.generation)return;
    const a=detail.attempt;host.append(make('p',`${this.stage(a.status)} · 执行版本 ${a.version}`,'supplier-attempt-state'),make('p',a.latestResult.message,'field-note'));
    const actions=make('div','','document-downloads');
    for(const [label,action] of [['打开此任务的专用浏览器','open'],['检查当前页面','inspect'],['核对已有订单','reconcile']]){
      actions.append(this.button(label,async()=>{await api(`projects/${pid}/supplier-attempts/${a.id}/step`,'POST',{expectedAttemptVersion:a.version,action,requestId:requestId()});},context,a.status==='running'));
    }host.append(actions);
    const snap=a.latestResult.snapshot;
    if(snap){host.append(make('p',`平台可见订单号：${snap.orderNumber||'尚无'} · ${snap.orderNumberVisible?'已看到':'本页未看到'} · ${when(snap.observedAt)}`,'field-note'));
      host.append(make('p',snap.paymentStatus==='unpaid'?`该订单行显示：${snap.paymentStatusLabel}`:'付款、DFM、方向和生产状态尚未核实。','field-note'));}
    const files=make('div','','document-downloads');
    for(const [name,f] of [['Gerber',a.sourceBinding.gerber],['BOM',a.sourceBinding.procurement?.bom],['CPL',a.sourceBinding.procurement?.cpl]] as [string,any][]){
      if(!f?.artifactId)continue;const link=document.createElement('a');link.textContent=`下载固定的 ${name}`;link.href=`/api/artifacts/${f.artifactId}/file`;link.className='text-button';files.append(link);
    }host.append(files);
    const history=document.createElement('details');history.open=this.expanded.has(a.id);history.ontoggle=()=>history.open?this.expanded.add(a.id):this.expanded.delete(a.id);
    history.append(make('summary',`文件摘要与步骤历史 · ${detail.steps.length} 条`));
    history.append(make('p',`绑定摘要 ${a.sourceBindingSha256}`,'artifact-digest'),make('p',`Gerber ${a.sourceBinding.gerber.sha256}`,'artifact-digest'));
    for(const step of detail.steps){history.append(make('p',`${step.attemptVersion} · ${this.action(step.action)} · ${this.stage(step.result.status)} · ${when(step.createdAt)}`),make('p',step.result.message,'field-note'));}
    host.append(history);
  }

  private button(label:string,action:()=>Promise<void>,context:Context,disabled=false){
    const button=make('button',label,'text-button') as HTMLButtonElement;button.disabled=this.busy||disabled;
    button.onclick=async()=>{this.busy=true;button.disabled=true;try{await action();this.notify('执行记录已更新。');}catch(error){this.notify((error as Error).message,true);}finally{this.busy=false;await this.render(this.context);}};
    return button;
  }
  private stage(value:string){return ({prepared:'资料已固定','existing-order':'已关联既有订单',running:'正在检查','needs-login':'需要登录','needs-open':'需要重新打开','manual-required':'需要人工接管','ready-for-manual-form':'等待人工表单操作','existing-order-found':'已看到订单号','existing-order-not-visible':'本页未看到订单号','duplicate-blocked':'已阻止重复操作',interrupted:'执行已中断，需核对',failed:'检查未完成'} as Record<string,string>)[value]||value;}
  private action(value:string){const result=value.endsWith('-result');const base=result?value.slice(0,-7):value;return (({prepare:'固定资料',open:'打开工作页',inspect:'检查页面',reconcile:'核对订单',recover:'中断恢复',upload:'上传资料',configure:'工艺配置',match:'器件匹配',review:'提交前核对',submit:'提交'} as Record<string,string>)[base]||base)+(result?'结果':'');}
}
