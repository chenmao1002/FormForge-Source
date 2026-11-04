const {dimensionsMatch,verifyBarePcbReview}=require('./parameters.cjs');
const {safeUrl}=require('../desktop/policy.cjs');
const {expect}=require('playwright/test');
const path=require('node:path');
const fs=require('node:fs');
delete require.cache[require.resolve('./order-state.cjs')];
const {orderState}=require('./order-state.cjs');
delete require.cache[require.resolve('./order-submit.cjs')];
const {submitPcb,findPcb}=require('./order-submit.cjs');

async function openEntry(context,target,timeout=30000,ready=async()=>null) {
  const previous=new Map(context.pages().map(p=>[p,p.url()]));
  const changed=()=>context.pages().findLast(p=>!p.isClosed() && (!previous.has(p) || previous.get(p)!==p.url()));
  let selected;
  await target.click();
  await expect.poll(async()=>{selected=changed() || await ready();return !!selected;},{timeout,message:'PCB入口未打开，未提交订单。'}).toBeTruthy();
  return selected;
}
async function selectPcbMaterialWhenReady(page) {
  await expect(page.locator('.el-loading-mask.is-fullscreen').filter({visible:true})).toHaveCount(0,{timeout:45000});
  const picker=page.locator('.el-dialog__wrapper').filter({hasText:'请选择板材类别'}).filter({visible:true});
  await picker.waitFor({state:'visible',timeout:2000}).catch(()=>{});
  if(await picker.count()===1){
    await picker.getByText('FR-4',{exact:true}).click();
    await expect(picker).toBeHidden();
  }
}
async function ensurePcbDimensions(page,board) {
  const fields=page.locator('#pcbLengthInput,#pcbWidthInput');
  let raw=await fields.evaluateAll(es=>es.map(e=>e.value.trim()));
  let source='existing-form-values';
  if(raw.length===2&&raw.every(v=>v==='')){
    // The native upload dialog explicitly allows continuing before parsing.
    // Set only BOTH blank fields from the SHA-bound CAM's dimensions; never
    // overwrite a conflicting provider measurement or call this recognition.
    await page.locator('#pcbLengthInput').fill(String(board.widthMm/10));
    await page.locator('#pcbWidthInput').fill(String(board.heightMm/10));
    await page.locator('#pcbWidthInput').press('Tab');
    raw=await fields.evaluateAll(es=>es.map(e=>e.value.trim()));source='agent-source-bound-unparsed';
  }
  const dimensions=raw.map(v=>Number(v)*10);
  if(!dimensionsMatch(dimensions,board))throw new Error('平台尺寸字段与绑定CAM不符，未覆盖冲突值或提交。');
  // Existing values may have been manually entered in an earlier attempt.
  // Field contents alone cannot prove a native Gerber recognition response.
  return {dimensions,dimensionSource:source,platformRecognized:false};
}
async function confirmBarePcbOnly(page) {
  const confirmation=page.getByRole('button',{name:'确定，不需要SMT',exact:true});
  await confirmation.waitFor({state:'visible',timeout:2000}).catch(()=>{});
  if(await confirmation.isVisible().catch(()=>false)) {
    await confirmation.click();
    await expect(confirmation).toBeHidden();
  }
}
async function quoteRealSmt(api,progress,pcb,refresh=false) {
  api.setPage(pcb.page);
  if(!refresh && progress.state.assemblyQuote?.actualBoardManufacturingPriceVerified && /\/smt-order-/.test(pcb.page.url()) && await pcb.page.getByText(progress.state.fileName,{exact:true}).isVisible().catch(()=>false)) {
    delete require.cache[require.resolve('./smt-submit.cjs')];
    return require('./smt-submit.cjs').continueSmt(api,progress);
  }
  delete require.cache[require.resolve('./smt.cjs')];
  const quote=await require('./smt.cjs').run({...api,pcb:{code:pcb.displayNumber,fileName:progress.state.fileName,kind:'actual-pcb',sizeMm:[api.checkpoint.board.widthMm,api.checkpoint.board.heightMm]}});
  progress.state.assemblyQuote=quote;progress.state.phase='actual-smt-quoted';progress.save();
  delete require.cache[require.resolve('./smt-submit.cjs')];
  return require('./smt-submit.cjs').continueSmt(api,progress);
}
async function run(api) {
  const progress=orderState(api);
  if(progress.state.smt?.quantityCorrection && await api.getPage().getByRole('button',{name:'开始修改',exact:true}).isVisible().catch(()=>false)){
    delete require.cache[require.resolve('./smt-quantity-correction.cjs')];
    return require('./smt-quantity-correction.cjs').openCorrection(api,progress);
  }
  if(progress.state.smt?.status==='quantity-correction-matching')return quoteRealSmt(api,progress,{...progress.state.pcb,page:api.getPage()},true);
  if(progress.state.smt?.status==='submitted' || progress.state.smt?.createResponse?.smtOrderCode || progress.state.smt?.displayNumber || progress.state.smt?.submitClickedAt) {
    progress.state.smt.displayNumber=progress.state.smt.displayNumber || progress.state.smt.createResponse?.smtOrderCode;progress.save();
    delete require.cache[require.resolve('./order-observe.cjs')];
    return require('./order-observe.cjs').observeSmt(api,progress);
  }
  if(api.submitRequested && progress.state.smt && await api.getPage().getByText('极性选择',{exact:true}).isVisible().catch(()=>false)){
    delete require.cache[require.resolve('./smt-submit.cjs')];
    return require('./smt-submit.cjs').inspectConfirmation(api,progress);
  }
  if(api.submitRequested && progress.state.pcb?.displayNumber && /https:\/\/www\.jlc\.com\/newOrder\//.test(api.getPage().url()) && await api.getPage().getByText(progress.state.fileName,{exact:true}).isVisible().catch(()=>false) && await api.getPage().getByText('订单编号：'+progress.state.pcb.displayNumber,{exact:true}).isVisible().catch(()=>false)) {
    const native=api.getPage();
    fs.writeFileSync(path.join(progress.directory,'pcb-order-list-private.txt'),await native.locator('body').innerText());
    await native.screenshot({path:path.join(progress.directory,'pcb-order-list-private.png'),fullPage:false});
    api.event('pcb-order-list-observed',{displayNumber:progress.state.pcb.displayNumber,fileName:progress.state.fileName});
  }
  if(progress.state.smt?.status==='submission-started') {
    delete require.cache[require.resolve('./smt-submit.cjs')];
    return require('./smt-submit.cjs').inspectConfirmation(api,progress);
  }
  if(progress.state.smt?.status==='waiting-pcb-review') {
    const pcb=await api.step('wait-pcb-review',async()=>{
      if(!api.canAct())throw Object.assign(new Error('已交人工接管，审核等待结束。'),{code:'human_control'});
      const current=await findPcb(api,progress.state.fileName);
      progress.state.pcb={...progress.state.pcb,...current,page:undefined};progress.save();
      if(current.row.includes('等待嘉立创审核'))api.event('pcb-audit-waiting',{displayNumber:current.displayNumber});
      return current;
    });
    const changed=!pcb.row.includes('等待嘉立创审核');
    if(changed) {
      const row=pcb.page.locator('tr').filter({hasText:progress.state.fileName});
      await row.getByText('选择此单',{exact:true}).click();
      progress.state.smt.status='matching-resume';progress.state.smt.pcbReviewChangedAt=new Date().toISOString();progress.save();
    }
    return {phase:changed?'pcb-review-changed':'waiting-pcb-review',pcbOrderNumber:pcb.displayNumber,pcbRow:pcb.row,pcbOrderSubmitted:true,smtOrderSubmitted:false,matchingSaved:true,paymentPerformed:false};
  }
  if(progress.state.smt?.status==='matching-resume') {
    const root=api.context.pages().find(p=>p.url()==='https://www.jlc-smt.com/order');
    if(!root)throw new Error('已保存匹配的下单入口不可用。');
    api.setPage(root);
    const row=root.locator('tr').filter({hasText:progress.state.fileName});
    const select=row.getByText('选择此单',{exact:true});
    if(await select.isVisible().catch(()=>false))await select.click();
    let restored;
    await api.step('restore-saved-supplier-matching',async()=>{
      await root.getByText('已为您保存上传的修改结果，可直接进入BOM匹配',{exact:true}).waitFor({state:'visible'});
      const opening=api.context.waitForEvent('page',{timeout:30000});
      await root.getByRole('button',{name:'下一步，BOM匹配选型',exact:true}).click();
      restored=await opening;await restored.waitForURL(/\/smt-order-/,{waitUntil:'domcontentloaded',timeout:45000});
      api.setPage(restored);progress.state.smt.status='matching-restored';progress.save();
    });
    return quoteRealSmt(api,progress,{...progress.state.pcb,page:restored},true);
  }
  if(api.submitRequested && progress.state.pcb) {
    for(const candidate of api.context.pages().filter(p=>/\/smt-order-/.test(p.url())).reverse()) {
      if(await candidate.getByText(progress.state.fileName,{exact:true}).isVisible().catch(()=>false))
        return quoteRealSmt(api,progress,{...progress.state.pcb,page:candidate});
    }
    const existing=await api.step('recover-existing-pcb',()=>findPcb(api,progress.state.fileName));
    if(progress.state.pcb.displayNumber && progress.state.pcb.displayNumber!==existing.displayNumber)throw new Error('既有PCB订单身份冲突，未重复提交。');
    progress.state.pcb={...progress.state.pcb,...existing,page:undefined,status:'submitted'};progress.save();
    return quoteRealSmt(api,progress,existing);
  }
  let basicConfigured=false;
  let page=api.context.pages().findLast(p=>/https:\/\/(?:member|www)\.jlc\.com\//.test(p.url()) && /newOrder|client|integrated/.test(p.url()));
  const uploadCacheFile=path.join(api.local,'gerber-uploads.json');
  const uploadCache=fs.existsSync(uploadCacheFile)?JSON.parse(fs.readFileSync(uploadCacheFile,'utf8')):{};
  const cached=uploadCache[api.gerber.sha256];
  if(!api.submitRequested&&!progress.state.prepared&&api.checkpoint.taskKind==='prepare-order'){
    // A new preparation must not inherit our old, unsubmitted form remarks.
    // Open a clean form; never clear or edit the supplier's remark field.
    // The supplier's existing upload-workbench route is distinct from the
    // bare parameter form. It was recorded by the real PCB preparation flow.
    const current=api.getPage();
    const currentFile=current&&!current.isClosed()?new URL(current.url()).searchParams.get('pcbFileId'):null;
    const cachedFile=cached?new URL(cached.url).searchParams.get('pcbFileId'):null;
    const sameLiveFile=!!currentFile&&currentFile===cachedFile;
    page=sameLiveFile?current:await api.context.newPage();
    let entry='https://member.jlc.com/integrated/#/?type=onLineNewOrderByFileId&spm=SO01';
    if(cached){
      const u=new URL(cached.url);
      if(u.origin!=='https://www.jlc.com'||!u.pathname.startsWith('/newOrder/')||!u.searchParams.has('pcbFileId')||u.username||u.password)throw new Error('已上传Gerber来源地址无效。');
      entry=cached.url;progress.state.localGerberName=path.basename(api.gerber.path);progress.state.fileName=cached.fileName;progress.save();
      api.event('reuse-byte-bound-gerber-upload',{sha256:api.gerber.sha256,fileName:cached.fileName,source:cached.source});
    }
    if(!sameLiveFile)await page.goto(entry,{waitUntil:'domcontentloaded',timeout:45000});
    if(cached)await page.locator('#pcbLengthInput').waitFor({state:'visible',timeout:45000});
    api.setPage(page);
  }
  if(api.submitRequested&&progress.state.prepared&&progress.state.prepared.notePolicy!=='leave-vendor-content-unchanged')throw new Error('旧准备未核对备注保留规则，请重新准备干净表单。');
  if(!page) {
    await api.step('open-real-pcb-entry',async()=>{
      const smt=api.context.pages().find(p=>p.url()==='https://www.jlc-smt.com/order') || await api.context.newPage();
      if(smt.url()==='about:blank')await smt.goto('https://www.jlc-smt.com/order',{waitUntil:'domcontentloaded'});
      const target=smt.getByText('没有PCB?立即前往PCB下单>>',{exact:true});
      await target.waitFor({state:'visible'});
      const entry=await target.evaluate(el=>{const link=el.closest('a');return {tag:el.tagName,anchor:!!link,href:link?.href||null,target:link?.target||null};});
      api.event('pcb-entry-observed',{...entry,href:entry.href?safeUrl(entry.href):null});
      // Supplier links can navigate this page or reuse a named tab. A new
      // page event alone cannot prove either of those legitimate transitions.
      page=await openEntry(api.context,target);
      await page.waitForURL(/https:\/\/[^/]*jlc\.com\//,{waitUntil:'domcontentloaded',timeout:45000});
    });
  }
  if(page.url().startsWith('https://member.jlc.com/integrated/')) {
    await api.step('open-pcb-order-form',async()=>{
      const link=page.getByRole('link',{name:/^PCB下单/});
      await link.waitFor({state:'visible',timeout:30000});
      page=await openEntry(api.context,link);
      await page.waitForLoadState('domcontentloaded');
    });
  }
  api.setPage(page);
  if(api.submitRequested && progress.state.prepared && await page.getByText('订单检查',{exact:true}).filter({visible:true}).count()===1) {
    const created=await submitPcb(api,page,progress);
    return quoteRealSmt(api,progress,created);
  }
  if(page.url().includes('/newOrder/') && await page.locator('#pcbLengthInput').count()===0) {
    await api.step('upload-actual-gerber',async()=>{
      const name=path.basename(api.gerber.path).replace(/\.zip$/i,'');
      if(!progress.state.pcb&&!progress.state.prepared){progress.state.fileName=name;progress.save();}
      let existing=page.locator('tr').filter({hasText:name});
      if(await existing.count()===0) {
        api.checkedFiles(api.root,[api.gerber]);
        const chooser=page.waitForEvent('filechooser',{timeout:15000}).catch(()=>null);
        await page.getByText('上传PCB/FPC下单',{exact:true}).click();
        const dialog=await chooser;if(!dialog)throw new Error('PCB上传入口未打开，未提交订单。');
        await dialog.setFiles(api.gerber.path);
        api.event('gerber-upload-issued',{name,sha256:api.gerber.sha256});
        await expect.poll(async()=>await page.locator('#pcbLengthInput').count()>0 || await existing.count()>0,{timeout:90000}).toBeTruthy();
      }
      if(await page.locator('#pcbLengthInput').count()===0) {
        existing=page.locator('tr').filter({hasText:name});
        await existing.first().waitFor({state:'visible',timeout:60000});
        api.checkpoint.uploadedGerber={name,sha256:api.gerber.sha256,observedRow:await existing.first().innerText()};api.save();
        const row=existing.first();
        if((await row.innerText()).includes('已下单'))throw new Error('该制造文件已有订单，先核对订单记录，不重复提交。');
        const sourcePage=page;
        const parsing=sourcePage.getByText('文件解析中，等待解析完成可以提供更好的下单体验，是否继续等待',{exact:true}).filter({visible:true});
        let acknowledged=await parsing.count()>0;
        const next=acknowledged?sourcePage.getByRole('button',{name:'继续下单',exact:true}):row.getByText('立即下单',{exact:true});
        if(acknowledged)api.event('gerber-parsing-notice',{action:'continue-to-parameter-form',orderSubmitted:false});
        page=await openEntry(api.context,next,45000,async()=>{
          if(!acknowledged && await parsing.count()>0) {
            acknowledged=true;
            api.event('gerber-parsing-notice',{action:'continue-to-parameter-form',orderSubmitted:false});
            await sourcePage.getByRole('button',{name:'继续下单',exact:true}).click();
          }
          return await sourcePage.locator('#pcbLengthInput').count()>0?sourcePage:null;
        });
        await page.waitForURL(/https:\/\/[^/]*jlc\.com\//,{waitUntil:'domcontentloaded',timeout:45000});
        await page.locator('#pcbLengthInput').waitFor({state:'visible',timeout:45000});api.setPage(page);
      }
    });
  }
  if(await page.locator('#pcbLengthInput').count()>0) {
    const marking=page.locator('.el-dialog__wrapper[title="加客编"]').filter({visible:true});
    const confirmMarking=async()=>{
      await marking.getByRole('button',{name:'无要求',exact:true}).first().click();
      await marking.getByRole('button',{name:'确认',exact:true}).click();
      await expect(marking).toBeHidden();
    };
    if(await marking.isVisible().catch(()=>false))await confirmMarking();
    const reviewTitle=page.getByText('订单检查',{exact:true}).filter({visible:true});
    if(await reviewTitle.isVisible().catch(()=>false)) {
      await page.keyboard.press('Escape');
      if(await reviewTitle.isVisible().catch(()=>false)) {
        const close=page.getByRole('button',{name:'Close',exact:true});
        if(await close.count()===1)await close.click();
        else {
          const icon=reviewTitle.locator('xpath=ancestor::*[.//*[contains(@class,"close")]][1]').locator('[class*="close"]').filter({visible:true});
          if(await icon.count()===1)await icon.click();
          else {api.event('review-header-markup',{html:await reviewTitle.locator('xpath=../..').evaluate(e=>e.outerHTML.slice(0,11000))});throw new Error('订单复核面板未关闭，保留现有配置。');}
        }
      }
    }
    const keepHasl=page.getByRole('button',{name:'继续使用无铅喷锡',exact:true});
    if(await keepHasl.isVisible().catch(()=>false))await keepHasl.click();
    const keepStandard=page.getByRole('button',{name:/^按标准合约常规处理/});
    if(await keepStandard.isVisible().catch(()=>false))await keepStandard.click();
    await api.step('configure-pcb-basic',async()=>{
      try {
      await selectPcbMaterialWhenReady(page);
      const proofTitle=page.getByText('确认生产稿说明',{exact:true}).filter({visible:true});
      const confirmProof=async()=>{
        await page.getByRole('button',{name:'需要确认生产稿',exact:true}).click();
        await page.getByRole('button',{name:'手动确认',exact:true}).click();
        await page.getByRole('button',{name:'确认2次',exact:true}).click();
        await page.getByRole('button',{name:'确定',exact:true}).filter({visible:true}).click();
        await expect(proofTitle).toBeHidden();api.event('pcb-field-set',{label:'manual production proof, twice'});
      };
      if(await proofTitle.isVisible().catch(()=>false))await confirmProof();
      const onboarding=page.getByRole('button',{name:'开始体验',exact:true});
      const materialNames=page.locator('.el-dialog__wrapper:visible p.name');
      api.event('material-picker-options',{labels:await materialNames.allTextContents()});
      const materialChoice=materialNames.filter({hasText:/^FR-4$/});
      const materialDialog=page.locator('.el-dialog__wrapper').filter({hasText:'请选择板材类别'}).filter({visible:true});
      if(await materialChoice.count()===1){await materialChoice.click();await expect(materialChoice).toBeHidden();}
      else if(await materialDialog.count()===1){await materialDialog.getByText('FR-4',{exact:true}).click();await expect(materialDialog).toBeHidden();}
      const material=page.getByText('请选择板材类别：',{exact:true});
      const skip=page.getByText('跳过',{exact:true});
      if(await skip.isVisible().catch(()=>false))await skip.click();
      for(let pass=0;pass<3&&(await material.isVisible().catch(()=>false)||await onboarding.isVisible().catch(()=>false));pass++) {
        let handled=false;
        for(const candidate of [page.getByText('FR-4',{exact:true}).last(),onboarding]) {
          if(!await candidate.isVisible().catch(()=>false))continue;
          if(candidate===onboarding && !await onboarding.isVisible())continue;
          try {await candidate.click({trial:true,timeout:800});}
          catch {continue;}
          await candidate.click();handled=true;break;
        }
        if(!await material.isVisible().catch(()=>false) && !await onboarding.isVisible().catch(()=>false))break;
        if(!handled)throw new Error('首次使用提示遮挡工艺表单，未配置或提交。');
      }
      if(await skip.isVisible().catch(()=>false))await skip.click();
      await page.getByRole('button',{name:'FR-4',exact:true}).click();
      await page.getByRole('button',{name:'2',exact:true}).click();
      const dimensionReview=await ensurePcbDimensions(page,api.checkpoint.board);const dimensions=dimensionReview.dimensions;
      api.event('pcb-dimensions-reviewed',dimensionReview);
      const quantity=page.getByPlaceholder('数量',{exact:true});
      if(await quantity.inputValue()!==String(api.checkpoint.quantity)) {
        const option=page.getByText(String(api.checkpoint.quantity),{exact:true}).filter({visible:true});
        if(await option.count()!==1)await quantity.click();
        await option.click();await expect(quantity).toHaveValue(String(api.checkpoint.quantity));
      }
      const deliveryType=page.getByText('出货方式',{exact:true});
      if(await deliveryType.count()===1)api.event('pcb-delivery-type-markup',{html:await deliveryType.locator('..').evaluate(e=>e.outerHTML.slice(0,5000))});
      await page.getByRole('button',{name:/^需要（确认2次/}).click();
      await proofTitle.waitFor({state:'visible'});await confirmProof();
      await page.getByRole('button',{name:'手动确认订单',exact:true}).click();
      api.checkpoint.pcbChoices={material:'FR-4',copperLayers:2,sizeMm:dimensions,dimensionSource:dimensionReview.dimensionSource,platformDimensionsRecognized:dimensionReview.platformRecognized,quantity:api.checkpoint.quantity,manualOrderConfirmation:true,manualProductionProof:true};api.save();
      } catch(error) {api.event('pcb-basic-failed',{reason:error.message.slice(0,9000)});throw error;}
    });
    await api.step('configure-pcb-manufacturing',async()=>{
      const field=label=>page.getByText(label,{exact:true}).locator('xpath=ancestor::*[.//button[normalize-space(.)!="" and not(@aria-label="Close")]][1]');
      const choose=async(locator,label)=>{
        if(!api.canAct())throw new Error('已交人工接管。');
        try {await locator.click();api.event('pcb-field-set',{label});}
        catch(error){api.event('pcb-field-failed',{label,reason:error.message.slice(0,3500)});throw error;}
      };
      await choose(page.getByRole('button',{name:'1.6',exact:true}),'thickness 1.6mm');
      await choose(field('外层铜厚').getByRole('button',{name:'1盎司',exact:true}),'outer copper 1oz');
      await choose(page.getByText('不指定 (真A级)',{exact:true}).filter({visible:true}),'standard TG135 material');
      await choose(field('阻焊颜色').getByRole('button',{name:'绿色',exact:true}),'green mask');
      await choose(field('字符颜色').getByRole('button',{name:'白色',exact:true}),'white legend');
      await choose(field('阻焊覆盖').getByRole('button',{name:'过孔盖油',exact:true}),'via tenting; Gerber remains authoritative');
      await choose(field('焊盘喷镀').getByRole('button',{name:/^无铅喷锡/}),'lead-free HASL');
      await keepHasl.waitFor({state:'visible',timeout:1500}).catch(()=>{});
      if(await keepHasl.isVisible().catch(()=>false))await keepHasl.click();
      await choose(field('最小孔径/外径').getByRole('button',{name:/^0\.3mm/}),'minimum drill 0.3mm');
      await choose(field('品质赔付服务').getByRole('button',{name:/^按标准合同常规处理/}),'standard compensation');
      await keepStandard.waitFor({state:'visible',timeout:1500}).catch(()=>{});
      if(await keepStandard.isVisible().catch(()=>false))await keepStandard.click();
      await choose(field('标志增加方式').getByRole('button',{name:'无要求',exact:true}),'standard mark position');
      await choose(page.getByRole('button',{name:'加嘉立创客编（免费）',exact:true}),'standard free customer marking');
      await marking.waitFor({state:'visible',timeout:1500}).catch(()=>{});
      if(await marking.isVisible().catch(()=>false))await confirmMarking();
      await choose(field('是否需要磨边').getByRole('button',{name:'不需要',exact:true}),'no optional edge grinding');
      const requiresSmt=api.checkpoint.assemblyScope!=='pcb-only';
      await choose(field('是否SMT贴片').getByRole('button',{name:requiresSmt?'需要':'不需要',exact:true}),requiresSmt?'requires SMT':'bare PCB only; no SMT');
      if(!requiresSmt)await confirmBarePcbOnly(page);
      const steelLabel=page.getByText('是否开钢网',{exact:true}).filter({visible:true});
      if(await steelLabel.count()>0)api.event('pcb-steel-markup',{html:await steelLabel.locator('..').evaluate(e=>e.outerHTML.slice(0,9000))});
      await choose(field('是否开钢网').getByRole('button',{name:'不需要',exact:true}),'no stencil');
      await choose(page.getByRole('button',{name:'手动确认订单',exact:true}),'manual confirmation; no automatic deduction');
      await choose(page.getByRole('button',{name:'不同交期订单不一起发货',exact:true}),'independent shipment');
      await choose(page.getByText('顺丰电商标快',{exact:true}).filter({visible:true}).first(),'SF standard e-commerce delivery');
      api.checkpoint.pcbChoices={...api.checkpoint.pcbChoices,thicknessMm:1.6,mask:'green',legend:'white',finish:'lead-free HASL',minimumDrillMm:0.3,requiresSmt,separateStencil:false};api.save();
      const preview=page.getByText('2D仿真图',{exact:true}).locator('xpath=ancestor::*[.//canvas or .//img][1]');
      api.event('pcb-preview-markup',{html:await preview.evaluate(e=>e.outerHTML.slice(0,14000))});
      await preview.screenshot({path:path.join(progress.directory,'pcb-platform-preview.png')});
      await choose(page.getByRole('button',{name:'检查订单',exact:true}),'check order without submitting');
      const deliveryAck=page.getByText('确认无误',{exact:true}).filter({visible:true});
      if(await deliveryAck.count()===1)await deliveryAck.click();
      if(!requiresSmt){
        const review=page.getByText('订单检查',{exact:true}).filter({visible:true})
          .locator('xpath=ancestor::*[.//button[contains(normalize-space(.),"确认并提交")]][1]');
        await expect(review).toBeVisible();
        const acknowledged=review.getByRole('button',{name:'已确认',exact:true});
        if(await acknowledged.count()===1)await acknowledged.click();
        const text=await review.innerText();
        verifyBarePcbReview(text,api.checkpoint.quantity,api.checkpoint.board,progress.state.fileName);
        api.checkpoint.pcbChoices.nativeReviewComplete=true;api.save();
        api.event('pcb-preparation-reviewed',{dimensions:api.checkpoint.pcbChoices.sizeMm,quantity:api.checkpoint.quantity,requiresSmt:false,separateStencil:false,orderSubmitted:false});
      }
    });
  }
  api.checkpoint.recoveryUrls=api.context.pages().map(p=>p.url()).filter(u=>u.startsWith('https://'));
  api.save();
  if(await page.locator('#pcbLengthInput').count()>0) {
    const manual=page.getByRole('button',{name:'手动确认订单',exact:true});
    const selectedOptions=await page.locator('button.checked').allInnerTexts();
    const amountText=await page.getByRole('button',{name:/网页版下单/}).innerText();
    const remark=page.getByPlaceholder('选填',{exact:true});let remarksObservation={observed:false};
    if(await remark.count()===1){
      const value=await remark.inputValue();
      if(value.startsWith('FormForge ')&&value.includes('保持原Gerber外形与孔位'))throw new Error('发现旧程序写入的未提交备注，请使用新表单准备；不修改备注字段。');
      remarksObservation={observed:true,hasContent:!!value.trim(),oldAgentRemarkDetected:false};
    }
    progress.state.prepared={observedAt:new Date().toISOString(),fileName:progress.state.fileName,notePolicy:'leave-vendor-content-unchanged',supplierNotesTouched:false,remarksObservation,
      choices:api.checkpoint.pcbChoices,selectedOptions,manualOrderClass:await manual.getAttribute('class'),
      webPriceText:amountText};progress.save();
    const nativeUrl=new URL(page.url());
    if(nativeUrl.searchParams.has('pcbFileId')){
      uploadCache[api.gerber.sha256]={url:page.url(),fileName:progress.state.fileName,source:'actual-file-bound-preparation',verifiedAt:new Date().toISOString()};
      fs.writeFileSync(uploadCacheFile,JSON.stringify(uploadCache,null,2));
    }
    fs.writeFileSync(path.join(progress.directory,'pcb-preparation.json'),JSON.stringify(progress.state.prepared,null,2));
    await page.screenshot({path:path.join(progress.directory,'pcb-private-viewport.png'),fullPage:false});
    api.checkpoint.orderDirectory=progress.directory;api.save();
    basicConfigured=true;
  }
  if(api.submitRequested) {
    const created=await submitPcb(api,page,progress);
    return quoteRealSmt(api,progress,created);
  }
  if(!basicConfigured || !progress.state.prepared)throw new Error('尚未完成实际PCB工艺与报价核对，不能报告已配置。');
  return {phase:'pcb-basic-configured',url:safeUrl(page.url()),actualGerberSha256:api.gerber.sha256,
    orderSubmitted:false,paymentPerformed:false};
}
module.exports={run,openEntry,selectPcbMaterialWhenReady,ensurePcbDimensions,confirmBarePcbOnly};
