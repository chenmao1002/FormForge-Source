// Playwright owns the page/session. Legacy module supplies pure byte/quote rules only.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {expect}=require('playwright/test');
const {verifyObservation}=require('./print-observation.cjs');
const {checkedFiles,fail}=require('../desktop/policy.cjs');
const {keyFor,printRequestBlocked,validateQuote}=require('../desktop/print-order.cjs');
const ENTRY='https://www.jlc-3dp.cn/placeOrder';
const sourceSha256=crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
const money=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<0.005;
const PAGE_CACHE=Symbol.for('formforge.print-pages');

// Only rendered DOM; never read supplier stores, tokens, addresses or note contents.
function readPage(names){
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
  const compact=s=>String(s||'').replace(/\s+/g,' ').trim();
  const text=document.body?.innerText||'';
  const all=[...document.querySelectorAll('div,span,p,a,td,button,label')].filter(visible);
  const rowNodes=[];
  const rows=names.map(name=>{
    const labels=all.filter(e=>compact(e.innerText)===name).reverse();
    for(const label of labels){for(let row=label.parentElement,depth=0;row&&depth<12;row=row.parentElement,depth++){
      if(names.filter(n=>(row.innerText||'').includes(n)).length!==1)break;
      const inputs=[...row.querySelectorAll('input[type=text],input[type=number]')].filter(e=>visible(e)&&/^\d+$/.test(e.value));
      if(inputs.length===1){rowNodes.push(row);return {name,quantity:Number(inputs[0].value),text:compact(row.innerText)};}
    }}return {name,quantity:null,text:''};
  });
  const minimal=all.filter(e=>rowNodes.some(row=>row.contains(e))&&!Array.from(e.children).some(c=>visible(c)&&compact(c.innerText)===compact(e.innerText)));
  let terms=null;
  for(const input of document.querySelectorAll('input[type=checkbox]')){
    for(let parent=input.parentElement,depth=0;parent&&depth<5;parent=parent.parentElement,depth++){
      const value=compact(parent.innerText);
      if(value.length<500&&value.includes('我已阅读并同意')&&value.includes('打印须知')&&value.includes('3D模型设计规范')){terms=input;break;}
    }
    if(terms)break;
  }
  const manual=[...document.querySelectorAll('button')].filter(visible).find(e=>compact(e.innerText)==='人工确认订单');
  const amount=re=>{const m=text.match(re);return m?Number(m[1].replace(/,/g,'')):null;};
  const selected48=all.some(e=>{
    if(compact(e.innerText)!=='48小时')return false;
    for(let node=e,depth=0;node&&depth<3;node=node.parentElement,depth++)
      if(/(?:^|\s)(?:check|checked|active|selected|is-checked)(?:\s|$)/.test(node.className?.toString()||'')||node.getAttribute('aria-checked')==='true'||node.matches('label')&&node.querySelector('input:checked'))return true;
    return false;
  });
  return {pathname:location.pathname,batch:new URL(location.href).searchParams.get('preOrderBatchNumber'),
    fileNames:names.filter(n=>text.includes(n)),rows,quantities:rows.map(r=>r.quantity),
    // Minimal labels retain the same exact material/finish checks used by the old controller.
    elements:minimal.map(e=>({label:compact(e.innerText).replace(/(打磨|喷漆|镶嵌螺母)：\s*/g,'$1：')})).filter(e=>/^(?:9000HE|白色)$|^(?:打磨|喷漆|镶嵌螺母)：[^：\s]+$/.test(e.label)),
    termsChecked:!!terms?.checked,manualConfirmChecked:!!manual&&(manual.classList.contains('check')||manual.getAttribute('aria-pressed')==='true'),
    quoteExcludingTax:amount(/总计\s*[（(]不含税[）)]\s*[¥￥]\s*([\d,.]+)/),
    payable:amount(/(?:应付总额|应付金额|实付金额|总计[（(]含税[）)])\s*[:：]?\s*[¥￥]\s*([\d,.]+)/),
    leadTimeSelected:selected48,success:/下单成功|订单提交成功|订单已提交/.test(text),
    orderNumbers:[...new Set(text.match(/\bG\d{10,20}\b/g)||[])],
    unpaidObserved:/未付款|待付款|等待(?:您)?支付|等待审核|市场审核/.test(text),
    loading:!!document.querySelector('[aria-busy=true]')||[...document.querySelectorAll('.el-loading-mask')].some(visible),
    rejected:/商品不符合材料尺寸限制|报价失败|无法报价/.test(text)};
}
async function modelRow(page,name,names){
  const label=page.getByText(name,{exact:true}).filter({visible:true});
  // History lists may repeat filenames. Choose only a row with a quantity editor.
  for(const candidate of await label.all()){
    for(let depth=1;depth<=12;depth++){
      const row=candidate.locator(`xpath=ancestor::*[${depth}]`);
      if(!await row.count())break;
      const text=await row.innerText();if(names.filter(n=>text.includes(n)).length!==1)break;
      const editor=row.locator('input[type=text]:visible,input[type=number]:visible');
      if(await editor.count()===1&&/^\d+$/.test(await editor.inputValue()))return {row,editor};
    }
  }
  fail('print_order_mismatch','未唯一定位制作件数量行：'+name,409);
}
function createPrintController(api){
  const directory=path.join(api.local,'print-orders');fs.mkdirSync(directory,{recursive:true});
  const latestFile=path.join(directory,'latest.json');
  let latest=fs.existsSync(latestFile)?JSON.parse(fs.readFileSync(latestFile,'utf8')):null,gate=null,page=null;
  const save=state=>{fs.writeFileSync(path.join(directory,state.key+'.json'),JSON.stringify(state,null,2));fs.writeFileSync(latestFile,JSON.stringify(state,null,2));latest=state;};
  const load=spec=>{
    const key=keyFor(spec),file=path.join(directory,key+'.json');
    if(fs.existsSync(file))return JSON.parse(fs.readFileSync(file,'utf8'));
    // Import the historical submission marker, never cookies or a second browser session.
    const old=path.join(api.root,'.local','desktop','print-orders',key+'.json');
    if(fs.existsSync(old)){const state=JSON.parse(fs.readFileSync(old,'utf8'));save({...state,migratedFrom:'electron',migratedAt:new Date().toISOString()});return {...state,migratedFrom:'electron'};}
    return {key,specification:spec,phase:'new'};
  };
  const namesFor=spec=>spec.files.map(f=>path.basename(f.path));
  const summary=spec=>page.evaluate(readPage,namesFor(spec));
  const wait=async(spec,check)=>{let current;await expect.poll(async()=>{
    try{current=await summary(spec);return check(current);}catch(error){if(/Execution context was destroyed|Cannot find context/.test(error.message))return false;throw error;}
  },{timeout:25000}).toBeTruthy();return current;};
  const nativeBatch=async()=>{
    const batches=new Set(),evidence=[];
    for(const request of await page.requests()){
      const u=new URL(request.url());if(u.hostname!=='www.jlc-3dp.cn'||!/^\/3dp\/preorder\/(getBatchPreOrderInfo|calculateFee)$/.test(u.pathname))continue;
      let body={};try{body=request.postDataJSON()||{};}catch{}
      const batch=body.preOrderBatchNumber||body.batchCreatePreOrderNum||u.searchParams.get('preOrderBatchNumber');
      if(batch)batches.add(String(batch));
      evidence.push({route:u.pathname,fields:Object.keys(body),batch:batch||null});
    }
    fs.writeFileSync(path.join(directory,'native-batch-evidence.json'),JSON.stringify(evidence,null,2));
    return batches.size===1?[...batches][0]:null;
  };
  const locate=async(spec,fresh=false)=>{
    const context=api.getContext();
    const cached=context[PAGE_CACHE]?.get(keyFor(spec));
    if(!fresh&&cached&&!cached.isClosed())page=cached;
    if(fresh){
      page=await context.newPage();
      const trace=(phase,details)=>fs.appendFileSync(path.join(directory,'browser-events.jsonl'),JSON.stringify({at:new Date().toISOString(),phase,...details})+'\n');
      page.on('response',response=>{if(response.request().method()==='POST'){const u=new URL(response.url());trace('response',{route:u.origin+u.pathname,status:response.status()});}});
      page.on('requestfailed',request=>{const u=new URL(request.url());trace('request-failed',{route:u.origin+u.pathname,error:request.failure()?.errorText});});
      await page.goto(ENTRY,{waitUntil:'domcontentloaded',timeout:45000});
    }
    else if(!page||page.isClosed()){
      const candidates=[];
      for(const p of context.pages().filter(p=>p.url().startsWith('https://www.jlc-3dp.cn/'))){
        if((await p.evaluate(readPage,namesFor(spec))).fileNames.length===spec.files.length)candidates.push(p);
      }
      if(candidates.length!==1)fail('print_reconcile_required','未唯一定位本批3D页面；请在同一浏览器接管核对原批次/订单。',409);
      page=candidates[0];
    }
    api.setPage(page);
    if(new URL(page.url()).origin!=='https://www.jlc-3dp.cn')fail('human_control','3D页面离开原生站点，请在同一浏览器完成登录后交回控制。',409);
    return page;
  };
  const blockedState=state=>state.phase==='submission-started'||state.nativeRequestSentAt||state.orderNumbers?.length;
  const observeSubmitted=async(state,spec)=>{
    const receiptFile=path.join(directory,state.key+'-create-response-private.json');
    if(!fs.existsSync(receiptFile))return null;
    const receipt=JSON.parse(fs.readFileSync(receiptFile,'utf8'));
    const ids=receipt.body?.data?.orderAccessIds;
    if(!state.nativeRequestSentAt||receipt.body?.success!==true||!Array.isArray(ids)||!ids.length)return null;
    const context=api.getContext();
    let list=context.pages().find(p=>p.url().startsWith('https://member.jlc.com/center/swh/'));
    if(!list)list=await context.newPage();api.setPage(list);
    const records=new Map(),wrappers=new Map(),routes=[];
    const sanitize=value=>{
      if(Array.isArray(value))return value.map(sanitize);
      if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([key])=>!/address|mobile|phone|email|token|secret|contact|receiver|account|invoice|recipient|tax/i.test(key)).map(([key,v])=>[key,sanitize(v)]));
      if(typeof value==='string'&&/^https?:\/\//.test(value)){try{const u=new URL(value);return u.origin+u.pathname;}catch{}}
      return value;
    };
    const scan=(value,parent)=>{
      if(!value||typeof value!=='object')return;
      if(!Array.isArray(value)){
        const id=ids.find(id=>value.orderAccessId===id);
        if(id&&/^G\d{10,20}$/.test(value.orderCode||'')){
          const safe=sanitize(value);records.set(id,safe);fs.writeFileSync(path.join(directory,state.key+'-lookup-private.json'),JSON.stringify([...records.entries()],null,2));
          if(parent&&!Array.isArray(parent)){const detail=sanitize(parent);wrappers.set(id,detail);fs.writeFileSync(path.join(directory,state.key+'-'+value.orderCode+'-details-private.json'),JSON.stringify(detail,null,2));}
        }
      }
      Object.values(value).forEach(child=>scan(child,value));
    };
    const capture=async response=>{
      const u=new URL(response.url());
      if(!/(?:^|\.)jlc(?:-3dp)?\.c(?:om|n)$/.test(u.hostname)||!/order|produce|preorder/i.test(u.pathname))return;
      try{const body=await response.json();routes.push({route:u.origin+u.pathname,status:response.status(),keys:Object.keys(body||{})});scan(body);}
      catch{}
      fs.writeFileSync(path.join(directory,'order-observation-routes.json'),JSON.stringify(routes,null,2));
    };
    list.on('response',capture);
    try{
      await list.goto('https://member.jlc.com/center/swh/',{waitUntil:'domcontentloaded',timeout:45000});
      await expect.poll(()=>records.size,{timeout:45000}).toBe(ids.length);
      let observation;try{observation=verifyObservation(ids,records,wrappers,spec);}catch(error){fail('print_reconcile_required',error.message,409);}
      const numbers=observation.orders.map(order=>order.orderNumber);
      state.orderNumbers=numbers;state.orderAccessIds=ids;state.observedAt=new Date().toISOString();
      state.phase='submitted-unpaid-observed';state.observation=observation;save(state);
      return {printObservation:{matchedNativeOrders:true,orderNumbers:numbers,...state.observation,paymentPerformed:false},...controller.status(),paymentPerformed:false,productionReleased:false,supplierNotesTouched:false};
    }finally{list.off('response',capture);}
  };
  const controller={
    status(){return {printOrderingEnabled:true,printAdapter:'playwright-reviewed-3d-unpaid/1',printAdapterSourceSha256:sourceSha256,printSubmission:latest?{
      projectId:latest.specification.projectId,projectVersion:latest.specification.projectVersion,phase:latest.phase,
      orderNumbers:latest.orderNumbers||[],copiesPerPart:latest.specification.copiesPerPart,parts:latest.specification.files.length,
      payableCny:latest.specification.expectedPayableCny??latest.payableCny??null,quoteExcludingTaxCny:latest.quoteExcludingTaxCny??null,
      paymentPerformed:false,supplierNotesTouched:false}:null};},
    networkBlocked(request){
      const details={url:request.url(),method:request.method(),uploadData:request.postDataBuffer()?[{bytes:request.postDataBuffer()}]:[]};
      const blocked=printRequestBlocked(details,gate);
      if(!blocked&&details.method==='POST'&&new URL(details.url).pathname.endsWith('/preorder/submitOrder')){
        gate.used=true;gate.state.nativeRequestSentAt=new Date().toISOString();save(gate.state);
      }
      return blocked;
    },
    async command(body){
      const spec=body.specification;
      if(!spec||spec.material!=='9000HE'||spec.color!=='white'||spec.finish!=='no-sanding-no-paint-no-inserts'||spec.leadTimeHours!==48)
        fail('print_order_mismatch','本入口的3D工艺与复核不符。');
      const files=checkedFiles(api.root,spec.files),names=namesFor(spec);
      let state=load(spec);latest=state;
      if(body.action==='print-observe'){
        const observed=await observeSubmitted(state,spec);if(observed)return observed;
      }
      if(body.action!=='print-observe'&&blockedState(state))fail('print_reconcile_required','本批3D已有提交尝试；先查已有订单，不重复创建。',409);
      if(body.action==='print-quote'){
        const reusable=[];
        const context=api.getContext(),cache=context[PAGE_CACHE]||(context[PAGE_CACHE]=new Map());
        const cached=cache.get(keyFor(spec));
        if(cached&&!cached.isClosed()&&cached.url().startsWith(ENTRY))reusable.push(cached);
        for(const candidate of api.getContext().pages().filter(p=>p.url().startsWith(ENTRY))){
          if(reusable.includes(candidate))continue;
          const uploaded=await candidate.locator('input[type=file]').evaluateAll(async inputs=>{
            const files=inputs.flatMap(e=>Array.from(e.files||[]));
            return Promise.all(files.map(async f=>({name:f.name,sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await f.arrayBuffer()))).map(b=>b.toString(16).padStart(2,'0')).join('')})));
          });
          if(uploaded.length===spec.files.length&&spec.files.every(f=>uploaded.some(u=>u.name===path.basename(f.path)&&u.sha256===f.sha256)))reusable.push(candidate);
        }
        if(reusable.length===1){page=reusable[0];api.setPage(page);}else await locate(spec,true);
        const upload=page.locator('input[type=file]');
        await expect(upload).toHaveCount(1,{timeout:20000});
        const payloads=checkedFiles(api.root,spec.files).map((file,i)=>{
          const buffer=fs.readFileSync(file);
          if(crypto.createHash('sha256').update(buffer).digest('hex')!==spec.files[i].sha256)fail('file_mismatch','上传前STL字节已经变化。',409);
          return {name:path.basename(file),mimeType:'application/octet-stream',buffer};
        });
        if(reusable.length!==1){
          const choosing=page.waitForEvent('filechooser',{timeout:15000});
          await (await upload.isVisible()?upload:upload.locator('..')).click();
          await (await choosing).setFiles(payloads);
          cache.set(keyFor(spec),page);
        }
        for(const name of names)await expect.poll(async()=>{try{await modelRow(page,name,names);return true;}catch{return false;}},{timeout:90000}).toBeTruthy();
        const dismissGrindingNotice=async()=>{
          if(await page.getByText(/尺寸不满足打磨表处限制/).filter({visible:true}).count())await page.getByRole('button',{name:'确定',exact:true}).filter({visible:true}).click();
        };
        await dismissGrindingNotice();
        const template=page.getByText('9000HE｜白色',{exact:true}).filter({visible:true});
        if(await template.count()===1){
          await template.click();
          await expect.poll(async()=>{
            for(const name of names){const {row}=await modelRow(page,name,names);if(!await row.getByText('9000HE',{exact:true}).isVisible().catch(()=>false))return false;}
            return true;
          },{timeout:20000}).toBeTruthy();
        }
        await dismissGrindingNotice();
        for(const name of names){
          try {
          const {row,editor}=await modelRow(page,name,names);
          const material=row.getByText('9000HE',{exact:true});
          if(!await material.isVisible().catch(()=>false)){
            const choose=row.getByText(/^(选择材料|请选择材料|9600|普通树脂)$/).filter({visible:true});
            if(await choose.count()!==1)fail('print_order_mismatch','未识别材料选择入口；请接管核对，不静默替代材料。',409);
            await choose.click();
            const dialog=page.locator('[role=dialog]:visible,.el-dialog:visible').filter({hasText:'9000HE'}).last();
            await dialog.getByText('9000HE',{exact:true}).click();
            const confirm=dialog.getByRole('button',{name:/^(确定|确认|确认选择)$/});
            if(await confirm.isVisible().catch(()=>false))await confirm.click();
            await expect(material).toBeVisible();
          }
          const color=row.getByText('白色',{exact:true});await expect(color).toBeVisible();
          const sanding=row.getByText(/^\s*打磨：\s*粗磨\s*$/);
          if(await sanding.isVisible().catch(()=>false)){
            await sanding.click();const none=page.getByText('不需要',{exact:true}).filter({visible:true});
            await expect(none).toHaveCount(1);await none.click();
          }
          // Existing default finish must match exactly; never accept a different finish silently.
          for(const pattern of [/^\s*打磨：\s*(不需要|不支持)\s*$/,/^\s*喷漆：\s*不需要\s*$/,/^\s*镶嵌螺母：\s*不需要\s*$/])await expect(row.getByText(pattern)).toBeVisible();
          await editor.fill(String(spec.copiesPerPart));await editor.blur();await expect(editor).toHaveValue(String(spec.copiesPerPart));
          }catch(error){
            try {
              const failedRow=await modelRow(page,name,names);
              fs.writeFileSync(path.join(directory,'configuration-row.html'),await failedRow.row.evaluate(e=>e.outerHTML));
            }catch{}
            fs.appendFileSync(path.join(directory,'configuration-errors.jsonl'),JSON.stringify({at:new Date().toISOString(),file:name,message:String(error.message).slice(0,10000)})+'\n');throw error;
          }
        }
        await page.getByText('48小时',{exact:true}).filter({visible:true}).click();
        const current=await wait(spec,s=>!s.loading&&s.quantities.every(n=>n===spec.copiesPerPart)&&s.quoteExcludingTax>0&&s.leadTimeSelected);
        if(current.rejected)fail('print_order_mismatch','供应商拒绝当前模型或材料，未进入结算。',409);
        const quoted={...spec,quotedExcludingTaxCny:current.quoteExcludingTax};
        fs.writeFileSync(path.join(directory,'quote-observation.json'),JSON.stringify(current,null,2));
        validateQuote({text:names.join('\n'),elements:current.elements},current,quoted,new Map(spec.files.map(f=>[path.basename(f.path),f.sha256])));
        if(spec.quotedExcludingTaxCny!=null&&!money(current.quoteExcludingTax,spec.quotedExcludingTaxCny))fail('print_order_mismatch','实时报价与请求绑定金额不符。',409);
        state={...state,specification:quoted,phase:'quoted',uploadedFiles:spec.files,quoteExcludingTaxCny:current.quoteExcludingTax,quotedAt:new Date().toISOString()};save(state);
        return {printQuote:{...this.status().printSubmission,specification:quoted},orderSubmitted:false,paymentPerformed:false,supplierNotesTouched:false};
      }
      await locate(spec);
      if(body.action==='print-observe'){
        const current=await summary(spec);
        if(state.batch&&current.batch&&state.batch!==current.batch)fail('print_reconcile_required','当前3D批次与提交记录不符。',409);
        const identityConfirmed=state.orderNumbers?.length?current.orderNumbers.some(n=>state.orderNumbers.includes(n)):!!state.batch&&current.batch===state.batch;
        if(state.nativeRequestSentAt&&identityConfirmed&&current.orderNumbers.length===1&&current.fileNames.length===spec.files.length&&current.unpaidObserved){
          if(state.orderNumbers?.length&&!state.orderNumbers.includes(current.orderNumbers[0]))fail('print_reconcile_required','观察订单号与原记录不符。',409);
          state.phase='submitted-unpaid-observed';state.orderNumbers=current.orderNumbers;state.observedAt=new Date().toISOString();save(state);
        }
        const {batch,elements,rows,...publicObservation}=current;
        return {printObservation:publicObservation,...this.status(),supplierNotesTouched:false,paymentPerformed:false};
      }
      if(body.action==='print-prepare'){
        let current=await summary(spec);
        if(!(state.quoteVerifiedAt&&current.pathname==='/submitOrder'&&current.fileNames.length===spec.files.length)){
          const uploads=new Map((state.uploadedFiles||[]).map(f=>[path.basename(f.path),f.sha256]));
          validateQuote({text:current.fileNames.join('\n'),elements:current.elements},current,spec,uploads);
          if(current.pathname!=='/placeOrder'||!current.leadTimeSelected)fail('print_order_mismatch','当前3D报价页或48小时交期不符。',409);
          await page.evaluate(()=>{
            document.querySelectorAll('[data-ff-print-terms]').forEach(e=>e.removeAttribute('data-ff-print-terms'));
            const matches=[];
            for(const input of document.querySelectorAll('input[type=checkbox]')){
              for(let parent=input.parentElement,depth=0;parent&&depth<5;parent=parent.parentElement,depth++){
                const value=(parent.innerText||'').replace(/\s+/g,' ').trim();
                if(value.length<500&&value.includes('我已阅读并同意')&&value.includes('打印须知')&&value.includes('3D模型设计规范')){matches.push(input);break;}
              }
            }
            if(matches.length===1)matches[0].setAttribute('data-ff-print-terms','checked-purpose');
          });
          const checkbox=page.locator('input[data-ff-print-terms="checked-purpose"]');await expect(checkbox).toHaveCount(1);
          if(!await checkbox.isChecked()){
            const indicator=checkbox.locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," el-checkbox ")][1]').locator('.el-checkbox__inner');
            if(await indicator.count()===1)await indicator.click();else await checkbox.check();
          }
          await wait(spec,s=>s.termsChecked);
          state.phase='checkout-preparing';state.quoteVerifiedAt=new Date().toISOString();save(state);
          await page.getByRole('button',{name:'提交订单',exact:true}).click();
          await page.waitForURL(url=>url.pathname==='/submitOrder',{waitUntil:'domcontentloaded',timeout:30000});
          current=await wait(spec,s=>s.pathname==='/submitOrder'&&s.fileNames.length===spec.files.length);
        }
        if(!current.manualConfirmChecked){await page.getByRole('button',{name:'人工确认订单',exact:true}).click();current=await wait(spec,s=>s.manualConfirmChecked);}
        current=await wait(spec,s=>s.pathname==='/submitOrder'&&s.fileNames.length===spec.files.length&&s.manualConfirmChecked&&s.payable>0);
        current.batch=current.batch||await nativeBatch();
        if(!current.batch||!current.payable)fail('print_order_mismatch','3D结算未返回原生批次及实际应付总额。',409);
        state.phase='checkout-prepared';state.batch=current.batch;state.payableCny=current.payable;state.preparedAt=new Date().toISOString();save(state);
        return {printPreparation:{...this.status().printSubmission,quotedExcludingTaxCny:spec.quotedExcludingTaxCny,payableCny:current.payable,manualConfirmChecked:true},orderSubmitted:false,paymentPerformed:false,supplierNotesTouched:false};
      }
      if(body.action!=='print-submit-unpaid')fail('invalid_input','未知3D动作。');
      const grant=body.submissionAuthorization;
      if(!grant?.approvalId||grant.allowedSteps?.length!==1||grant.allowedSteps[0]!=='submit-print'||!/^[a-f0-9]{64}$/.test(grant.intentSha256||''))fail('print_order_mismatch','缺少本次3D提交Agent复核授权。',409);
      const current=await summary(spec);
      current.batch=current.batch||await nativeBatch();
      if(state.phase!=='checkout-prepared'||current.pathname!=='/submitOrder'||!current.manualConfirmChecked||current.fileNames.length!==spec.files.length||!current.batch||current.batch!==state.batch||!money(current.payable,spec.expectedPayableCny))
        fail('print_order_mismatch','3D结算文件、批次、确认方式或应付总额已变化。',409);
      state.specification=spec;state.phase='submission-started';state.submissionStartedAt=new Date().toISOString();state.approvalId=grant.approvalId;save(state);
      gate={...grant,step:'submit-print',used:false,batch:state.batch,state};
      try{
        const receipt=page.waitForResponse(r=>new URL(r.url()).hostname==='www.jlc-3dp.cn'&&new URL(r.url()).pathname.endsWith('/preorder/submitOrder')&&r.request().method()==='POST',{timeout:45000}).catch(()=>null);
        await page.getByRole('button',{name:'立即下单',exact:true}).click();
        const response=await receipt;
        if(response){
          let body=null;try{body=await response.json();}catch{}
          fs.writeFileSync(path.join(directory,state.key+'-create-response-private.json'),JSON.stringify({at:new Date().toISOString(),status:response.status(),body},null,2));
          state.nativeResponseStatus=response.status();state.nativeResponseCode=body?.code;
          state.responseOrderNumbers=[...new Set(JSON.stringify(body||{}).match(/\bG\d{10,20}\b/g)||[])];save(state);
        }
        const observed=await wait(spec,s=>s.success||s.orderNumbers.length>0);
        const submitted=gate.used&&response?.ok()&&observed.success;
        state.phase=submitted?'submitted-awaiting-observation':'result-needs-reconciliation';
        state.orderNumbers=observed.orderNumbers.length?observed.orderNumbers:state.responseOrderNumbers||[];state.finishedAt=new Date().toISOString();save(state);
        return {...this.status(),orderSubmitted:submitted?true:null,paymentPerformed:false,productionReleased:false,supplierNotesTouched:false};
      }finally{
        if(!gate.used){state.attempts=[...(state.attempts||[]),{startedAt:state.submissionStartedAt,finishedAt:new Date().toISOString(),nativeRequestSent:false}];state.phase='checkout-prepared';save(state);}
        gate=null;
      }
    },
  };
  const execute=controller.command;
  controller.command=async function(body){
    try{return await execute.call(this,body);}catch(error){
      fs.appendFileSync(path.join(directory,'command-errors.jsonl'),JSON.stringify({at:new Date().toISOString(),action:body.action,requestId:body.requestId,message:String(error.message).slice(0,16000)})+'\n');throw error;
    }
  };
  return controller;
}
module.exports={createPrintController,readPage,modelRow};
