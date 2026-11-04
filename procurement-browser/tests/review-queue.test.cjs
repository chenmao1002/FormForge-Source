const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {ReviewQueue}=require('../review-queue.cjs');
function setup(t) {
  const base=path.resolve(__dirname,'../../.local/review-queue-tests');fs.mkdirSync(base,{recursive:true});
  const dir=fs.mkdtempSync(path.join(base,'case-'));t.after(()=>{assert.ok(dir.startsWith(base+path.sep));fs.rmSync(dir,{recursive:true,force:true});});return dir;
}
test('review resumes after restart, releases between polls, and stores no submission grant',async t=>{
  const dir=setup(t);let time=0;let q=new ReviewQueue(dir,()=>time);
  const cp={orderBinding:'a'.repeat(64),projectId:'p',projectVersion:29,quantity:10,result:{phase:'waiting-pcb-review'},submissionAuthorization:{secret:'not-persisted'}};
  q.enqueue(cp);assert.equal(q.due(),undefined);time=60000;
  q=new ReviewQueue(dir,()=>time);let calls=0;
  await q.observe(q.due(),async item=>{calls++;assert.equal(item.submissionAuthorization,undefined);return {waiting:true};});
  assert.equal(q.due(),undefined);assert.equal(calls,1);time=120000;
  await q.observe(q.due(),async()=>({waiting:false,phase:'pcb-review-changed'}));
  assert.equal(q.items[0].status,'ready');time=999999;assert.equal(q.due(),undefined);
  assert.ok(!fs.readFileSync(q.file,'utf8').includes('not-persisted'));
  assert.equal(fs.readFileSync(path.join(dir,'review-events.jsonl'),'utf8').trim().split('\n').length,2);
});
test('interrupted read-only observation can resume; failed queries do not retry mutations',async t=>{
  const dir=setup(t),q=new ReviewQueue(dir,()=>100000);
  q.enqueue({orderBinding:'b'.repeat(64),result:{phase:'waiting-smt-review'}});
  q.items[0].status='running';q.items[0].nextCheckAt=0;q.save();
  const restarted=new ReviewQueue(dir,()=>100000);assert.ok(restarted.due());
  await restarted.observe(restarted.due(),async()=>{throw new Error('login required');});
  assert.equal(restarted.items[0].status,'failed');assert.equal(restarted.due(),undefined);
  restarted.retry('b'.repeat(64));assert.ok(restarted.due());
  assert.throws(()=>restarted.retry('missing'));
});
