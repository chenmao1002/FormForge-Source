const {assemblyCount}=require('./parameters.cjs');
const fs=require('node:fs'),path=require('node:path');
const SMTLIST='https://member.jlc.com/center/smt/home/order/smt_list/';
function smtOrderCard(page,fileName,number){
  if(!/^SMT\d+$/.test(number))throw new Error('缺少唯一SMT订单号。');
  const literal="concat('"+fileName.split("'").join("',\"'\",'")+"','')";
  return page.getByText(new RegExp('订单编号：\\s*'+number+'\\b')).filter({visible:true}).locator('xpath=ancestor::*[contains(.,'+literal+')][1]');
}
async function observeSmt(api,progress) {
  let number=progress.state.smt.displayNumber;
  if(number && !/^SMT\d+$/.test(number))throw new Error('正式SMT订单号无效；不重下订单。');
  if(!number && !progress.state.smt.submitClickedAt)throw new Error('没有本次SMT提交依据；不关联其他订单。');
  let page=api.context.pages().find(p=>p.url().startsWith(SMTLIST));
  if(!page)page=await api.context.newPage();
  await page.goto(SMTLIST,{waitUntil:'domcontentloaded',timeout:45000});api.setPage(page);
  return api.step('observe-existing-smt-order',async()=>{
    const card=smtOrderCard(page,progress.state.fileName,number);
    await card.waitFor({state:'visible',timeout:30000});
    const text=await card.innerText();
    const codes=[...new Set(text.match(/SMT\d{6,}/g)||[])];
    if(codes.length!==1 || (number && codes[0]!==number) || !text.includes(progress.state.pcb.displayNumber))throw new Error('不能唯一确定本次SMT订单卡片。');
    number=codes[0];progress.state.smt.displayNumber=number;progress.state.smt.status='submitted';progress.save();
    const cardCount=Number(text.match(/(\d+)片(?:顶层|底层|双面)样板/)?.[1]);
    if(cardCount!==assemblyCount(api.checkpoint)){
      fs.writeFileSync(path.join(progress.directory,'smt-quantity-card-private.txt'),text);
      progress.state.smt.cardQuantity=cardCount;progress.state.phase='smt-quantity-mismatch';progress.save();
      if(!api.submitRequested || !/等待您支付|待付款/.test(text))throw new Error('实际SMT数量与请求不符；仅可修正本次未付款订单，不重建。');
      progress.state.smt.quantityCorrection={from:cardCount,to:assemblyCount(api.checkpoint),orderNumber:number,startedAt:new Date().toISOString()};progress.save();
      const pending=api.context.waitForEvent('page',{timeout:10000}).catch(()=>null);
      await card.getByText('修改元件',{exact:true}).click();
      const editing=await pending;
      if(editing){await editing.waitForLoadState('domcontentloaded');api.setPage(editing);}
      progress.state.phase='smt-quantity-edit-opened';progress.save();
      return {phase:'smt-quantity-edit-opened',pcbOrderNumber:progress.state.pcb.displayNumber,smtOrderNumber:number,expectedAssemblyQuantity:assemblyCount(api.checkpoint),supplierCardQuantity:cardCount,editOpened:!!editing,paymentPerformed:false};
    }
    const total=Number(text.match(/[￥¥]\s*([\d.]+)/)?.[1]);
    if(!total || total!==progress.state.smt.total)throw new Error('正式SMT订单金额与提交时不一致。');
    const awaiting=text.includes('等待您支付') || text.includes('待付款');
    const underReview=text.includes('等待审核');
    const observation={observedAt:new Date().toISOString(),kind:'agent-observed-browser-ui',fileName:progress.state.fileName,
      pcbOrderNumber:progress.state.pcb.displayNumber,smtOrderNumber:number,total,currency:'CNY',quantity:assemblyCount(api.checkpoint),pcbQuantity:api.checkpoint.quantity,side:'top',
      reviewStatus:awaiting?'approved':underReview?'under-review':'unknown',paymentStatus:awaiting?'unpaid':'unknown',
      awaitingPaymentObserved:awaiting,providerStatusLabel:awaiting?'等待您支付':underReview?'等待审核':'未识别',
      directionService:'engineer-assistance',dfmStatus:'pending',directionStatus:'pending',paymentPerformed:false,productionReleased:false};
    fs.writeFileSync(path.join(progress.directory,'smt-native-order-card-private.txt'),text);
    fs.writeFileSync(path.join(progress.directory,'smt-native-observation.json'),JSON.stringify(observation,null,2));
    await card.screenshot({path:path.join(progress.directory,'smt-native-order-card.png')});
    api.event('smt-native-order-observed',observation);
    progress.state.smt.observation=observation;progress.state.phase=awaiting?'awaiting-payment':'waiting-smt-review';progress.save();
    return {phase:progress.state.phase,pcbOrderNumber:observation.pcbOrderNumber,smtOrderNumber:number,assemblyTotal:total,currency:'CNY',pcbOrderSubmitted:true,smtOrderSubmitted:true,awaitingPaymentObserved:awaiting,paymentPerformed:false,productionReleased:false};
  });
}
module.exports={observeSmt,smtOrderCard};
