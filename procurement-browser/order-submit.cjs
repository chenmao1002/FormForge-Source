const {verifyReview}=require('./parameters.cjs');
const fs=require('node:fs'),path=require('node:path');
const {expect}=require('playwright/test');
const {safeUrl}=require('../desktop/policy.cjs');
const ENTRY='https://www.jlc-smt.com/order';
async function findPcb(api,name) {
  let smt=api.context.pages().find(p=>p.url()===ENTRY);
  if(!smt)smt=await api.context.newPage();
  await smt.goto(ENTRY,{waitUntil:'domcontentloaded'});
  const search=smt.getByPlaceholder('可输入PCB文件名或PCB编号查询');
  await search.waitFor({state:'visible',timeout:30000});
  await search.fill(name);
  const rows=smt.locator('tr').filter({hasText:name});
  await expect.poll(async()=>{
    await smt.getByRole('button',{name:/^查\s*询$/}).click();
    return await rows.count();
  },{timeout:90000,intervals:[1000,2000,4000,8000]}).toBeGreaterThan(0);
  await expect(rows).toHaveCount(1);
  const row=rows.first(),cells=await row.locator('td').allInnerTexts();
  const code=cells[1]?.trim();
  if(!code || code==='Y1' || !/^Y[0-9]+$/.test(code))throw new Error('未读取到实际PCB订单号。');
  api.setPage(smt);
  return {displayNumber:code,fileName:name,observedAt:new Date().toISOString(),
    row:await row.innerText(),page:smt};
}
async function submitPcb(api,page,progress) {
  if(!api.submitRequested)throw new Error('订单提交未明确授权。');
  const review=page.getByText('订单检查',{exact:true}).filter({visible:true})
    .locator('xpath=ancestor::*[.//button[contains(normalize-space(.),"确认并提交")]][1]');
  const reviewText=await review.innerText();
  if(/去填写|[1-9]\d*\s*项未填写|[1-9]\d*\s*项待检查/.test(reviewText))throw new Error('原生订单复核仍有未完成项目，未提交。');
  if(!reviewText.includes(progress.state.fileName) || !reviewText.includes('手动确认订单') || !reviewText.includes('无铅喷锡'))throw new Error('原生复核文件或付款/工艺条件不符，未提交。');
  verifyReview(reviewText,api.checkpoint.quantity,api.checkpoint.board);
  fs.writeFileSync(path.join(progress.directory,'pcb-final-review-private.txt'),reviewText);
  const manual=page.getByRole('button',{name:'手动确认订单',exact:true,includeHidden:true});
  if(!/\bchecked\b/.test(await manual.getAttribute('class')))throw new Error('自动扣款未明确关闭，未提交。');
  const observed=[];
  const capture=async response=>{
    const request=response.request();
    if(request.method()!=='POST' || !/\.jlc\.com\//.test(response.url()) || !/order|pcb/i.test(new URL(response.url()).pathname))return;
    try {const body=await response.json();observed.push({url:safeUrl(response.url()),status:response.status(),body});}
    catch { }
  };
  const remember=()=>fs.writeFileSync(path.join(progress.directory,'pcb-submit-network-private.json'),JSON.stringify(observed,null,2));
  if(progress.state.pcb?.status==='submission-started') {
    // Recover by looking up the unique file; never repeat the submit click.
    const existing=await findPcb(api,progress.state.fileName);
    progress.state.pcb={...progress.state.pcb,...existing,page:undefined,status:'submitted'};progress.save();
    return existing;
  }
  return api.step('submit-pcb',async()=>{
    progress.begin('pcb');page.on('response',capture);
    try {
      await review.getByRole('button',{name:'确认并提交',exact:true}).click();
      api.event('pcb-submit-clicked',{fileName:progress.state.fileName});
      // The same browser's SMT list is an independent read of order creation.
      const created=await findPcb(api,progress.state.fileName);
      progress.state.pcb={...progress.state.pcb,...created,page:undefined,status:'submitted'};
      progress.state.phase='pcb-submitted';progress.save();
      api.event('pcb-order-observed',{displayNumber:created.displayNumber,fileName:created.fileName});
      return created;
    }finally{page.off('response',capture);remember();}
  });
}
module.exports={submitPcb,findPcb};
