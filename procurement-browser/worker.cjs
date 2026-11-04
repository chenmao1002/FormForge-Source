const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const {safeUrl, vendorUrl, checkedFiles, commit, sensitive} = require('../desktop/policy.cjs');
const {blockedProcurementRequest,visibleFrame,validateSubmissionAuthorization}=require('./policy.cjs');
const {validateResume,completion,measuredStep}=require('./lifecycle.cjs');
const {validateParameters}=require('./parameters.cjs');
const {ReviewQueue}=require('./review-queue.cjs');
const {observeReview}=require('./review.cjs');
const {createPrintController}=require('./print.cjs');
const {inspectDom}=require('./inspection.cjs');

const ROOT = path.resolve(__dirname, '..');
const LOCAL = path.join(ROOT, '.local', 'playwright');
const PROFILE = path.join(LOCAL, 'profile');
const DESCRIPTOR = path.join(LOCAL, 'runtime.json');
const ENTRY = 'https://www.jlc-smt.com/order';
const stamp = () => new Date().toISOString();
const json = (p, v) => {fs.mkdirSync(path.dirname(p), {recursive:true}); fs.writeFileSync(p, JSON.stringify(v,null,2));};
fs.mkdirSync(LOCAL,{recursive:true});
const temporary=path.join(LOCAL,'tmp');fs.mkdirSync(temporary,{recursive:true});
process.env.TEMP=temporary;process.env.TMP=temporary;process.env.TMPDIR=temporary;
let context, active, busy=false, mode='agent', lastBlockedRequest=null, run=null;
let reviewActive=false,commandActive=false;
const printRunFile=path.join(LOCAL,'print-run.json');
let printRun=fs.existsSync(printRunFile)?JSON.parse(fs.readFileSync(printRunFile,'utf8')):null;
if(printRun?.status==='running'){
  printRun={...printRun,status:'interrupted',finishedAt:stamp()};json(printRunFile,printRun);
  const interruptedDir=path.join(LOCAL,'print-runs');fs.mkdirSync(interruptedDir,{recursive:true});
  fs.appendFileSync(path.join(interruptedDir,'events.jsonl'),JSON.stringify(printRun)+'\n');
}
let printController=createPrintController({root:ROOT,local:LOCAL,getContext:()=>context,setPage:p=>active=p});
const reviews=new ReviewQueue(LOCAL);
const settingsFile=path.join(LOCAL,'browser-settings.json');
let browserMode=fs.existsSync(settingsFile)?JSON.parse(fs.readFileSync(settingsFile,'utf8')).browserMode:'headless';
if(!['visible','minimized','headless'].includes(browserMode))browserMode='headless';
const token = crypto.randomBytes(36).toString('base64url');
const workerSourceSha256=crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
const stateFile = path.join(LOCAL,'checkpoint.json');
let checkpoint = fs.existsSync(stateFile) ? JSON.parse(fs.readFileSync(stateFile,'utf8')) : {};
if (checkpoint.status==='running') {
  fs.appendFileSync(path.join(LOCAL,'events.jsonl'),JSON.stringify({at:stamp(),runId:checkpoint.runId,phase:'interrupted',step:checkpoint.step})+'\n');
  checkpoint={...checkpoint,status:'interrupted',finishedAt:stamp()}; json(stateFile,checkpoint);
  json(path.join(LOCAL,'runs',checkpoint.runId,'interrupted.json'),checkpoint);
}
function event(phase, fields={}) {
  const row={at:stamp(),runId:run?.id || checkpoint.runId,phase,...fields};
  fs.appendFileSync(path.join(LOCAL,'events.jsonl'),JSON.stringify(row)+'\n');
  if(['run-completed','run-failed','interrupted'].includes(phase) && checkpoint.runId)
    json(path.join(LOCAL,'runs',checkpoint.runId,phase+'.json'),checkpoint);
}
function publicState() {
  return {available:true,adapter:'playwright-independent/1',pid:process.pid,workerSourceSha256,browserOpen:!!context,
    browserVersion:context?.browser()?.version(),browserMode,mode,busy,quoteOnly:checkpoint.taskKind!=='submit-unpaid',orderingEnabled:true,
    paymentEnabled:false,productionReleaseEnabled:false,url:active ? safeUrl(active.url()) : '',
    lastBlockedRequest,printTask:printRun,...printController.status(),reviews:reviews.items,checkpoint:{runId:checkpoint.runId,projectId:checkpoint.projectId,
      projectVersion:checkpoint.projectVersion,status:checkpoint.status,step:checkpoint.step,
      orderBinding:checkpoint.orderBinding,nextCheckAt:checkpoint.nextCheckAt,startedAt:checkpoint.startedAt,finishedAt:checkpoint.finishedAt,elapsedSeconds:checkpoint.elapsedSeconds,
      files:checkpoint.files?.map(f=>({name:path.basename(f.path),sha256:f.sha256})),
      result:checkpoint.result,error:checkpoint.error}};
}
async function launch() {
  if(context)return;
  context=await chromium.launchPersistentContext(PROFILE,{
    channel:'msedge',headless:browserMode==='headless',viewport:{width:1440,height:1000},acceptDownloads:true,
    downloadsPath:path.join(LOCAL,'downloads'),serviceWorkers:'block',
  });
  context.setDefaultTimeout(15000);
  await context.route('**/*',async route=>{
    const request=route.request();
    if(new URL(request.url()).hostname==='www.jlc-3dp.cn'){
      if(printController.networkBlocked(request)){
        lastBlockedRequest={at:stamp(),method:request.method(),url:safeUrl(request.url())};
        event('blocked-print-request',lastBlockedRequest);return route.abort('blockedbyclient');
      }
      return route.continue();
    }
    if(blockedProcurementRequest(request.url(),request.method(),reviewActive?{busy,taskKind:'review'}:{busy,taskKind:checkpoint.taskKind,step:checkpoint.step,submissionAuthorization:checkpoint.submissionAuthorization})) {
      lastBlockedRequest={at:stamp(),method:request.method(),url:safeUrl(request.url())};
      event('blocked-commit-request',lastBlockedRequest);
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });
  const track=page=>{
    active=page;
    if(browserMode==='minimized')minimize(page).catch(()=>event('minimize-failed'));
    page.on('dialog',async d=>{await d.dismiss();event('dialog-dismissed',{type:d.type()});});
    page.on('close',()=>{if(active===page)active=context?.pages().findLast(p=>!p.isClosed());});
    page.on('download',async download=>{
      const name=path.basename(download.suggestedFilename()).replace(/[<>:"/\\|?*]/g,'_');
      const destination=path.join(LOCAL,'downloads',Date.now()+'-'+name);
      try {await download.saveAs(destination);event('download',{name,sha256:crypto.createHash('sha256').update(fs.readFileSync(destination)).digest('hex')});}
      catch {event('download-failed',{name});}
    });
  };
  context.pages().forEach(track); context.on('page',track);
  if(!active)active=await context.newPage();
  context.on('close',()=>{context=null;active=null;});
  event('browser-launched',{browserVersion:context.browser().version()});
}
async function minimize(page) {
  const cdp=await context.newCDPSession(page);
  try {
    const {windowId}=await cdp.send('Browser.getWindowForTarget');
    await cdp.send('Browser.setWindowBounds',{windowId,bounds:{windowState:'minimized'}});
  }finally {await cdp.detach();}
}
async function open() {
  await launch();
  if(active.url()==='about:blank')await active.goto(ENTRY,{waitUntil:'domcontentloaded',timeout:45000});
  // Only an explicit human open may focus a visible window. Quotes never do.
  if(browserMode==='visible')await active.bringToFront();
  return publicState();
}
async function inspect() {
  if(!active || active.isClosed())return publicState();
  if(mode==='human')return publicState();
  const pages=[];
  for(const page of context.pages()) {
    if(page.isClosed())continue;
    const frames=[];
    for(const frame of page.frames()) {
      if(!vendorUrl(frame.url()) || /passport|\/login/i.test(frame.url())) {frames.push({url:safeUrl(frame.url()),private:true});continue;}
      if(!await visibleFrame(frame,page)) {frames.push({url:safeUrl(frame.url()),hidden:true});continue;}
      try {
        const data=await frame.evaluate(inspectDom);
        frames.push({url:safeUrl(frame.url()),...data});
      } catch {frames.push({url:safeUrl(frame.url()),loading:true});}
    }
    pages.push({active:page===active,url:safeUrl(page.url()),frames});
  }
  return {...publicState(),pages};
}
async function restart(body={}) {
  if(busy)throw Object.assign(new Error('询价正在执行，请等待当前步骤结束。'),{code:'busy'});
  const urls=context?.pages().map(p=>p.url()).filter(u=>vendorUrl(u)&&!/passport|\/login/i.test(u)) || checkpoint.recoveryUrls || [ENTRY];
  checkpoint.recoveryUrls=urls;json(stateFile,checkpoint);
  if(context)await context.close();
  if(body.browserMode) {
    if(!['visible','minimized','headless'].includes(body.browserMode))throw Object.assign(new Error('不支持的浏览器运行模式。'),{code:'invalid_input'});
    browserMode=body.browserMode;json(settingsFile,{browserMode});
  }
  await launch();
  // Browser restoration is verified separately from business restoration.
  const restored=[];
  for(const url of urls) {
    const page=restored.length ? await context.newPage() : active;
    try {await page.goto(url,{waitUntil:'domcontentloaded',timeout:45000});restored.push(page);}
    catch {event('restore-navigation-failed',{url:safeUrl(url)});}
  }
  active=restored.findLast(p=>/smt-order/.test(p.url())) || restored.at(-1) || active;
  if(browserMode==='minimized')await minimize(active);
  event('browser-restarted',{restoredPages:restored.length,browserMode});
  return publicState();
}
async function quote(body) {
  if(busy)throw Object.assign(new Error('已有询价任务执行中。'),{code:'busy'});
  if(mode==='human')throw Object.assign(new Error('请在登录完成后恢复智能体控制。'),{code:'human_control'});
  const pcbOnly=body.assemblyScope==='pcb-only';
  if(pcbOnly){validateParameters(body);body.files=[];body.parts=[];}
  if(!body.projectId || !Number.isInteger(body.projectVersion) || !Number.isInteger(body.quantity) || (!pcbOnly&&(body.files?.length!==2 || !body.parts?.length)))
    throw Object.assign(new Error('验证需要明确工程版本、数量及原BOM/坐标摘要。'),{code:'invalid_input'});
  validateParameters(body);
  if(!pcbOnly)checkedFiles(ROOT,body.files);
  if(['prepare-order','submit-unpaid'].includes(body.action))checkedFiles(ROOT,[body.gerber]);
  validateResume(checkpoint,body);
  validateSubmissionAuthorization(body);
  busy=true;
  try {
    await launch();
    if(active.url()==='about:blank')await active.goto(ENTRY,{waitUntil:'domcontentloaded',timeout:45000});
  } catch(error) {busy=false;throw error;}
  run={id:'pwq_'+crypto.randomBytes(8).toString('hex'),startedAt:stamp()};
  checkpoint={...(body.resume?checkpoint:{}),runId:run.id,previousRunId:checkpoint.runId,startedAt:run.startedAt,
    status:'running',step:'observing',projectId:body.projectId,projectVersion:body.projectVersion,
    quantity:body.quantity,assemblyQuantity:body.assemblyQuantity,assemblyScope:body.assemblyScope,board:body.board,files:body.files,parts:body.parts,taskKind:body.action,gerber:body.gerber,manufacturingProfile:body.manufacturingProfile,submissionAuthorization:body.submissionAuthorization,
    result:undefined,error:undefined,finishedAt:undefined,nextCheckAt:undefined,elapsedSeconds:undefined};json(stateFile,checkpoint);
  event('run-started',{previousRunId:checkpoint.previousRunId,files:body.files.map(f=>({name:path.basename(f.path),sha256:f.sha256}))});
  json(path.join(LOCAL,'runs',run.id,'started.json'),checkpoint);
  const step=(name,fn)=>measuredStep(name,fn,{
    check:()=>{if(mode!=='agent')throw Object.assign(new Error('任务已交人工接管。'),{code:'human_control'});},
    start:name=>{checkpoint.step=name;json(stateFile,checkpoint);event('step-started',{step:name});},event,
  });
  const adapterPath=require.resolve(['prepare-order','submit-unpaid'].includes(body.action)?'./order.cjs':'./smt.cjs');
  delete require.cache[adapterPath];
  let job;
  try {job=require(adapterPath);} catch(error) {busy=false;checkpoint.status='failed';checkpoint.finishedAt=stamp();json(stateFile,checkpoint);event('run-failed',{code:'adapter_load_failed'});run=null;throw error;}
  checkpoint.workflowSha256=crypto.createHash('sha256').update(fs.readFileSync(adapterPath)).digest('hex');
  json(stateFile,checkpoint);
  job.run({context,getPage:()=>active,setPage:p=>active=p,root:ROOT,local:LOCAL,
    files:body.files,parts:body.parts,gerber:body.gerber,submitRequested:body.action==='submit-unpaid',checkpoint,step,event,commit,sensitive,checkedFiles,
    canAct:()=>mode==='agent',save:()=>json(stateFile,checkpoint)}).then(result=>{
      checkpoint={...checkpoint,...completion(result),step:result.phase || 'quoted',finishedAt:stamp(),elapsedSeconds:(Date.now()-Date.parse(run.startedAt))/1000,result};
      json(stateFile,checkpoint);event('run-completed',{result});
      reviews.enqueue(checkpoint);
    }).catch(error=>{
      checkpoint={...checkpoint,status:error.code==='human_control'?'needs-login':'failed',finishedAt:stamp(),elapsedSeconds:(Date.now()-Date.parse(run.startedAt))/1000,
        error:{code:error.code || 'workflow_failed',message:String(error.message).split('\n')[0].slice(0,300)}};
      json(stateFile,checkpoint);event('run-failed',{step:checkpoint.step,error:checkpoint.error});
    }).finally(()=>{busy=false;run=null;});
  return publicState();
}
async function command(body) {
  if(reviewActive && body.action!=='inspect')throw Object.assign(new Error('正在只读查询审核状态，请稍后操作。'),{code:'busy'});
  switch(body.action) {
    case 'open':return open();
    case 'inspect':return inspect();
    case 'print-quote':case 'print-prepare':case 'print-submit-unpaid':case 'print-observe':return printCommand(body);
    case 'review-retry':reviews.retry(body.orderBinding);return publicState();
    case 'quote':return quote(body);
    case 'prepare-order':return quote(body);
    case 'submit-unpaid':return quote(body);
    case 'restart':return restart(body);
    case 'handoff':
      if(busy)throw Object.assign(new Error('当前步骤尚未结束，请结束后接管。'),{code:'busy'});
      mode='human';return restart({browserMode:'visible'});
    case 'resume':
      if(busy)throw Object.assign(new Error('任务尚未结束。'),{code:'busy'});
      await restart({browserMode:'headless'});mode='agent';return publicState();
    case 'close':if(busy)throw Object.assign(new Error('任务尚未结束。'),{code:'busy'});mode='human';if(context)await context.close();return publicState();
    case 'shutdown':if(busy)throw Object.assign(new Error('任务尚未结束。'),{code:'busy'});if(context)await context.close();setTimeout(()=>server.close(()=>process.exit(0)),100);return {ok:true};
    default:throw Object.assign(new Error('不支持的验证动作。'),{code:'invalid_input'});
  }
}
async function printCommand(body){
  if(busy)throw Object.assign(new Error('已有采购任务执行中。'),{code:'busy'});
  if(mode!=='agent')throw Object.assign(new Error('请登录完成后交回控制。'),{code:'human_control'});
  checkedFiles(ROOT,body.specification?.files);
  const printPath=require.resolve('./print.cjs');
  if(printController.status().printAdapterSourceSha256!==crypto.createHash('sha256').update(fs.readFileSync(printPath)).digest('hex')){
    delete require.cache[printPath];printController=require(printPath).createPrintController({root:ROOT,local:LOCAL,getContext:()=>context,setPage:p=>active=p});
  }
  busy=true;
  printRun={requestId:body.requestId,action:body.action,projectId:body.specification.projectId,
    projectVersion:body.specification.projectVersion,status:'running',startedAt:stamp()};
  json(printRunFile,printRun);
  const execute=async()=>{
    try{
      await launch();const result=await printController.command(body);
      printRun={...printRun,status:'completed',finishedAt:stamp(),result};
    }catch(error){
      printRun={...printRun,status:(error.error||error.code)==='human_control'?'needs-login':'failed',finishedAt:stamp(),
        error:{code:error.error||error.code||'print_failed',message:String(error.message).split('\n')[0].slice(0,300)}};
    }finally{
      printRun.elapsedSeconds=(Date.parse(printRun.finishedAt)-Date.parse(printRun.startedAt))/1000;
      json(printRunFile,printRun);
      const dir=path.join(LOCAL,'print-runs');fs.mkdirSync(dir,{recursive:true});
      fs.appendFileSync(path.join(dir,'events.jsonl'),JSON.stringify(printRun)+'\n');
      busy=false;
    }
  };
  execute().catch(()=>{busy=false;});return publicState();
}
async function pollReviews() {
  if(busy || commandActive || mode!=='agent' || browserMode!=='headless')return;
  const item=reviews.due();if(!item)return;
  busy=true;reviewActive=true;
  try {
    await reviews.observe(item,async()=>{
      await launch();
      return observeReview({local:LOCAL,context,getPage:()=>active,setPage:p=>active=p,
        checkpoint:{quantity:item.quantity},step:async(name,fn)=>fn(),event:()=>{}},item);
    });
  } finally {reviewActive=false;busy=false;}
}
setInterval(()=>pollReviews().catch(error=>event('review-poll-failed',{code:error.code||'review_failed'})),15000).unref();
const server=http.createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json; charset=utf-8');
  if(req.headers.authorization!=='Bearer '+token){res.writeHead(403);return res.end(JSON.stringify({error:'access_denied'}));}
  try {
    let result;
    if(req.method==='GET'&&req.url==='/status')result=publicState();
    else if(req.method==='POST'&&req.url==='/command'){
      let content='';for await(const chunk of req){content+=chunk;if(content.length>32000)throw new Error('请求过大。');}
      if(commandActive)throw Object.assign(new Error('另一浏览器控制操作尚未结束。'),{code:'busy'});
      commandActive=true;
      try {result=await command(JSON.parse(content));}finally{commandActive=false;}
    }else{res.writeHead(404);return res.end('{}');}
    res.end(JSON.stringify(result));
  }catch(error){res.writeHead(409);res.end(JSON.stringify({error:error.code || 'browser_unavailable',message:String(error.message).split('\n')[0].slice(0,300)}));}
});
server.listen(0,'127.0.0.1',()=>{
  json(DESCRIPTOR,{protocol:1,pid:process.pid,port:server.address().port,token,startedAt:stamp()});
  console.log('FormForge Playwright quotation worker ready.');
});
