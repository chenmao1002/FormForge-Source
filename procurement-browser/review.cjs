const fs=require('node:fs'),path=require('node:path');
async function observeReview(api,item) {
  if(!/^[a-f0-9]{64}$/.test(item.binding))throw new Error('审核订单绑定无效。');
  const directory=path.join(api.local,'orders',item.binding),file=path.join(directory,'state.json');
  const state=JSON.parse(fs.readFileSync(file,'utf8'));
  if(state.binding!==item.binding||state.input.projectId!==item.projectId||state.input.projectVersion!==item.projectVersion)
    throw new Error('审核订单身份不符。');
  if(item.phase==='waiting-pcb-review') {
    const pcb=await require('./order-submit.cjs').findPcb(api,state.fileName);
    if(pcb.displayNumber!==state.pcb?.displayNumber)throw new Error('审核查询返回另一PCB订单。');
    const waiting=pcb.row.includes('等待嘉立创审核');
    // Do not select the PCB or advance matching here: those are mutations.
    return {waiting,phase:waiting?'waiting-pcb-review':'pcb-review-changed',pcbOrderNumber:pcb.displayNumber,observedAt:pcb.observedAt,requiresResume:!waiting};
  }
  if(item.phase!=='waiting-smt-review')throw new Error('不支持的审核状态。');
  const progress={state,directory,save:()=>fs.writeFileSync(file,JSON.stringify(state,null,2))};
  const result=await require('./order-observe.cjs').observeSmt(api,progress);
  return {...result,waiting:result.phase==='waiting-smt-review'};
}
module.exports={observeReview};
