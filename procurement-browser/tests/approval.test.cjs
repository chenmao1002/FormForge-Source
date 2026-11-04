const {test}=require('node:test');
const assert=require('node:assert/strict');
const {blockedProcurementRequest,validateSubmissionAuthorization}=require('../policy.cjs');
const grant={approvalId:'approval-fixture',intentSha256:'a'.repeat(64),allowedSteps:['submit-pcb','submit-smt']};
test('submission action alone cannot authorize a worker',()=>{
  assert.throws(()=>validateSubmissionAuthorization({action:'submit-unpaid'}),{code:'approval_required'});
  assert.throws(()=>validateSubmissionAuthorization({action:'submit-unpaid',approvalId:'other',submissionAuthorization:grant}));
  validateSubmissionAuthorization({action:'submit-unpaid',approvalId:grant.approvalId,submissionAuthorization:grant});
  validateSubmissionAuthorization({action:'quote'});
});
test('only the matching unpaid step is allowed; payment and unrelated writes remain blocked',()=>{
  const pcb='https://www.jlc.com/api/NewOrder/ClientSubmitOrder',smt='https://www.jlc-smt.com/smtClientOrder/v1/saveSmtOrder';
  const state={busy:true,taskKind:'submit-unpaid',step:'submit-pcb',submissionAuthorization:grant};
  assert.equal(blockedProcurementRequest(pcb,'POST',state),false);
  assert.equal(blockedProcurementRequest(smt,'POST',state),true);
  assert.equal(blockedProcurementRequest(pcb,'POST',{...state,submissionAuthorization:undefined}),true);
  assert.equal(blockedProcurementRequest(pcb,'POST',{...state,busy:false}),true);
  assert.equal(blockedProcurementRequest(pcb,'POST',{...state,taskKind:'quote'}),true);
  assert.equal(blockedProcurementRequest(smt,'POST',{...state,step:'submit-smt'}),false);
  for(const suffix of ['/payment/confirm','/payOrder','/releaseProduction','/smtOrderPay/submit'])
    assert.equal(blockedProcurementRequest('https://www.jlc.com'+suffix,'POST',state),true);
});
