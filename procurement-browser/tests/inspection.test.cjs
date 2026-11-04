const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const {inspectDom}=require('../inspection.cjs');
test('inspection masks invoice/address blocks and unknown form values without changing vendor DOM',async()=>{
 const temporary=path.resolve(__dirname,'../../.local/inspection-tests/tmp');fs.mkdirSync(temporary,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true,env:{...process.env,TEMP:temporary,TMP:temporary}});
 try{
  const page=await browser.newPage();
  await page.setContent(`<main><p>板子尺寸</p><input id="pcbLengthInput" value="5.6"><input id="pcbWidthInput" value="2.6">
   <p>需要发票</p><p>开票资料</p><button>测试开票公司</button><p>91310000TESTTAX00001</p>
   <p>确认订单方式</p><button>手动确认订单</button><p>收货地址</p><p>测试省测试区保密路99号</p><button>赵甲</button>
   <p>联系方式</p><p>赵甲 188[收货或开票资料已隐藏]7654</p><p>选择快递</p><button>顺丰电商标快</button>
   <input value="unknown-secret-company"><textarea>平台原始备注</textarea><p>总价</p><p>¥69.54</p><button>检查订单</button></main>`);
  const before=await page.locator('body').innerHTML(),result=await page.evaluate(inspectDom),serialized=JSON.stringify(result);
  for(const secret of ['测试开票公司','91310000TESTTAX00001','保密路','赵甲','7654','unknown-secret-company','平台原始备注'])assert(!serialized.includes(secret),secret);
  assert(result.text.includes('¥69.54'));assert(result.controls.some(c=>c.text==='手动确认订单'));assert(result.controls.some(c=>c.text==='检查订单'));
  assert.equal(result.inputs.find(i=>i.id==='pcbLengthInput').value,'5.6');assert.equal(result.inputs.find(i=>i.id==='pcbWidthInput').value,'2.6');
  assert.equal(await page.locator('body').innerHTML(),before);
 }finally{await browser.close();}
});
