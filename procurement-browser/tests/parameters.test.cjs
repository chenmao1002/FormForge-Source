const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {validateParameters,verifyReview}=require('../parameters.cjs');
const {orderState}=require('../order-state.cjs');
const {run}=require('../order.cjs');
const board={widthMm:45,heightMm:60,layers:2,thicknessMm:1.6,side:'top'};

test('bare PCB prepared review binds actual filename, process and manual confirmation',()=>{
  const {verifyBarePcbReview}=require('../parameters.cjs');
  const review='文件名：BREEZE40_hash\n板材类别 FR-4\n板子数量 5 板子层数 2 4.5 CM x 6 CM\n成品板厚 1.6 MM\n焊盘喷镀 无铅喷锡\n确认订单方式 手动确认订单\n是否需要SMT 不需要\n是否需要钢网 不需要';
  assert.equal(verifyBarePcbReview(review,5,board,'BREEZE40_hash').orderSubmitted,false);
  for(const [a,b] of [['BREEZE40_hash','another_board'],['FR-4','铝基板'],['1.6 MM','1.0 MM'],['无铅喷锡','有铅喷锡'],['手动确认订单','系统自动扣款并确认'],['是否需要SMT 不需要','是否需要SMT 需要'],['是否需要钢网 不需要','是否需要钢网 去填写']])
    assert.throws(()=>verifyBarePcbReview(review.replace(a,b),5,board,'BREEZE40_hash'));
  assert.throws(()=>verifyBarePcbReview(review+'\n还有1项未填写',5,board,'BREEZE40_hash'));
});

test('bare PCB preparation has no assembly count and cannot become a submission',()=>{
  const input={action:'prepare-order',quantity:5,assemblyScope:'pcb-only',board};
  validateParameters(input);
  assert.equal(require('../parameters.cjs').assemblyCount(input),0);
  for(const extra of [{action:'submit-unpaid'},{action:'quote'},{assemblyQuantity:1},{files:[{}]},{parts:[{}]}])
    assert.throws(()=>validateParameters({...input,...extra}));
});

test('initial loading mask and delayed material dialog are handled before PCB fields',async()=>{
  const {chromium}=require('playwright');
  const {selectPcbMaterialWhenReady}=require('../order.cjs');
  const temporary=path.resolve(__dirname,'../../.local/parameter-tests/browser-tmp');fs.mkdirSync(temporary,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true,env:{...process.env,TEMP:temporary,TMP:temporary}});
  try{
    const page=await browser.newPage();
    await page.setContent('<div class="el-loading-mask is-fullscreen">Loading</div><script>setTimeout(()=>{document.body.innerHTML=`<div class="el-dialog__wrapper"><p>请选择板材类别</p><button onclick="document.body.dataset.selected=\'FR-4\';this.parentElement.remove()">FR-4</button></div>`},200)</script>');
    await selectPcbMaterialWhenReady(page);
    assert.equal(await page.locator('body').getAttribute('data-selected'),'FR-4');
    const {ensurePcbDimensions}=require('../order.cjs');
    await page.setContent('<input id="pcbLengthInput"><input id="pcbWidthInput">');
    const filled=await ensurePcbDimensions(page,board);
    assert.deepEqual(filled.dimensions,[45,60]);assert.equal(filled.platformRecognized,false);
    assert.equal(filled.dimensionSource,'agent-source-bound-unparsed');
    const reused=await ensurePcbDimensions(page,board);
    assert.equal(reused.platformRecognized,false);assert.equal(reused.dimensionSource,'existing-form-values');
    await page.locator('#pcbLengthInput').fill('9.9');
    await assert.rejects(ensurePcbDimensions(page,board),/未覆盖冲突值/);
    assert.equal(await page.locator('#pcbLengthInput').inputValue(),'9.9');
    const {confirmBarePcbOnly}=require('../order.cjs');
    await page.setContent('<script>setTimeout(()=>{document.body.innerHTML=`<div><p>请选择本单是否需要SMT贴片</p><button onclick="document.body.dataset.smt=\'no\';this.parentElement.remove()">确定，不需要SMT</button><button>需要SMT</button></div>`},200)</script>');
    await confirmBarePcbOnly(page);
    assert.equal(await page.locator('body').getAttribute('data-smt'),'no');
  }finally{await browser.close();}
});
test('dimensions and quantity are read from the requested board',()=>{
  validateParameters({action:'submit-unpaid',quantity:10,board});
  verifyReview('板子数量 10 板子层数 2 4.5 CM x 6 CM',10,board);
  for(const text of ['板子数量 5 板子层数 2 4.5 CM x 6 CM','板子数量 10 板子层数 2 2.6 CM x 3.2 CM'])
    assert.throws(()=>verifyReview(text,10,board));
  for(const bad of [{...board,layers:4},{...board,side:'bottom'},{...board,widthMm:NaN}])assert.throws(()=>validateParameters({action:'submit-unpaid',quantity:10,board:bad}));
  assert.throws(()=>validateParameters({action:'submit-unpaid',quantity:10}));
});

