const fs=require('node:fs'),path=require('node:path');
async function openCorrection(api,progress){
 const page=api.getPage(),smt=progress.state.smt;
 if(!api.submitRequested || !smt.quantityCorrection || smt.displayNumber!==smt.quantityCorrection.orderNumber)throw new Error('缺少已核对的未付款数量修正依据。');
 const body=await page.locator('body').innerText();
 if(!body.includes(smt.displayNumber)||!body.includes(progress.state.fileName)||!body.includes('原单将会取消'))throw new Error('本次修改入口身份或取消说明不符。');
 return api.step('open-existing-smt-quantity-correction',async()=>{
  const opening=api.context.waitForEvent('page',{timeout:30000}).catch(()=>null);
  await page.getByRole('button',{name:'开始修改',exact:true}).click();
  const target=await opening;
  if(!target)throw new Error('修改入口未返回匹配页，须先查原单状态，不再次点击。');
  await target.waitForLoadState('domcontentloaded');
  await target.getByText(progress.state.fileName,{exact:true}).waitFor({state:'visible',timeout:45000});
  api.setPage(target);
  progress.state.previousSmtOrders=[...(progress.state.previousSmtOrders||[]),{...smt,modificationStartedAt:new Date().toISOString(),nativeModificationCancelsOriginal:true}];
  progress.state.smt={status:'quantity-correction-matching',previousOrderNumber:smt.displayNumber,expectedQuantity:smt.quantityCorrection.to};
  progress.state.phase='quantity-correction-matching';progress.save();
  const scripts=await target.locator('script[src]').evaluateAll(es=>es.map(e=>e.src).filter(s=>/\.js(?:\?|$)/.test(s)));
  fs.writeFileSync(path.join(progress.directory,'matching-public-scripts.json'),JSON.stringify(scripts,null,2));
  return {phase:progress.state.phase,previousSmtOrderNumber:smt.displayNumber,pcbOrderNumber:progress.state.pcb.displayNumber,expectedAssemblyQuantity:smt.quantityCorrection.to,paymentPerformed:false};
 });
}
module.exports={openCorrection};
