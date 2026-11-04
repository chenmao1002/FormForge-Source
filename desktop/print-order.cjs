// Dedicated reviewed 3D ordering. Ordinary supplier controls remain quote-only.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const policy=require('./policy.cjs');
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
function keyFor(spec){const {files,expectedPayableCny,quotedExcludingTaxCny,...base}=spec;return hash(JSON.stringify({...base,fileHashes:files.map(f=>f.sha256).sort()}));}
function printCommitAllowed(step,label,url){
  let u;try{u=new URL(url);}catch{return false;}
  if(u.hostname!=='www.jlc-3dp.cn')return false;
  return step==='checkout'&&u.pathname==='/placeOrder'&&label==='提交订单'
    ||step==='submit'&&u.pathname==='/submitOrder'&&label==='立即下单'
    ||step==='manual'&&u.pathname==='/submitOrder'&&label==='人工确认订单';
}
function requestPayload(details){
  const bytes=Buffer.concat((details.uploadData||[]).filter(d=>d.bytes).map(d=>Buffer.from(d.bytes))).toString('utf8');
  try{return JSON.parse(bytes);}catch{return Object.fromEntries(new URLSearchParams(bytes));}
}
function printRequestBlocked(details,gate){
  const u=new URL(details.url),method=details.method;
  if(!['POST','PUT','PATCH','DELETE'].includes(method))return false;
  if(/\/settlement\/(?:instantPayV2|immediatelyPay|unionPay)(?:\/|$)|\/order\/freeOrder(?:\/|$)/i.test(u.pathname))return true;
  if(!u.pathname.endsWith('/preorder/submitOrder'))return policy.blockedRequest(details.url,method);
  if(!gate||gate.used||gate.step!=='submit-print'||!gate.approvalId||gate.allowedSteps?.length!==1||gate.allowedSteps[0]!=='submit-print'||u.hostname!=='www.jlc-3dp.cn')return true;
  const data=requestPayload(details);
  return data.confirmOrderType!=='CUSTOMER_CONFIRM'||!data.preOrderBatchNumber||(gate.batch&&data.preOrderBatchNumber!==gate.batch);
}
function validateQuote(snapshot,state,spec,uploads){
  const names=spec.files.map(f=>path.basename(f.path));
  for(const f of spec.files){const name=path.basename(f.path);if(!snapshot.text.includes(name)||uploads.get(name)!==f.sha256)policy.fail('print_order_mismatch','3D文件与本次已核对上传不符。',409);}
  if(snapshot.elements.filter(e=>e.label==='9000HE').length!==names.length||snapshot.elements.filter(e=>e.label==='白色').length!==names.length)policy.fail('print_order_mismatch','3D材料或颜色与本次复核不符。',409);
  for(const [prefix,allowed] of [['打磨：',['不需要','不支持']],['喷漆：',['不需要']],['镶嵌螺母：',['不需要']]]){
    const values=snapshot.elements.filter(e=>e.label.startsWith(prefix)).map(e=>e.label.slice(prefix.length));
    if(values.length!==names.length||values.some(v=>!allowed.includes(v)))policy.fail('print_order_mismatch','3D表面处理或镶嵌选项与本次复核不符。',409);
  }
  if(state.quantities.length!==names.length||state.quantities.some(n=>n!==spec.copiesPerPart)||!Number.isFinite(state.quoteExcludingTax)||Math.abs(state.quoteExcludingTax-spec.quotedExcludingTaxCny)>.005)policy.fail('print_order_mismatch','3D份数或实时报价与本次复核不符。',409);
}
// Fixed DOM reads only. No supplier application state, credentials or note edits.
function readPrintPageState(names){
  const visible=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
  const compact=s=>String(s||'').replace(/\s+/g,' ').trim(),body=document.body?.innerText||'';
  const quantities=names.map(name=>{
    const candidates=[...document.querySelectorAll('div,span,p,a')].filter(el=>visible(el)&&compact(el.innerText)===name).reverse();
    for(const label of candidates){let row=label.parentElement;for(let depth=0;row&&depth<12;depth++,row=row.parentElement){
      const text=row.innerText||'';if(names.filter(n=>text.includes(n)).length!==1)break;
      const inputs=[...row.querySelectorAll('input[type=text],input[type=number]')].filter(el=>visible(el)&&/^\d+$/.test(el.value));
      if(inputs.length===1)return Number(inputs[0].value);
    }}return null;
  });
  let terms=null;
  for(const el of document.querySelectorAll('input[type=checkbox]')){
    let parent=el.parentElement;
    for(let i=0;parent&&i<4;i++,parent=parent.parentElement){const text=compact(parent.innerText);if(text.length<500&&text.includes('我已阅读并同意')){terms=el;break;}}
    if(terms)break;
  }
  const termsId=[...(globalThis.__ffDesktopElements||new Map())].find(([,v])=>(v.el||v)===terms)?.[0]||null;
  const manual=[...document.querySelectorAll('button')].find(el=>compact(el.innerText)==='人工确认订单');
  const note=[...document.querySelectorAll('textarea')].find(el=>/备注/.test(el.closest('.item-box')?.querySelector('.title')?.innerText||''));
  const amount=pattern=>{const m=body.match(pattern);return m?Number(m[1].replace(/,/g,'')):null;};
  return {pathname:location.pathname,fileNames:names.filter(n=>body.includes(n)),quantities,termsId,termsChecked:!!terms?.checked,
    manualConfirmChecked:!!manual?.classList.contains('check'),noteValue:note?note.value:null,
    batch:new URL(location.href).searchParams.get('preOrderBatchNumber'),
    quoteExcludingTax:amount(/总计\s*[（(]不含税[）)]\s*[¥￥]\s*([\d,.]+)/),
    payable:amount(/(?:应付总额|应付金额|实付金额|总计[（(]含税[）)])\s*[:：]?\s*[¥￥]\s*([\d,.]+)/),
    modelRows:[...document.querySelectorAll('tr')].filter(el=>visible(el)&&names.some(n=>(el.innerText||'').includes(n))).map(el=>compact(el.innerText)),
    success:/下单成功|订单提交成功|订单已提交/.test(body),orderNumbers:[...new Set(body.match(/\bG\d{10,20}\b/g)||[])],
    unpaidObserved:/未付款|待付款|等待(?:您)?支付|等待审核|市场审核/.test(body)};
}
async function printTermsPoint(id){
  const entry=globalThis.__ffDesktopElements?.get(id),el=entry?.el||entry;
  if(entry?.frame||!el?.isConnected||el.type!=='checkbox'||!id.startsWith(`v${globalThis.__ffDesktopVersion}-`)||location.pathname!=='/placeOrder')return {error:'invalid-print-terms'};
  let parent=el.parentElement,context='';
  for(let i=0;parent&&i<4;i++,parent=parent.parentElement){if((parent.innerText||'').includes('我已阅读并同意')&&(parent.innerText||'').length<500){context=parent.innerText;break;}}
  if(!context.includes('打印须知')||!context.includes('3D模型设计规范'))return {error:'invalid-print-terms'};
  if(el.checked)return {alreadyChecked:true};
  const root=el.closest('.el-checkbox'),indicator=root?.querySelector('.el-checkbox__inner');
  if(!root||!indicator||root.querySelectorAll('input[type=checkbox]').length!==1||el.disabled)return {error:'unrecognized-print-terms'};
  indicator.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
  await new Promise(resolve=>{const timer=setTimeout(resolve,300);requestAnimationFrame(()=>{clearTimeout(timer);resolve();});});
  if(!el.isConnected||el.closest('.el-checkbox')!==root)return {error:'stale-print-terms'};
  const r=indicator.getBoundingClientRect(),x=Math.floor((Math.max(0,r.left)+Math.min(innerWidth,r.right))/2),y=Math.floor((Math.max(0,r.top)+Math.min(innerHeight,r.bottom))/2),hit=document.elementFromPoint(x,y);
  if(r.width<1||r.height<1||!hit||!root.contains(hit))return {error:'obstructed-print-terms'};
  return {point:{x,y}};
}
function createPrintController(api){
  const uploads=api.uploads||new Map();let gate=null,latest=null;
  const directory=path.join(api.local,'print-orders');fs.mkdirSync(directory,{recursive:true});
  const save=state=>{fs.writeFileSync(path.join(directory,state.key+'.json'),JSON.stringify(state,null,2));latest=state;};
  const load=spec=>{const key=keyFor(spec),file=path.join(directory,key+'.json');return fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):{key,specification:spec,phase:'new'};};
  const summary=async spec=>api.getContents().executeJavaScriptInIsolatedWorld(999,[{code:`(${readPrintPageState.toString()})(${JSON.stringify(spec.files.map(f=>path.basename(f.path)))})`}]);
  const click=async(label,step)=>{
    const snapshot=await api.inspect(),choices=snapshot.elements.filter(e=>e.tag==='button'&&e.label.trim()===label&&!e.disabled);
    if(choices.length!==1)policy.fail('print_order_mismatch','当前3D提交控件不唯一或未就绪。',409);
    return api.elementAction({action:'click',elementId:choices[0].id},step);
  };
  const wait=async(spec,predicate)=>{const deadline=Date.now()+18000;for(;;){const value=await summary(spec);if(predicate(value))return value;if(Date.now()>deadline)policy.fail('print_order_mismatch','3D原生页面尚未返回可核对结果，请观察后继续。',409);await new Promise(r=>setTimeout(r,400));}};
  return {
    status:()=>({printOrderingEnabled:true,printAdapterSourceSha256:api.sourceSha256||null,printSubmission:latest?{projectId:latest.specification.projectId,projectVersion:latest.specification.projectVersion,phase:latest.phase,orderNumbers:latest.orderNumbers||[],copiesPerPart:latest.specification.copiesPerPart,parts:latest.specification.files.length,payableCny:latest.specification.expectedPayableCny??null,paymentPerformed:false,supplierNotesTouched:false}:null}),
    recordUploads:files=>{for(const f of files)uploads.set(path.basename(f),hash(fs.readFileSync(f)));},
    networkBlocked:details=>{
      const blocked=printRequestBlocked(details,gate);
      if(!blocked&&details.method==='POST'&&new URL(details.url).pathname.endsWith('/preorder/submitOrder')){gate.used=true;gate.state.nativeRequestSentAt=new Date().toISOString();save(gate.state);}
      return blocked;
    },
    async command(body){
      const spec=body.specification;
      if(!spec||spec.material!=='9000HE'||spec.color!=='white'||spec.finish!=='no-sanding-no-paint-no-inserts'||spec.leadTimeHours!==48)policy.fail('print_order_mismatch','本入口的3D工艺与复核不符。');
      policy.checkedFiles(api.root,spec.files);
      let state=load(spec);latest=state;
      if(body.action==='print-observe'){
        const current=await summary(spec);
        if(state.nativeRequestSentAt&&current.orderNumbers.length===1&&current.fileNames.length===spec.files.length&&current.unpaidObserved){state.phase='submitted-unpaid-observed';state.orderNumbers=current.orderNumbers;state.observedAt=new Date().toISOString();save(state);}
        return {...api.status(),printObservation:{...current,noteValue:undefined,batch:undefined},supplierNotesTouched:false,paymentPerformed:false};
      }
      if(state.phase==='submission-started'||state.nativeRequestSentAt||state.orderNumbers?.length)policy.fail('print_reconcile_required','本批3D已有提交尝试；先查已有订单，不重复创建。',409);
      if(body.action==='print-prepare'){
        const snapshot=await api.inspect();let current=await summary(spec);
        if(!(state.quoteVerifiedAt&&current.pathname==='/submitOrder'&&current.fileNames.length===spec.files.length)){
          validateQuote(snapshot,current,spec,uploads);
          if(current.pathname!=='/placeOrder')policy.fail('print_order_mismatch','当前不是已核对的3D报价页。',409);
          if(!current.termsChecked){
            if(!current.termsId||!api.status().visible)policy.fail('print_order_mismatch','未定位可见的3D打印条款控件。',409);
            const point=await api.getContents().executeJavaScriptInIsolatedWorld(999,[{code:`(${printTermsPoint.toString()})(${JSON.stringify(current.termsId)})`}]);
            if(point.error)policy.fail('print_order_mismatch','3D打印条款控件未能通过归属和遮挡核对。',409);
            if(!point.alreadyChecked){const {x,y}=point.point;api.getContents().focus();for(const type of ['mouseMove','mouseDown','mouseUp'])api.getContents().sendInputEvent({type,x,y,...(type==='mouseMove'?{}:{button:'left',clickCount:1})});}
            current=await wait(spec,s=>s.termsChecked);
          }
          state.phase='checkout-preparing';state.quoteVerifiedAt=new Date().toISOString();save(state);
          await click('提交订单','checkout');current=await wait(spec,s=>s.pathname==='/submitOrder'&&s.fileNames.length===spec.files.length);
        }
        if(!current.manualConfirmChecked){await click('人工确认订单','manual');current=await summary(spec);}
        if(!current.manualConfirmChecked)policy.fail('print_order_mismatch','3D订单未明确使用人工确认方式。',409);
        state.phase='checkout-prepared';state.batch=current.batch;state.preparedAt=new Date().toISOString();save(state);
        return {...await api.inspect(),printPreparation:{projectId:spec.projectId,parts:spec.files.length,copiesPerPart:spec.copiesPerPart,quotedExcludingTaxCny:spec.quotedExcludingTaxCny,payableCny:current.payable,manualConfirmChecked:true,supplierNotesTouched:false},orderSubmitted:false,paymentPerformed:false};
      }
      if(body.action!=='print-submit-unpaid')policy.fail('invalid_input','未知3D下单动作。');
      const grant=body.submissionAuthorization;
      if(!grant?.approvalId||grant.allowedSteps?.length!==1||grant.allowedSteps[0]!=='submit-print'||!/^[a-f0-9]{64}$/.test(grant.intentSha256||''))policy.fail('print_order_mismatch','缺少本次3D提交的Agent复核授权。',409);
      const current=await summary(spec);
      if(state.phase!=='checkout-prepared'||current.pathname!=='/submitOrder'||!current.manualConfirmChecked||current.fileNames.length!==spec.files.length||!Number.isFinite(current.payable)||Math.abs(current.payable-spec.expectedPayableCny)>.005)policy.fail('print_order_mismatch','3D结算页与本次文件、确认方式或应付总额不符。',409);
      state.specification=spec;state.phase='submission-started';state.submissionStartedAt=new Date().toISOString();state.approvalId=grant.approvalId;save(state);
      gate={...grant,step:'submit-print',used:false,batch:state.batch,state};
      try{
        await click('立即下单','submit');const observed=await wait(spec,s=>s.success||s.orderNumbers.length>0);
        state.phase=gate.used&&observed.success?'submitted-awaiting-observation':'result-needs-reconciliation';state.orderNumbers=observed.orderNumbers;state.finishedAt=new Date().toISOString();save(state);
        return {...await api.inspect(),orderSubmitted:gate.used&&observed.success?true:null,paymentPerformed:false,productionReleased:false,supplierNotesTouched:false};
      }finally{
        if(!gate.used){state.attempts=[...(state.attempts||[]),{startedAt:state.submissionStartedAt,finishedAt:new Date().toISOString(),nativeRequestSent:false}];state.phase='checkout-prepared';save(state);}
        gate=null;
      }
    },
  };
}
module.exports={createPrintController,printRequestBlocked,printCommitAllowed,validateQuote,keyFor};
