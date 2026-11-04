delete require.cache[require.resolve('./parameters.cjs')];
const {assemblyCount,verifyAssembly}=require('./parameters.cjs');
delete require.cache[require.resolve('./bom-review.cjs')];
const {selectedRow,packageEquivalent,bomRow,verifySettlementQuantity}=require('./bom-review.cjs');
const fs=require('node:fs'),path=require('node:path');
const {expect}=require('playwright/test');
async function continueSmt(api,progress) {
  const page=api.getPage();
  if(!api.submitRequested)throw new Error('SMT提交未明确授权。');
  return api.step('submit-smt',async()=>{
    const finishMode=page.getByRole('button',{name:'完成匹配，查看3D图',exact:true});
    if(await finishMode.isVisible().catch(()=>false))await finishMode.click();
    if(await page.getByText('费用明细',{exact:true}).isVisible().catch(()=>false))await page.getByText('SMT下单',{exact:true}).click();
    const body=await page.locator('body').innerText();
    const total=Number(body.match(/网络支付总费用\s*(?:\(仅供参考\))?\s*[:：]?\s*[￥¥]\s*([\d,.]+)/)?.[1]?.replace(/,/g,''));
    if(!body.includes(progress.state.fileName) || !body.includes(progress.state.pcb.displayNumber) || total!==progress.state.assemblyQuote.total)throw new Error('最终PCB身份或费用与报价不一致。');
    await expect(page.getByLabel('单板',{exact:true})).toBeChecked();
    await verifyAssembly(page,api.checkpoint.quantity,assemblyCount(api.checkpoint));
    const checked=[];
    for(const part of api.parts) {
      const row=bomRow(page,part);
      await expect(row).toHaveCount(1);
      const cells=await row.locator('td').allInnerTexts();
      selectedRow(cells,part,assemblyCount(api.checkpoint));
      api.event('smt-final-group-cells',{supplierCode:part.supplierCode,cells});
      const refs=(cells[1]||'').match(/\b[A-Z]+\d+\b/g)||[];
      if(JSON.stringify(refs.sort())!==JSON.stringify([...part.designators].sort()))throw new Error('最终位号核对不一致。');
      const packageLines=cells[4]?.split('\n').map(s=>s.trim()).filter(Boolean)||[];
      const packages=[packageLines[0],packageLines[1] || cells[5]?.match(/封装[：:]\s*([^\n]+)/)?.[1]?.trim()];
      if(!packageEquivalent(packages[1],packages[0],part))throw new Error('原始与选定封装不一致：'+part.supplierCode);
      if(part.mounting==='through-hole' && !/插装/.test(await row.innerText()))throw new Error('排针没有保留插装。');
      await row.locator('input[type=checkbox]').check();
      checked.push({supplierCode:part.supplierCode,mpn:part.mpn,refs,packages});
    }
    await expect(page.getByText(`${api.parts.length}/${api.parts.length}`,{exact:true})).toBeVisible();
    fs.writeFileSync(path.join(progress.directory,'smt-final-matching.json'),JSON.stringify(checked,null,2));
    progress.state.checkedReferences=checked.flatMap(p=>p.refs);progress.save();
    api.event('all-assembly-groups-checked',{groups:api.parts.length,references:checked.flatMap(p=>p.refs).length,throughHoleComponents:api.parts.filter(p=>p.mounting==='through-hole').reduce((n,p)=>n+p.perBoard,0)*assemblyCount(api.checkpoint)});
    const next=page.getByText('备料完成，下一步',{exact:true});
    api.event('smt-next-step-markup',{html:await next.evaluate(e=>e.outerHTML)});
    const simple=page.getByText('切到简易模式',{exact:true});
    if(await simple.isVisible().catch(()=>false))await simple.click();
    await page.getByText(/^生产时间(?:2-3|3-4)天$/).click();
    progress.begin('smt');
    progress.state.smt.nextClickedAt=new Date().toISOString();progress.save();
    const nativeFee=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/countSmtOrderFee'),{timeout:45000}).catch(()=>null);
    await next.click();
    const response=await nativeFee;
    if(!response)throw new Error('未取得供应商结算数量，未正式提交SMT。');
    const saved=await response.json(),actualQuantity=Number(saved.data?.allPatchNum);
    api.event('smt-settlement-quantity',{expectedQuantity:assemblyCount(api.checkpoint),actualQuantity,code:saved.code});
    fs.writeFileSync(path.join(progress.directory,'smt-settlement-quantity-private.json'),JSON.stringify(saved,null,2));
    verifySettlementQuantity(saved,assemblyCount(api.checkpoint));
    progress.state.smt.verifiedSettlementQuantity=actualQuantity;progress.save();
    await page.screenshot({path:path.join(progress.directory,'smt-after-next-private.png'),fullPage:false});
    progress.state.phase='smt-next-step';progress.save();
    return {phase:'smt-next-step',pcbOrderNumber:progress.state.pcb.displayNumber,pcbOrderSubmitted:true,smtOrderSubmitted:false,paymentPerformed:false,productionReleased:false};
  });
}
async function inspectConfirmation(api,progress) {
  const page=api.getPage();
  if(await page.getByText('极性选择',{exact:true}).isVisible().catch(()=>false))return nativeConfirmation(api,progress,page);
  if(!progress.state.smt.submitClickedAt && await page.getByText('备料完成，下一步',{exact:true}).isVisible().catch(()=>false)){
    api.event('smt-preconfirmation-reconciled',{reason:'Matching page is still visible; no formal order submit was clicked.'});
    progress.state.smt.status='matching-resume';progress.save();
    return continueSmt(api,progress);
  }
  if(await page.getByText('PCB订单审核中',{exact:true}).isVisible().catch(()=>false)) {
    progress.state.smt.status='waiting-pcb-review';
    progress.state.smt.orderSubmitted=false;progress.state.smt.savedMatchingObservedAt=new Date().toISOString();
    progress.state.phase='waiting-pcb-review';progress.save();
    await page.getByText('去查看审核状态',{exact:true}).click();
    return {phase:'waiting-pcb-review',pcbOrderNumber:progress.state.pcb.displayNumber,pcbOrderSubmitted:true,smtOrderSubmitted:false,matchingSaved:true,paymentPerformed:false};
  }
  throw new Error('当前页面不是已验证的可见SMT确认流程；未重放提交。');
}
async function nativeConfirmation(api,progress,page) {
  if(progress.state.smt.nativeReadyAt)return submitNativeSmt(api,progress,page);
  return api.step('prepare-native-smt-confirmation',async()=>{
    const engineer=page.getByLabel('需要嘉立创工程师依据丝印，协助确认或者修正方向。（收费10元）',{exact:true});
    await engineer.check();await expect(engineer).toBeChecked();
    const amount=async()=>Number((await page.locator('body').innerText()).match(/网络支付总费[：:]\s*￥\s*([\d.]+)/)?.[1]);
    await expect.poll(amount,{timeout:30000}).toBeGreaterThan(progress.state.assemblyQuote.total);
    const total=await amount();
    if(total-progress.state.assemblyQuote.total<10 || total-progress.state.assemblyQuote.total>12)throw new Error('方向服务后费用变化超出当前原生10元服务及税费范围。');
    const agreement=page.getByText('阅读并接受《SMT贴片焊接质量责任认定及承担的说明》',{exact:true});
    api.event('smt-agreement-markup',{html:await agreement.locator('..').evaluate(e=>e.outerHTML.slice(0,5000))});
    progress.state.smt.nativeReadyAt=new Date().toISOString();progress.state.smt.total=total;
    progress.state.smt.directionService='engineer-assistance';progress.state.smt.directionServiceBaseFee=10;
    progress.save();
    fs.writeFileSync(path.join(progress.directory,'smt-native-confirmation-private.txt'),await page.locator('body').innerText());
    return {phase:'smt-native-confirmation-ready',pcbOrderNumber:progress.state.pcb.displayNumber,assemblyTotal:total,currency:'CNY',pcbOrderSubmitted:true,smtOrderSubmitted:false,paymentPerformed:false};
  });
}
async function submitNativeSmt(api,progress,page) {
  if(progress.state.smt.submitClickedAt)throw new Error('SMT正式提交结果须先查单，不重放提交。');
  return api.step('submit-smt',async()=>{
    if(progress.state.smt.verifiedSettlementQuantity!==assemblyCount(api.checkpoint))throw new Error('缺少供应商实际结算数量证明，未正式提交SMT。');
    await expect(page.locator('input[name="treatment-radio"][value="4"]')).toBeChecked();
    await verifyAssembly(page,api.checkpoint.quantity,assemblyCount(api.checkpoint));
    await page.getByLabel('阅读并接受《SMT贴片焊接质量责任认定及承担的说明》',{exact:true}).check();
    const native=await page.locator('body').innerText();
    const total=Number(native.match(/网络支付总费[：:]\s*￥\s*([\d.]+)/)?.[1]);
    if(total!==progress.state.smt.total || !native.includes(progress.state.fileName))throw new Error('正式提交前文件或费用发生变化。');
    const response=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/saveSmtOrder'),{timeout:45000}).catch(()=>null);
    progress.state.smt.submitClickedAt=new Date().toISOString();progress.save();
    try {await page.getByRole('button',{name:'提交订单',exact:true}).click();}
    catch(error){api.event('smt-native-click-failed',{reason:error.message.slice(0,6000)});throw error;}
    const result=await response;
    if(!result)throw new Error('SMT已点击正式提交但结果未定；先查既有订单，不重试提交。');
    const body=await result.json();
    fs.writeFileSync(path.join(progress.directory,'smt-create-response-private.json'),JSON.stringify({at:new Date().toISOString(),status:result.status(),body},null,2));
    if(body.code!==200)throw new Error('SMT提交响应需核对：'+body.code+' '+body.message);
    progress.state.smt.status='submitted';progress.state.smt.createResponse=body.data;progress.save();
    api.event('smt-order-created',{responseKeys:Object.keys(body.data||{}),total});
    await expect.poll(()=>page.url(),{timeout:30000}).not.toContain('/smt-order-');
    api.setPage(page);
    fs.writeFileSync(path.join(progress.directory,'smt-after-create-private.txt'),await page.locator('body').innerText());
    await page.screenshot({path:path.join(progress.directory,'smt-after-create-private.png'),fullPage:false});
    return {phase:'smt-submitted-unpaid-verification',pcbOrderNumber:progress.state.pcb.displayNumber,assemblyTotal:total,currency:'CNY',pcbOrderSubmitted:true,smtOrderSubmitted:true,paymentPerformed:false};
  });
}
module.exports={continueSmt,inspectConfirmation};
