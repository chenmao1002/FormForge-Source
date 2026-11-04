const {test}=require('node:test'),assert=require('node:assert/strict');
const {printRequestBlocked,printCommitAllowed,validateQuote,keyFor,createPrintController}=require('../print-order.cjs');
const make=(path,body={confirmOrderType:'CUSTOMER_CONFIRM',preOrderBatchNumber:'native-batch'})=>({url:'https://www.jlc-3dp.cn/3dpportal'+path,method:'POST',uploadData:[{bytes:Buffer.from(JSON.stringify(body))}]});
const grant={step:'submit-print',approvalId:'approval-test',allowedSteps:['submit-print'],batch:'native-batch',used:false};
test('3D native creation is limited to one reviewed manual-confirmation batch',()=>{
 assert.equal(printRequestBlocked(make('/preorder/submitOrder'),grant),false);
 for(const bad of [null,{...grant,used:true},{...grant,step:'quote'},{...grant,allowedSteps:['submit-pcb','submit-smt']},{...grant,batch:'another-batch'}])assert.equal(printRequestBlocked(make('/preorder/submitOrder'),bad),true);
 assert.equal(printRequestBlocked(make('/preorder/submitOrder',{confirmOrderType:'AUTO',preOrderBatchNumber:'native-batch'}),grant),true);
 for(const path of ['/settlement/instantPayV2','/settlement/immediatelyPay','/settlement/unionPay','/order/freeOrder'])assert.equal(printRequestBlocked(make(path),grant),true);
});
test('dedicated native buttons never authorize payment or a different site/step',()=>{
 assert(printCommitAllowed('checkout','提交订单','https://www.jlc-3dp.cn/placeOrder'));
 assert(printCommitAllowed('submit','立即下单','https://www.jlc-3dp.cn/submitOrder'));
 for(const [step,label,url] of [['submit','立即支付','https://www.jlc-3dp.cn/submitOrder'],['checkout','立即下单','https://www.jlc-3dp.cn/submitOrder'],['submit','立即下单','https://www.jlc.com/submitOrder'],[null,'立即下单','https://www.jlc-3dp.cn/submitOrder']])assert(!printCommitAllowed(step,label,url));
});
test('quote checks actual uploaded hashes, material, color, quantity and price',()=>{
 const spec={files:[{path:'part.stl',sha256:'frozen'}],copiesPerPart:1,quotedExcludingTaxCny:5.5};
 const snapshot={text:'part.stl',elements:[{label:'9000HE'},{label:'白色'},{label:'打磨：不需要'},{label:'喷漆：不需要'},{label:'镶嵌螺母：不需要'}]},native={quantities:[1],quoteExcludingTax:5.5};
 validateQuote(snapshot,native,spec,new Map([['part.stl','frozen']]));
 for(const bad of [{quantities:[2],quoteExcludingTax:5.5},{quantities:[1],quoteExcludingTax:6},{quantities:[1],quoteExcludingTax:null}])assert.throws(()=>validateQuote(snapshot,bad,spec,new Map([['part.stl','frozen']])));
 assert.throws(()=>validateQuote(snapshot,native,spec,new Map([['part.stl','changed']])));
 assert.throws(()=>validateQuote({...snapshot,elements:[{label:'9600'},{label:'白色'}]},native,spec,new Map([['part.stl','frozen']])));
 assert.throws(()=>validateQuote({...snapshot,elements:snapshot.elements.map(e=>e.label==='打磨：不需要'?{label:'打磨：粗磨'}:e)},native,spec,new Map([['part.stl','frozen']])));
 assert.equal(keyFor({...spec,expectedPayableCny:10}),keyFor({...spec,expectedPayableCny:11}));
 assert.equal(keyFor(spec),keyFor({...spec,quotedExcludingTaxCny:6,files:[{path:'renamed.stl',sha256:'frozen'}]}));
});
test('failure before a native request can be corrected; an uncertain sent request cannot be resent',async t=>{
 const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
 const base=path.resolve(__dirname,'../../.local/print-order-tests');fs.mkdirSync(base,{recursive:true});
 const root=fs.mkdtempSync(path.join(base,'case-'));t.after(()=>{assert(root.startsWith(base+path.sep));fs.rmSync(root,{recursive:true,force:true});});
 fs.mkdirSync(path.join(root,'validation'));const file=path.join(root,'validation','part.stl');fs.writeFileSync(file,require('../../tests/stl-fixture.cjs')());
 const spec={files:[{path:file,sha256:crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')}],copiesPerPart:1,material:'9000HE',color:'white',finish:'no-sanding-no-paint-no-inserts',leadTimeHours:48,quotedExcludingTaxCny:5.5,expectedPayableCny:10};
 const local=path.join(root,'local'),directory=path.join(local,'print-orders');fs.mkdirSync(directory,{recursive:true});const statePath=path.join(directory,keyFor(spec)+'.json');
 fs.writeFileSync(statePath,JSON.stringify({key:keyFor(spec),specification:spec,phase:'checkout-prepared',batch:'native-batch'}));
 let send=false,controller;const api={root,local,getContents:()=>({executeJavaScriptInIsolatedWorld:async()=>({pathname:'/submitOrder',manualConfirmChecked:true,fileNames:['part.stl'],payable:10})}),status:()=>({}),inspect:async()=>({elements:[{id:'v1-e1',tag:'button',label:'立即下单',disabled:false}]}),elementAction:async()=>{if(send)assert.equal(controller.networkBlocked(make('/preorder/submitOrder')),false);throw Error('simulated observation failure');}};
 controller=createPrintController(api);const body={action:'print-submit-unpaid',specification:spec,submissionAuthorization:{approvalId:'review',intentSha256:'a'.repeat(64),allowedSteps:['submit-print']}};
 await assert.rejects(controller.command(body));assert.equal(JSON.parse(fs.readFileSync(statePath)).phase,'checkout-prepared');
 send=true;await assert.rejects(controller.command(body));assert.ok(JSON.parse(fs.readFileSync(statePath)).nativeRequestSentAt);
 await assert.rejects(controller.command(body),e=>e.error==='print_reconcile_required');
});
