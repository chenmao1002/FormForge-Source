const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const vm=require('node:vm');
const p=require('../policy.cjs');
const {actOnElement,observePage}=require('../page-tools.cjs');

test('supplier destinations and sanitized source preserve public routes',()=>{
  assert.equal(p.vendorUrl('https://www.jlc-3dp.cn/placeOrder'),true);
  assert.equal(p.vendorUrl('https://www.jlc.com.evil.example/'),false);
  assert.equal(p.vendorUrl('https://account:secret@www.jlc.com/'),false);
  assert.equal(p.safeUrl('https://www.jlc.com/order?accessToken=secret#/a?token=abc'),'https://www.jlc.com/order');
  assert.equal(p.safeUrl('about:blank'),'about:blank');
});
test('manufacturing upload binds allowed real path and SHA',()=>{
  const root=path.resolve(__dirname,'../..'),dir=path.join(root,'validation','20261024-electron-fixture');fs.mkdirSync(dir,{recursive:true});
  const file=path.join(dir,'upload.csv');fs.writeFileSync(file,'Designator,Quantity\nU1,1\n');const sha256=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  assert.deepEqual(p.checkedFiles(root,[{path:file,sha256}]),[fs.realpathSync(file)]);
  assert.throws(()=>p.checkedFiles(root,[{path:file,sha256:'0'.repeat(64)}]),{error:'file_mismatch'});
  assert.throws(()=>p.checkedFiles(root,[{path:path.join(root,'package.json'),sha256:'0'.repeat(64)}]),{error:'invalid_input'});
});
test('identified order/payment commit controls and write endpoints are denied',()=>{
  for(const label of ['提交订单','立即付款','确认下单','确认生产','Place Order'])assert.equal(p.commit(label),true,label);
  assert.equal(p.commit('获取报价'),false);
  assert.equal(p.blockedRequest('https://www.jlc.com/api/order/create','POST'),true);
  assert.equal(p.blockedRequest('https://www.jlc.com/api/quote/calculate','POST'),false);
});
function act(element,action='click'){
  const sandbox={globalThis:{__ffDesktopVersion:7,__ffDesktopElements:new Map([['v7-e0',element]])},document:{},Event:class{}};
  return vm.runInNewContext(`(${actOnElement.toString()})('v7-e0',${JSON.stringify(action)},'10')`,sandbox);
}
function button(label,dialog){return {isConnected:true,innerText:label,getAttribute:()=>'',closest:()=>dialog,click(){throw new Error('commit clicked');}};}
test('generic confirmation reads the dialog body, not just a neutral heading',()=>{
  const dialog={innerText:'提示\n是否提交订单？\n确定',querySelector:()=>({textContent:'提示'})};
  assert.equal(act(button('确定',dialog)).error,'quote_only');
});
test('stale controls and readonly model fields cannot be mutated',()=>{
  assert.equal(act({isConnected:false}).error,'stale_element');
  const readonly={isConnected:true,tagName:'INPUT',type:'text',readOnly:true,innerText:'',getAttribute:()=>'',closest:()=>null};
  assert.equal(act(readonly,'fill').error,'unsupported_control');
});

