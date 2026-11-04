const path=require('node:path');
function verifyObservation(ids,records,wrappers,spec){
  const orders=ids.map(id=>{
    const r=records.get(id),wrapper=wrappers.get(id);
    if(!r||!/^G\d{10,20}$/.test(r.orderCode||'')||wrapper?.selectOrderInfoVO?.orderAccessId!==id||!Array.isArray(wrapper.orderItemVOS))throw new Error('创建回执与订单明细身份未闭合。');
    const amount=r.selectOrderAmountVO,time=r.selectOrderTimeVO;
    if(!Number.isFinite(amount?.orderPayableAmount)||amount.orderActualAmount!==0||time?.orderPaymentTime!==null||wrapper.selectOrderDetailsVO?.submitFactory!==false||wrapper.selectOrderDetailsVO?.confirmOrderType!==true)throw new Error('订单未付款、人工确认或未放行依据不符。');
    return {orderNumber:r.orderCode,providerStatus:r.orderStatusName,payableCny:amount.orderPayableAmount,paidCny:0,shippingCny:amount.orderExpressFee,
      createdAt:time.orderCreateTime,paymentTime:null,parts:wrapper.orderItemVOS.map(item=>({fileName:item.fileName,quantity:item.itemCount,
        material:item.materialName,color:item.materialColor,leadTimeHours:item.materialDeliveryTime,
        modelIssues:(item.modelAbnormalForfaceList||[]).map(a=>({code:a.abnormalCode,name:a.abnormalName,description:a.abnormalDesc}))}))};
  });
  const parts=orders.flatMap(o=>o.parts),expected=spec.files.map(f=>path.basename(f.path)),total=orders.reduce((n,o)=>n+o.payableCny,0);
  if(new Set(orders.map(o=>o.orderNumber)).size!==ids.length||!Number.isFinite(spec.expectedPayableCny)||Math.abs(total-spec.expectedPayableCny)>0.005)throw new Error('新订单号或金额与结算复核不符。');
  if(parts.length!==expected.length||new Set(parts.map(p=>p.fileName)).size!==expected.length||!parts.every(p=>expected.includes(p.fileName)&&p.quantity===spec.copiesPerPart&&p.material===spec.material&&p.color==='白色'&&p.leadTimeHours===spec.leadTimeHours))throw new Error('订单明细文件、数量、材料或交期不符。');
  return {orders,totalCny:total,parts:parts.length,unpaidVerified:true,modelIssuesObserved:parts.some(p=>p.modelIssues.length>0)};
}
module.exports={verifyObservation};