test('assembly quantity and SMD-only scope are independent of fabricated count',()=>{
  validateParameters({action:'submit-unpaid',quantity:5,assemblyQuantity:2,assemblyScope:'smd-only',parts:[{mounting:'smd'}],board});
  assert.throws(()=>validateParameters({action:'submit-unpaid',quantity:5,assemblyQuantity:6,board}));
  assert.throws(()=>validateParameters({action:'submit-unpaid',quantity:5,assemblyQuantity:2,assemblyScope:'smd-only',parts:[{mounting:'through-hole'}],board}));
});
test('order identity includes dimensions and preserves historical orders without duplicates',t=>{
  const base=path.resolve(__dirname,'../../.local/parameter-tests');fs.mkdirSync(base,{recursive:true});
  const local=fs.mkdtempSync(path.join(base,'case-'));t.after(()=>{assert.ok(local.startsWith(base+path.sep));fs.rmSync(local,{recursive:true,force:true});});
  const api={local,checkpoint:{projectId:'p',projectVersion:29,quantity:5,board},gerber:{path:'board.zip',sha256:'gerber'},files:[{sha256:'bom'},{sha256:'cpl'}],parts:[],save(){}};
  const first=orderState(api).state.binding;
  api.checkpoint.board={...board,widthMm:46};assert.notEqual(orderState(api).state.binding,first);
  const input={projectId:'p',projectVersion:29,quantity:5,gerberSha256:'gerber',files:['bom','cpl'],parts:[]};
  const binding=crypto.createHash('sha256').update(JSON.stringify(input)).digest('hex');
  const dir=path.join(local,'orders',binding);fs.mkdirSync(dir,{recursive:true});
  fs.writeFileSync(path.join(dir,'state.json'),JSON.stringify({binding,input,pcb:{displayNumber:'Y117'}}));
  assert.throws(()=>orderState(api),/旧版订单/);
  api.checkpoint.board={...board,widthMm:26,heightMm:32};
  const recovered=orderState(api);assert.equal(recovered.state.binding,binding);assert.equal(recovered.state.pcb.displayNumber,'Y117');
  assert.throws(()=>recovered.begin('pcb'),/已存在/);
});

test('stale worker cannot write dimensionless state; empty preparations can be rebound without editing history',t=>{
  const base=path.resolve(__dirname,'../../.local/parameter-tests');fs.mkdirSync(base,{recursive:true});
  const local=fs.mkdtempSync(path.join(base,'empty-'));t.after(()=>{assert.ok(local.startsWith(base+path.sep));fs.rmSync(local,{recursive:true,force:true});});
  const api={local,checkpoint:{projectId:'p',projectVersion:42,quantity:5},gerber:{path:'unique-board.zip',sha256:'gerber'},files:[{sha256:'bom'},{sha256:'cpl'}],parts:[],save(){}};
  assert.throws(()=>orderState(api),/执行器未传入/);assert(!fs.existsSync(path.join(local,'orders')));
  const input={projectId:'p',projectVersion:42,quantity:5,gerberSha256:'gerber',files:['bom','cpl'],parts:[]};
  const legacy=crypto.createHash('sha256').update(JSON.stringify(input)).digest('hex'),dir=path.join(local,'orders',legacy);fs.mkdirSync(dir,{recursive:true});
  const file=path.join(dir,'state.json'),raw=JSON.stringify({binding:legacy,input,createdAt:'before',fileName:'generic',phase:'preparing'});fs.writeFileSync(file,raw);
  api.checkpoint.board=board;const current=orderState(api);assert.notEqual(current.state.binding,legacy);
  assert.equal(current.state.supersedesEmptyPreparation,legacy);assert.deepEqual(current.state.input.board,board);assert.equal(fs.readFileSync(file,'utf8'),raw);
  fs.writeFileSync(file,JSON.stringify({...JSON.parse(raw),pcb:{status:'submission-started'}}));
  assert.throws(()=>orderState(api),/旧版订单/);
});

test('a reachable supplier page without a verified parameter form is not configured',async t=>{
  const base=path.resolve(__dirname,'../../.local/parameter-tests');fs.mkdirSync(base,{recursive:true});
  const local=fs.mkdtempSync(path.join(base,'unready-'));t.after(()=>{assert.ok(local.startsWith(base+path.sep));fs.rmSync(local,{recursive:true,force:true});});
  const page={url:()=> 'https://member.jlc.com/client/',goto:async()=>{},locator:()=>({count:async()=>0})};
  const api={local,context:{pages:()=>[page],newPage:async()=>page},setPage(){},checkpoint:{projectId:'p',projectVersion:42,quantity:5,board},
    gerber:{path:'unique-board.zip',sha256:'gerber'},files:[{sha256:'bom'},{sha256:'cpl'}],parts:[],save(){}};
  await assert.rejects(run(api),/尚未完成实际PCB工艺与报价核对/);
});
