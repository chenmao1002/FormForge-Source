// Resume identity includes every input that can change the purchased product.
function validateResume(previous, body) {
  if (!body.resume) return;
  if (!previous.runId) throw Object.assign(new Error('没有可恢复的采购任务。'), {code:'resume_missing'});
  for (const key of ['projectId','projectVersion','quantity','assemblyQuantity','assemblyScope','files','parts','gerber','board','manufacturingProfile','taskKind']) {
    const next = key === 'taskKind' ? body.action : body[key];
    const old = key==='board' && next && !previous.board && previous.gerber && previous.quantity===5
      ? {widthMm:26,heightMm:32,layers:2,thicknessMm:1.6,side:'top'} : previous[key];
    if (JSON.stringify(old) !== JSON.stringify(next))
      throw Object.assign(new Error('恢复输入与原任务不同：'+key), {code:'file_mismatch'});
  }
}
function completion(result) {
  const waiting = ['waiting-pcb-review','waiting-smt-review'].includes(result.phase);
  return {status:waiting?'waiting':'completed', nextCheckAt:waiting?new Date(Date.now()+60000).toISOString():null};
}
async function measuredStep(name, fn, hooks) {
  hooks.check(); hooks.start(name);
  const started=performance.now();
  try {
    const result=await fn();
    hooks.event('step-completed',{step:name,elapsedSeconds:(performance.now()-started)/1000});
    return result;
  } catch(error) {
    hooks.event('step-failed',{step:name,elapsedSeconds:(performance.now()-started)/1000,code:error.code||'workflow_failed'});
    throw error;
  }
}
module.exports={validateResume,completion,measuredStep};
