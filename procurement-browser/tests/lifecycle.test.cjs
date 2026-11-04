const {test}=require('node:test');
const assert=require('node:assert/strict');
const {validateResume,completion,measuredStep}=require('../lifecycle.cjs');
const input={action:'submit-unpaid',projectId:'p1',projectVersion:29,quantity:5,files:[{sha256:'bom'},{sha256:'cpl'}],parts:[{designators:['J1']}],gerber:{sha256:'board'},manufacturingProfile:{settingsVersion:'ws_1',providerSelection:{shippingSource:'provider-default'}}};
const previous={...input,runId:'r1',taskKind:input.action};
test('resume preserves every source field and never escalates quotation to submission',()=>{
  validateResume(previous,{...input,resume:true});
  for(const [key,value] of Object.entries({projectId:'p2',projectVersion:30,quantity:6,files:[],parts:[],gerber:{sha256:'different'},action:'quote'}))
    assert.throws(()=>validateResume(previous,{...input,resume:true,[key]:value}),{code:'file_mismatch'});
  assert.throws(()=>validateResume(previous,{...input,resume:true,manufacturingProfile:{settingsVersion:'ws_2'}}),{code:'file_mismatch'});
  assert.throws(()=>validateResume({}, {...input,resume:true}),{code:'resume_missing'});
  validateResume(previous,{...input,projectId:'p2'});
});
test('supplier review is waiting, not a completed quote',()=>{
  for(const phase of ['waiting-pcb-review','waiting-smt-review']) {
    const result=completion({phase});
    assert.equal(result.status,'waiting');
    assert.ok(Date.parse(result.nextCheckAt)>Date.now());
  }
  assert.deepEqual(completion({phase:'awaiting-payment'}),{status:'completed',nextCheckAt:null});
});
test('step timing preserves failure and does not replay the operation',async()=>{
  const events=[];let calls=0;
  const hooks={check(){},start(){},event:(...args)=>events.push(args)};
  const error=new Error('supplier failed');
  await assert.rejects(measuredStep('upload',async()=>{calls++;throw error;},hooks),error);
  assert.equal(calls,1);assert.equal(events[0][0],'step-failed');assert.ok(events[0][1].elapsedSeconds>=0);
  assert.equal(await measuredStep('observe',async()=>42,hooks),42);
  assert.equal(events[1][0],'step-completed');
});
test('human ownership prevents the next step before side effects',async()=>{
  let calls=0;
  await assert.rejects(measuredStep('submit',async()=>{calls++;},{check(){throw new Error('human');}}),/human/);
  assert.equal(calls,0);
});