test('supplier remarks cannot be filled or cleared',()=>{
  const note={isConnected:true,tagName:'TEXTAREA',type:'textarea',innerText:'',getAttribute:name=>name==='placeholder'?'订单备注':'',closest:()=>null};
  assert.equal(act(note,'fill').error,'unsupported_control');
  assert.equal(act(note,'prepare-fill').error,'unsupported_control');
});
test('visible JLC customer label is masked and login prompts override authentication',()=>{
  function observe(lines){
    const account={tagName:'SPAN',innerText:'12345678A',children:[],closest:()=>null,getAttribute:()=>'',getBoundingClientRect:()=>({width:100,height:30})};
    const document={title:'报价',body:{innerText:lines},querySelectorAll:selector=>selector.startsWith('button,input')?[account]:[]};
    return vm.runInNewContext(`(${observePage.toString()})(1)`,{document,getComputedStyle:()=>({visibility:'visible',display:'block'}),globalThis:{},URL});
  }
  const authenticated=observe('12345678A\n上传模型\n报价 ￥10');
  assert.equal(authenticated.loginStatus,'authenticated');assert.equal(authenticated.accountLabel,'****678A');
  assert.equal(authenticated.elements[0].label,'****678A');assert.equal(JSON.stringify(authenticated).includes('12345678A'),false);
  assert.equal(observe('12345678A\n请登录再上传').loginStatus,'needs-login');
  assert.equal(observe('上传模型\n报价 ￥10').loginStatus,'unknown');
});
test('missing rAF still requires fresh connected geometry and an unobstructed hit',async()=>{
  let hitChecks=0;
  const element={isConnected:true,tagName:'BUTTON',type:'',innerText:'选择此单',getAttribute:()=>'',closest:()=>null,scrollIntoView(){},getBoundingClientRect:()=>({left:10,right:110,top:20,bottom:60}),contains:()=>false};
  const context={globalThis:{__ffDesktopVersion:7,__ffDesktopElements:new Map([['v7-e0',element]])},requestAnimationFrame(){},setTimeout,clearTimeout,innerWidth:800,innerHeight:600,document:{elementFromPoint(){hitChecks++;return element;}}};
  const allowed=await vm.runInNewContext(`(${actOnElement.toString()})('v7-e0','prepare-click','')`,context);
  assert.equal(allowed.ok,true);assert.equal(allowed.point.x,60);assert.equal(allowed.point.y,40);assert.equal(hitChecks,1);
  context.document.elementFromPoint=()=>({});
  const covered=await vm.runInNewContext(`(${actOnElement.toString()})('v7-e0','prepare-click','')`,context);
  assert.equal(covered.error,'unsupported_control');
});
test('cross-origin iframe reports its public source without reading its document',()=>{
  let reads=0;
  const frame={src:'https://other.example/quote?token=secret',getAttribute:()=>'',getBoundingClientRect:()=>({width:300,height:200}),get contentDocument(){reads++;throw new Error('cross-origin document read');}};
  const document={baseURI:'https://www.jlc-smt.com/order/replace',title:'报价',body:{innerText:'报价'},querySelectorAll:selector=>selector==='iframe'||selector==='iframe,frame'?[frame]:[]};
  const result=vm.runInNewContext(`(${observePage.toString()})(1)`,{document,getComputedStyle:()=>({visibility:'visible',display:'block'}),globalThis:{},URL});
  assert.equal(reads,0);assert.equal(result.elements.length,0);assert.equal(result.frames[0].src,'https://other.example/quote');
});

test('styled radio accepts its own associated label indicator but rejects other overlays',async()=>{
  const indicator={};
  const element={isConnected:true,tagName:'INPUT',type:'radio',value:'material-id',innerText:'',getAttribute:()=>'',closest:()=>null,scrollIntoView(){},getBoundingClientRect:()=>({left:10,right:30,top:20,bottom:40}),contains:()=>false};
  const label={control:element,contains:node=>node===indicator};element.labels=[label];
  const context={globalThis:{__ffDesktopVersion:7,__ffDesktopElements:new Map([['v7-e0',element]])},requestAnimationFrame:fn=>fn(),setTimeout,clearTimeout,innerWidth:800,innerHeight:600,document:{elementFromPoint:()=>indicator}};
  assert.equal((await vm.runInNewContext(`(${actOnElement.toString()})('v7-e0','prepare-click','')`,context)).ok,true);
  context.document.elementFromPoint=()=>({});
  assert.equal((await vm.runInNewContext(`(${actOnElement.toString()})('v7-e0','prepare-click','')`,context)).error,'unsupported_control');
  context.document.elementFromPoint=()=>indicator;label.control={};
  assert.equal((await vm.runInNewContext(`(${actOnElement.toString()})('v7-e0','prepare-click','')`,context)).error,'unsupported_control');
});

test('print material parameters can save a quote despite sample-order description, while commit text stays denied',async()=>{
  const dialog={innerText:'编辑打印参数\n选择材料：9000HE\n仅支持样品订单\n数量：1\n确认',querySelector:()=>({textContent:'编辑打印参数'})};
  const element={isConnected:true,tagName:'BUTTON',type:'button',innerText:'确认',getAttribute:()=>'',closest:()=>dialog,scrollIntoView(){},getBoundingClientRect:()=>({left:10,right:110,top:20,bottom:60}),contains:()=>false};
  const context={globalThis:{__ffDesktopVersion:7,__ffDesktopElements:new Map([['v7-e0',element]])},requestAnimationFrame:fn=>fn(),setTimeout,clearTimeout,innerWidth:800,innerHeight:600,document:{elementFromPoint:()=>element}};
  assert.equal((await vm.runInNewContext(`(${actOnElement.toString()})('v7-e0','prepare-click','')`,context)).ok,true);
  dialog.innerText+='\n提交订单';
  assert.equal((await vm.runInNewContext(`(${actOnElement.toString()})('v7-e0','prepare-click','')`,context)).error,'quote_only');
});
