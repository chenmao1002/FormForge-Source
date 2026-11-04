delete require.cache[require.resolve('./parameters.cjs')];
const {assemblyCount,selectAssembly}=require('./parameters.cjs');
delete require.cache[require.resolve('./bom-review.cjs')];
const {originalRow,selectedRow,bomRow}=require('./bom-review.cjs');
// Fixed quotation workflow. All browser mutations use Playwright locators.
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {expect}=require('playwright/test');
const {safeUrl}=require('../desktop/policy.cjs');
function assert(value,message){if(!value)throw new Error(message);}
async function run(api) {
  const {context,files,parts,step,event,checkpoint,save,local,commit,sensitive}=api;
  const pcb=api.pcb || {code:'Y1',fileName:'嘉立创SMT流程演示订单',kind:'supplier-demo',sizeMm:null};
  let page=api.getPage();
  const uploadState=async()=>page.locator('input[type=file]').evaluateAll(elements=>elements.map(e=>({
    input:e.outerHTML,files:[...e.files].map(f=>({name:f.name,size:f.size})),
    area:e.parentElement?.parentElement?.parentElement?.innerText?.slice(0,700)})));
  event('observed-upload-inputs',{inputs:await uploadState()});
  const demoRow=()=>page.locator('tr').filter({hasText:pcb.fileName})
    .filter({hasText:'选择此单'});
  const text=()=>page.locator('body').innerText();
  const click=async locator=>{
    assert(api.canAct(),'任务已交人工接管。');
    const label=(await locator.innerText()).trim();
    assert(!commit(label)&&!/备料完成|确认生产|支付订单/.test(label),'仅报价：拒绝提交、付款或生产控件。');
    await locator.click();
  };
  const labels=label=>page.locator('label').filter({hasText:new RegExp('^\\s*'+label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\s*$')});
  const radio=async label=>{
    const chosen=(await labels(label).count())===1?labels(label):page.getByText(label,{exact:true});
    if(await chosen.locator('input[type=radio]').count()===1 && await chosen.locator('input[type=radio]').isChecked())return;
    await click(chosen);
  };
  const login=async()=>{
    // The page initially renders a logged-out placeholder while SSO resolves.
    // Wait for authenticated business content, rather than sampling that frame.
    try {
      if(/\/smt-order-/.test(page.url()))await page.getByText('BOM详情模式',{exact:true}).waitFor({state:'visible',timeout:20000});
      else await demoRow().waitFor({state:'visible',timeout:20000});
    }catch {
      if(/passport|\/login/.test(page.url()) || await page.getByText('您还没有登录',{exact:false}).isVisible().catch(()=>false))
      throw Object.assign(new Error('独立浏览器需要用户登录。'),{code:'human_control'});
      throw new Error('供应商业务内容未就绪，未执行上传。');
    }
  };
  if(/\/smt-order-/.test(page.url())) {
    try {
      await page.getByText(pcb.fileName,{exact:true}).waitFor({state:'visible',timeout:12000});
      await page.locator('tr').filter({hasText:parts[0].supplierCode}).waitFor({state:'visible',timeout:12000});
    }catch {
      const existing=await text();
      assert(!/全部元件\s*[（(][1-9]\d*[)）]/.test(existing),'恢复页存在其他BOM，不自动覆盖。');
      const previous=page;
      page=context.pages().find(p=>p.url()==='https://www.jlc-smt.com/order') || await context.newPage();
      if(page.url()==='about:blank')await page.goto('https://www.jlc-smt.com/order',{waitUntil:'domcontentloaded'});
      api.setPage(page);await previous.close();
      event('business-restoration-rebuild',{reason:'Supplier POST-created matching state was not retained; rebuild from frozen files.'});
    }
  }
  if(!/\/smt-order-/.test(page.url()) && checkpoint.previousRunId) {
    // A failed form can retain native file inputs after Vue cleared its model.
    // Rebuild the disposable quotation form from the frozen input snapshot.
    await step('rebuild-quotation-form',async()=>page.reload({waitUntil:'domcontentloaded',timeout:45000}));
  }
  await step('login-check',login);
  const matching=()=>/\/smt-order-/.test(page.url());
  if(!matching()) {
    await step('select-pcb',async()=>{
      const demo=demoRow();
      await demo.waitFor({state:'visible',timeout:30000});
      assert(await demo.count()===1,'未唯一定位指定PCB；不选择其他订单。');
      assert((await demo.locator('td').nth(1).innerText()).trim()===pcb.code,'PCB身份与已核对订单不符。');
      checkpoint.pcbBasis={kind:pcb.kind,code:pcb.code,observedRow:(await demo.innerText()).replace(/1[3-9]\d{9}/g,'[电话]'),actualBoardMm:pcb.sizeMm};save();
      const selected=demo.getByText('已选择此单',{exact:true});
      if(!await selected.isVisible())await click(demo.getByText('选择此单',{exact:true}));
    });
    await step('upload-original-bom-cpl',async()=>{
      await click(page.getByText('上传BOM和坐标',{exact:true}));
      // Set CAD origin before uploads: changing it later clears the vendor model.
      await click(page.getByText('AD,99SE,Allegro,PADS(宏)等其他',{exact:true}));
      const inputs=page.locator('input[type=file]');
      assert(await inputs.count()===2,'BOM/坐标上传入口与已核对页面不符。');
      for(let i=0;i<1;i++) {
        assert(api.canAct(),'任务已交人工接管。');
        api.checkedFiles(api.root,[files[i]]);
        const chooser=page.waitForEvent('filechooser',{timeout:15000}).catch(()=>null);
        await click(page.getByRole('button',{name:'上传文件',exact:true}).nth(i));
        const dialog=await chooser;assert(dialog,'供应商未打开BOM文件选择入口。');
        await dialog.setFiles(files[i].path);
        event('observed-upload-inputs',{inputs:await uploadState()});
        await page.getByText(path.basename(files[i].path),{exact:false}).first().waitFor({state:'visible',timeout:30000});
        event('file-uploaded',{name:path.basename(files[i].path),sha256:files[i].sha256});
      }
    });
    await step('set-assembly-options',async()=>{
      await radio('只贴顶层');await radio('标准型');
      await radio('工业控制');
      await radio('需要（收费3元）');
      // Move focus away from the DFM help balloon without touching CAD origin.
      await click(page.getByText('无要求',{exact:true}).first());
      await radio('不允许系统自动确认');
      await radio('需要（收费20元）');
      checkpoint.options={quantity:assemblyCount(checkpoint),pcbQuantity:checkpoint.quantity,side:'top',service:'standard',dfmReview:true,
        autoDfmConfirmation:false,stencilReview:true,cadSource:'other/KiCad'};save();
    });
    await step('upload-cpl-after-layer-selection',async()=>{
      api.checkedFiles(api.root,[files[1]]);
      const inputs=page.locator('input[type=file]');
      assert(await inputs.count()===2,'坐标文件上传入口发生变化。');
      const chooser=page.waitForEvent('filechooser',{timeout:15000}).catch(()=>null);
      await click(page.getByRole('button',{name:'上传文件',exact:true}).nth(1));
      const dialog=await chooser;assert(dialog,'供应商未打开坐标文件选择入口。');
      await dialog.setFiles(files[1].path);
      event('observed-upload-inputs',{inputs:await uploadState()});
      await page.getByText(path.basename(files[1].path),{exact:false}).first().waitFor({state:'visible',timeout:30000});
      // Wait until vendor parsing has rendered the layer selection and filename.
      await page.locator('label').filter({hasText:/^\s*只贴顶层\s*$/}).waitFor({state:'visible'});
      event('file-uploaded',{name:path.basename(files[1].path),sha256:files[1].sha256});
    });
    await step('open-matching-page',async()=>{
      const popup=context.waitForEvent('page',{timeout:30000}).catch(()=>null);
      await click(page.getByRole('button',{name:'下一步，BOM匹配选型',exact:true}));
      const opened=await popup;
      assert(opened,'供应商未打开匹配页；保留当前表单等待核对。');
      page=opened;api.setPage(page);
      await page.waitForURL(/\/smt-order-/,{waitUntil:'domcontentloaded',timeout:45000});
      await page.getByText('BOM详情模式',{exact:true}).waitFor({state:'visible',timeout:45000});
      checkpoint.recoveryUrls=context.pages().map(p=>p.url()).filter(u=>u.startsWith('https://www.jlc-smt.com/'));save();
    });
  }
  await step('verify-uploaded-bom',async()=>{
    if(await page.getByText('费用明细',{exact:true}).isVisible().catch(()=>false))await page.getByText('SMT下单',{exact:true}).click();
    await page.getByText(pcb.fileName,{exact:true}).waitFor({state:'visible',timeout:30000});
    await page.locator('tr').filter({hasText:parts[0].supplierCode}).waitFor({state:'visible',timeout:30000});
    const body=await text();
    assert(body.includes(pcb.fileName),'当前匹配页不是本次指定PCB。');
    for(const part of parts){const row=bomRow(page,part),count=await row.count();event('bom-row-identity',{supplierCode:part.supplierCode,count,candidates:await row.evaluateAll(es=>es.slice(0,3).map(e=>[...e.querySelectorAll(':scope > td')].map(c=>c.innerText)))});assert(count===1,'匹配页未唯一定位原BOM器件：'+part.supplierCode+' ('+count+')');originalRow(await row.locator('td').allInnerTexts(),part);}
    assert(new RegExp('全部元件\\s*[（(]'+parts.length+'[)）]').test(body),'BOM组数不符。');
    event('bom-coverage-checked',{groups:parts.length,refs:parts.reduce((n,p)=>n+p.designators.length,0)});
  });
  await step('set-final-quantity-and-cpl-basis',async()=>{
    event('choice-markup',{labels:await page.locator('label').filter({hasText:/单板|片全贴/}).evaluateAll(es=>es.map(e=>e.outerHTML))});
    const single=labels('单板');
    const input=page.getByLabel('单板',{exact:true});
    if(!await input.isChecked()) {
      const rebuilt=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/viewSmtCacheBomFileListV2Result'),{timeout:45000}).catch(()=>null);
      await click(single);
      const confirmation=page.getByText('修改后元器件 用量 和 费用 将发生变化。',{exact:true});
      await confirmation.waitFor({state:'visible'});
      await click(page.getByRole('button',{name:'是',exact:true}));
      await confirmation.waitFor({state:'hidden'});
      const result=await rebuilt;assert(result && (await result.json()).code===200,'单板坐标基准未完成供应商保存。');
    }
    await expect(input).toBeChecked({timeout:20000});
    const expectedQuantity=assemblyCount(checkpoint);
    const quantitySaved=expectedQuantity!==checkpoint.quantity?page.waitForResponse(r=>{
      if(!new URL(r.url()).pathname.endsWith('/viewSmtCacheBomFileListV2Result'))return false;
      try{return Number(r.request().postDataJSON()?.allPatchNum)===expectedQuantity;}catch{return false;}
    },{timeout:45000}).catch(()=>null):null;
    await selectAssembly(page,checkpoint.quantity,assemblyCount(checkpoint));
    if(quantitySaved){
      const response=await quantitySaved;assert(response,'SMT数量未保存到供应商，不能只看输入框。');
      const saved=await response.json();
      const counts=[];const collect=(v,path='data')=>{if(v&&typeof v==='object')for(const [key,item]of Object.entries(v)){if(key==='allPatchNum')counts.push({path:path+'.'+key,value:item});else if(item&&typeof item==='object')collect(item,path+'.'+key);}};collect(saved.data);
      event('assembly-quantity-server-saved',{expectedQuantity,code:saved.code,dataKeys:Object.keys(saved.data||{}),counts});
      fs.writeFileSync(path.join(local,'runs',checkpoint.runId,'quantity-server-response-private.json'),JSON.stringify(saved,null,2));
      assert(saved.code===200 && counts.every(c=>Number(c.value)===expectedQuantity),'供应商未接受指定贴片数量。');
      checkpoint.assemblyQuantitySaved={requestedQuantity:expectedQuantity,observedAt:new Date().toISOString(),acknowledged:true,echoCounts:counts};save();
    }
    checkpoint.options={...checkpoint.options,cplBasis:'single-board',panel:[1,1]};save();
    const ordinary=page.getByText('生产时间3-4天',{exact:true});
    if(await ordinary.isVisible().catch(()=>false))await click(ordinary);
  });
  await step('match-supplier-stock',async()=>{
    checkpoint.replacements=checkpoint.replacements || {};
    for(const part of parts) {
      const row=bomRow(page,part);
      assert(await row.count()===1,'未唯一定位BOM分组：'+part.supplierCode);
      const rowText=await row.innerText();
      const refs=(await row.locator('td').nth(1).innerText()).match(/\b[A-Z]+\d+\b/g) || [];
      assert(JSON.stringify([...refs].sort())===JSON.stringify([...part.designators].sort()),'分组位号不符：'+part.supplierCode);
      assert(part.perBoard===refs.length,'原BOM数量与位号不符：'+part.supplierCode);
      if(!/私有库\s*[:：]/.test(rowText)){const verified=selectedRow(await row.locator('td').allInnerTexts(),part,assemblyCount(checkpoint));event('supplier-stock-verified',{supplierCode:part.supplierCode,stock:verified.stock});continue;}
      let selector=await openSelector(context);
      if(!selector) {
        await click(row.getByRole('button',{name:'替换',exact:true}));
        selector=await findSelector(context);
      }
      await click(selector.getByRole('button',{name:'嘉立创SMT',exact:true}));
      const search=selector.getByPlaceholder('多个关键词用空格隔开，例：10K 0603 电阻');
      assert(api.canAct(),'任务已交人工接管。');await search.fill(part.supplierCode);
      await click(selector.getByRole('button',{name:'搜索',exact:true}));
      const code=selector.getByText(part.supplierCode,{exact:true});
      await code.first().waitFor({state:'visible',timeout:30000});
      await expect.poll(async()=>(await selectableCards(selector,part)).length,{timeout:20000}).toBeGreaterThan(0);
      const candidates=await selectableCards(selector,part);
      const {card,text:supplierText}=candidates[0];
      assert(supplierText.includes(part.mpn),'同料号型号与原BOM不同。');
      const stock=Number(supplierText.match(/可用库存数\s*[:：]\s*([\d,]+)/)?.[1].replace(/,/g,''));
      assert(stock>=part.perBoard*assemblyCount(checkpoint),'原料号供应库存不足。');
      checkpoint.replacements[part.supplierCode]={observedAt:new Date().toISOString(),stock,
        selectorText:supplierText,required:part.perBoard*assemblyCount(checkpoint)};save();
      await click(card.getByRole('button',{name:'选用这个元件',exact:true}));
      await row.getByText(/私有库\s*[:：]/).waitFor({state:'hidden'});
      event('supplier-stock-selected',{supplierCode:part.supplierCode,mpn:part.mpn,stock});
    }
    const body=await text();
    assert(/私有库存\s*[（(]0[)）]/.test(body),'仍有私有库存，报价未覆盖全部新购物料。');
    assert(new RegExp('嘉立创库存\\s*[（(]'+parts.length+'[)）]').test(body),'供应商供料组数不完整。');
  });
  await step('download-bom-review',async()=>{
    await expect.poll(async()=>Number((await text()).match(/网络支付总费用\s*(?:\(仅供参考\))?\s*[:：]?\s*[￥¥]\s*([\d,.]+)/)?.[1]?.replace(/,/g,'')),{timeout:60000}).toBeGreaterThan(0);
    const download=page.waitForEvent('download',{timeout:45000});
    await click(page.getByText('下载结果(PDF)',{exact:true}));
    const file=await download;
    const destination=path.join(local,'quotes',checkpoint.runId,'provider-bom-review.pdf');
    fs.mkdirSync(path.dirname(destination),{recursive:true});await file.saveAs(destination);
    checkpoint.providerPdf={path:destination,sha256:crypto.createHash('sha256').update(fs.readFileSync(destination)).digest('hex')};save();
  });
  return step('read-complete-quote',async()=>{
    if(!await page.getByText('费用明细',{exact:true}).isVisible().catch(()=>false))await click(page.getByText('[明细]',{exact:true}));
    await page.getByText('费用明细',{exact:true}).waitFor({state:'visible'});
    const body=await text();
    const amount=name=>{
      const found=body.match(new RegExp(name+'\\s*(?:\\(仅供参考\\))?\\s*[:：]?\\s*[￥¥]\\s*([\\d,.]+)'));
      assert(found,'缺少费用：'+name);return Number(found[1].replace(/,/g,''));
    };
    const total=amount('网络支付总费用'),components=amount('元件费'),processing=amount('加工费');
    assert(total>0 && components>0 && processing>0,'平台尚未给出有效费用；零元不能作为完整报价。');
    assert(Math.abs(total-components-processing)<0.011,'总费用与元件费、加工费不一致。');
    const rows=await page.locator('tr').evaluateAll(elements=>elements.map(e=>e.innerText));
    const displayedSize=body.match(/尺寸\s*[:：]\s*([\d.]+)\s*\*\s*([\d.]+)\s*CM/i);
    const designSizeMatches=pcb.kind!=='supplier-demo' && displayedSize &&
      Number(displayedSize[1])*10===pcb.sizeMm[0] && Number(displayedSize[2])*10===pcb.sizeMm[1];
    let panelObservation=null;
    if(pcb.kind!=='supplier-demo' && !designSizeMatches && displayedSize){
      const current=await require('./order-submit.cjs').findPcb(api,pcb.fileName);api.setPage(page);
      const observed=current.row.match(/([\d.]+)\s*CM\s*[*×]\s*([\d.]+)\s*CM/i);
      assert(current.displayNumber===pcb.code && observed && observed.slice(1).every((n,i)=>Number(n)===Number(displayedSize[i+1])),'SMT生产尺寸与同一PCB订单不一致。');
      panelObservation={pcbOrderNumber:current.displayNumber,designBoardMm:pcb.sizeMm,supplierPanelMm:observed.slice(1).map(n=>Number(n)*10),source:'current-supplier-pcb-order-row',observedAt:new Date().toISOString(),finishedBoardSizeVerified:false};
      checkpoint.pcbPanelObservation=panelObservation;save();event('supplier-production-panel-observed',panelObservation);
    }
    const actualSizeVerified=!!(designSizeMatches||panelObservation);
    if(pcb.kind!=='supplier-demo')assert(actualSizeVerified,'SMT平台尺寸不可核对。');
    const summary={observedAt:new Date().toISOString(),kind:pcb.kind==='supplier-demo'?'supplier-demo-full-assembly-trial':'actual-pcb-assembly-quote',
      projectId:checkpoint.projectId,projectVersion:checkpoint.projectVersion,quantity:assemblyCount(checkpoint),pcbQuantity:checkpoint.quantity,side:'top',
      cplBasis:'single-board',sourceGroups:parts.length,refsPerBoard:parts.reduce((n,p)=>n+p.designators.length,0),
      supplierGroups:parts.length,privateGroups:0,currency:'CNY',componentCost:components,
      processingCost:processing,total,feeText:body.slice(body.indexOf('费用明细')),rows,
      actualBoardMm:pcb.sizeMm,supplierPcbBasis:checkpoint.pcbBasis,designSizeMatches:!!designSizeMatches,panelObservation,
      supplierDisplayedSize:body.match(/尺寸\s*[:：]\s*[\d.]+\s*\*\s*[\d.]+\s*CM/i)?.[0] || null,
      replacements:checkpoint.replacements,options:checkpoint.options,providerPdf:checkpoint.providerPdf,
      shippingKnown:false,actualBoardManufacturingPriceVerified:!!actualSizeVerified,
      orderSubmitted:false,paymentPerformed:false,productionReleased:false};
    const evidence=path.join(local,'quotes',checkpoint.runId);fs.mkdirSync(evidence,{recursive:true});
    fs.writeFileSync(path.join(evidence,'quote.json'),JSON.stringify(summary,null,2));
    await page.screenshot({path:path.join(evidence,'quote.png'),fullPage:true,mask:[page.locator('header')]});
    event('quotation-captured',{componentCost:components,processingCost:processing,total});
    return {total,componentCost:components,processingCost:processing,currency:'CNY',
      sourceGroups:parts.length,privateGroups:0,evidenceDirectory:evidence,
      actualBoardManufacturingPriceVerified:!!actualSizeVerified,quoteBasis:pcb.kind};
  });
}
async function findSelector(context) {
  const until=Date.now()+15000;
  while(Date.now()<until) {
    const found=await openSelector(context);if(found)return found;
    await new Promise(r=>setTimeout(r,200));
  }
  throw new Error('元器件替换页面未加载。');
}
async function openSelector(context) {
  for(const p of context.pages())for(const f of p.frames())
    if(/\/order\/replace/.test(f.url()) && await f.getByPlaceholder('多个关键词用空格隔开，例：10K 0603 电阻').isVisible().catch(()=>false))return f;
  return null;
}
async function selectableCards(selector,part) {
  const code=selector.getByText(part.supplierCode,{exact:true});
  const containers=code.locator('xpath=ancestor::*[.//button[contains(normalize-space(.),"选用这个元件")]][1]');
  const candidates=[];
  for(const card of await containers.all()) {
    const content=await card.innerText();
    if(content.includes(part.mpn) && /可用库存数/.test(content) && await card.getByRole('button',{name:'选用这个元件',exact:true}).count()===1)
      candidates.push({card,text:content});
  }
  return candidates.sort((a,b)=>a.text.length-b.text.length);
}
module.exports={run};
