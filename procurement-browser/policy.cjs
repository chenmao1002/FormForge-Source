const {blockedRequest}=require('../desktop/policy.cjs');
function blockedProcurementRequest(url,method,state) {
  const pathname=new URL(url).pathname;
  const payment=/\/(?:payOrder|confirmPayment|autoPay|releaseProduction)(?:[/?]|$)|\/(?:payment|pay)\/(?:create|submit|confirm|pay|checkout)(?:[A-Z_/-]|$)/i.test(pathname)
    || (method==='POST' && /\/smtOrderPay\//.test(pathname) && !pathname.endsWith('/checkSmtOrderIsCanPay'));
  const knownOrder=method==='POST' && /\/(?:ClientSubmitOrder|saveSmtOrder)$/.test(pathname);
  const expected=pathname.endsWith('/ClientSubmitOrder')?'submit-pcb':pathname.endsWith('/saveSmtOrder')?'submit-smt':null;
  const grant=state.submissionAuthorization;
  const submitting=knownOrder && state.busy && state.taskKind==='submit-unpaid' && state.step===expected
    && typeof grant?.approvalId==='string' && grant.allowedSteps?.includes(expected);
  return payment || ((knownOrder || blockedRequest(url,method)) && !submitting);
}
async function visibleFrame(frame,page) {
  if(frame===page.mainFrame())return true;
  try{return await (await frame.frameElement()).isVisible();}catch{return false;}
}
function validateSubmissionAuthorization(body){
  if(body.action!=='submit-unpaid')return;
  const grant=body.submissionAuthorization;
  if(!grant || typeof grant.approvalId!=='string' || grant.approvalId!==body.approvalId
    || !/^[a-f0-9]{64}$/.test(grant.intentSha256) || !Array.isArray(grant.allowedSteps)
    || grant.allowedSteps.length!==2 || !['submit-pcb','submit-smt'].every(s=>grant.allowedSteps.includes(s)))
    throw Object.assign(new Error('缺少公共服务核验后的本次提交授权。'),{code:'approval_required'});
}
module.exports={blockedProcurementRequest,visibleFrame,validateSubmissionAuthorization};
