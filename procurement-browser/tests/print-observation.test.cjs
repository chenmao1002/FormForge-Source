const test=require('node:test'),assert=require('node:assert/strict');
const {verifyObservation}=require('../print-observation.cjs');
function fixture(){
  const ids=['receipt-a','receipt-b'];
  const spec={files:[{path:'parts/front.stl'},{path:'parts/rear.stl'}],copiesPerPart:1,material:'9000HE',leadTimeHours:48,expectedPayableCny:13};
  const records=new Map(),wrappers=new Map();
  ids.forEach((id,i)=>{
    const r={orderAccessId:id,orderCode:'G2610250227'+i,orderStatusName:'待审核',selectOrderAmountVO:{orderPayableAmount:i?5:8,orderActualAmount:0,orderExpressFee:i?0:5},selectOrderTimeVO:{orderPaymentTime:null,orderCreateTime:'2026-10-25'}};
    records.set(id,r);wrappers.set(id,{selectOrderInfoVO:r,selectOrderDetailsVO:{submitFactory:false,confirmOrderType:true},orderItemVOS:[{fileName:i?'rear.stl':'front.stl',itemCount:1,materialName:'9000HE',materialColor:'白色',materialDeliveryTime:48,modelAbnormalForfaceList:i?[{abnormalCode:'THIN_WALL',abnormalName:'薄壁结构',abnormalDesc:'建议加厚',imageUrl:'https://example.invalid/private?token=secret'}]:[]}]});
  });
  return {ids,spec,records,wrappers};
}
const verify=f=>verifyObservation(f.ids,f.records,f.wrappers,f.spec);
test('receipt identities reconcile split orders, all parts, unpaid totals and model issues',()=>{
  const result=verify(fixture());assert.equal(result.totalCny,13);assert.equal(result.parts,2);assert.equal(result.unpaidVerified,true);assert.equal(result.modelIssuesObserved,true);
  assert.equal(result.orders[1].parts[0].modelIssues[0].code,'THIN_WALL');assert.ok(!JSON.stringify(result).includes('secret'));
});
test('missing or unrelated native detail cannot satisfy the receipt',()=>{
  const f=fixture();f.wrappers.delete('receipt-b');assert.throws(()=>verify(f),/身份/);
  const g=fixture();g.wrappers.get('receipt-b').selectOrderInfoVO={orderAccessId:'old-receipt'};assert.throws(()=>verify(g),/身份/);
});
test('paid or released order is not reported as unpaid',()=>{
  for(const mutate of [f=>f.records.get('receipt-a').selectOrderAmountVO.orderActualAmount=8,f=>f.records.get('receipt-a').selectOrderTimeVO.orderPaymentTime='2026-10-25',f=>f.wrappers.get('receipt-a').selectOrderDetailsVO.submitFactory=true,f=>f.wrappers.get('receipt-a').selectOrderDetailsVO.confirmOrderType=false]){
    const f=fixture();mutate(f);assert.throws(()=>verify(f),/未付款/);
  }
});
test('amount drift and duplicate order numbers fail reconciliation',()=>{
  const f=fixture();f.spec.expectedPayableCny=12;assert.throws(()=>verify(f),/金额/);
  const g=fixture();g.records.get('receipt-b').orderCode=g.records.get('receipt-a').orderCode;assert.throws(()=>verify(g),/订单号/);
});
test('omitted, extra, duplicate, wrong quantity or material parts fail reconciliation',()=>{
  for(const mutate of [p=>p.pop(),p=>p.push({...p[0]}),p=>p[0].fileName='front.stl',p=>p[0].itemCount=2,p=>p[0].materialName='9600',p=>p[0].materialColor='黑色',p=>p[0].materialDeliveryTime=72]){
    const f=fixture();mutate(f.wrappers.get('receipt-b').orderItemVOS);assert.throws(()=>verify(f),/明细/);
  }
});
