const {app,BrowserWindow,WebContentsView,ipcMain,session} = require('electron');
const path=require('node:path');
const fs=require('node:fs');
const http=require('node:http');
const crypto=require('node:crypto');
const {spawnSync}=require('node:child_process');
const policy=require('./policy.cjs');
const {observePage,actOnElement}=require('./page-tools.cjs');
const {printCommitAllowed}=require('./print-order.cjs');

const root=path.resolve(__dirname,'..');
const testOrigin=process.env.FORMFORGE_DESKTOP_TEST_ORIGIN || null;
if (testOrigin && !/^http:\/\/127\.0\.0\.1:\d+$/.test(testOrigin)) throw new Error('Invalid isolated test origin');
const runtimeRoot=path.join(root,'.local','desktop',...(testOrigin?['fixture']:[]));
fs.mkdirSync(runtimeRoot,{recursive:true});
if(process.platform==='win32'){
  const identity=spawnSync('whoami.exe',['/user','/fo','csv','/nh'],{encoding:'utf8',windowsHide:true});
  const sid=(identity.stdout||'').match(/S-1-5-(?:\d+-)*\d+/)?.[0];
  if(!sid)throw new Error('Cannot identify desktop session owner');
  const acl=spawnSync('icacls.exe',[runtimeRoot,'/inheritance:r','/grant:r',`*${sid}:(OI)(CI)F`,'*S-1-5-18:(OI)(CI)F'],{windowsHide:true});
  if(acl.status!==0)throw new Error('Cannot protect desktop session directory');
}
app.setPath('userData',path.join(runtimeRoot,testOrigin?'test-profile':'profile'));
app.setPath('sessionData',path.join(runtimeRoot,testOrigin?'test-sessions':'sessions'));
app.setPath('logs',path.join(runtimeRoot,'logs'));
app.setPath('crashDumps',path.join(runtimeRoot,'crashes'));
if(!app.requestSingleInstanceLock())app.exit(0);
app.on('second-instance',()=>{if(win&&!win.isDestroyed()){if(win.isMinimized())win.restore();win.focus();}});
const servicePort=Number(process.env.FORMFORGE_PORT||17840);
const workbenchUrl=`http://127.0.0.1:${servicePort}/`;
let win,workbench,supplier,supplierSession,server,printController;
const printUploads=new Map();let printAdapterSourceSha256=null;
const supplierParents=[];
let mode='agent',provider='jlc-smt',version=0,latestSnapshot=null,queue=Promise.resolve();
let split=.47,showSupplier=true,supplierFullscreen=false;
const token=crypto.randomBytes(32).toString('hex');
const results=new Map();
let quitting=false;
let navigationSequence=0,lastNavigation=null,navigationIssue=null,lastBlockedRequest=null;

function live(){return !!win&&!win.isDestroyed()&&!!supplier&&!supplier.webContents.isDestroyed();}
function dirty(){version++;latestSnapshot=null;}
function status(){return {available:live(),protocol:1,electronVersion:process.versions.electron,mode,provider,windowOpen:live(),minimized:live()?win.isMinimized():false,visible:live()?win.isVisible():false,pageVersion:version,supplierFullscreen,viewMode:supplierFullscreen?'supplier':(showSupplier?'split':'workbench'),popupDepth:supplierParents.length,quoteOnly:true,orderingEnabled:false,paymentEnabled:false,productionReleaseEnabled:false,url:live()?policy.safeUrl(supplier.webContents.getURL()):'',title:live()?supplier.webContents.getTitle():'',loginStatus:latestSnapshot?.loginStatus||'unknown',accountLabel:latestSnapshot?.accountLabel||null,lastNavigation,navigationIssue,lastBlockedRequest,...printController?.status()};}
function recordNavigation(url,source){
  const supported=policy.vendorUrl(url,testOrigin)||url==='about:blank';
  lastNavigation={sequence:++navigationSequence,source,targetUrl:policy.safeUrl(url),supported,observedAt:new Date().toISOString()};
  navigationIssue=supported?null:{...lastNavigation,error:'unsupported_destination',reason:'目标不在已核对的供应商站点范围，已阻止导航；未放宽域名或证书校验。'};
  ui();return supported;
}
function ui(){if(win&&!win.isDestroyed())win.webContents.send('desktop:status',status());}
function bounds(){
  if(!win||win.isDestroyed()||!workbench||!supplier)return;
  const {width,height}=win.getContentBounds(), top=88, divider=8;
  workbench.setVisible(!supplierFullscreen);
  if(supplierFullscreen){supplier.setVisible(true);supplier.setBounds({x:0,y:top,width,height:Math.max(1,height-top)});return;}
  const left=showSupplier?Math.round(width*split):width;
  workbench.setBounds({x:0,y:top,width:Math.max(1,left-divider/2),height:Math.max(1,height-top)});
  supplier.setBounds({x:left+divider/2,y:top,width:Math.max(1,width-left-divider/2),height:Math.max(1,height-top)});
  supplier.setVisible(showSupplier);
}
function trustedSender(event){return win&&!win.isDestroyed()&&event.sender===win.webContents;}
function returnToSupplierParent(closeCurrent=true){
  if(!win||win.isDestroyed())return false;
  let previous;while(supplierParents.length){const candidate=supplierParents.pop();if(!candidate.webContents.isDestroyed()){previous=candidate;break;}}
  if(!previous)return false;
  const current=supplier;supplier=previous;
  win.contentView.removeChildView(current);current.setVisible(false);
  dirty();bounds();ui();supplier.webContents.focus();
  if(closeCurrent&&!current.webContents.isDestroyed())current.webContents.close();
  return true;
}
function wireSupplier(contents){
  contents.setWindowOpenHandler(({url})=>{
    if(!recordNavigation(url,'window-open'))return {action:'deny'};
    return {action:'allow',createWindow:options=>{
      const child=new WebContentsView({...options,webPreferences:{...options.webPreferences,session:supplierSession,sandbox:true,contextIsolation:true,nodeIntegration:false,preload:undefined,backgroundThrottling:false}});
      supplier.setVisible(false);supplierParents.push(supplier);supplier=child;
      wireSupplier(child.webContents);win.contentView.addChildView(child);showSupplier=true;dirty();bounds();ui();
      return child.webContents;
    }};
  });
  contents.on('will-navigate',(event,url)=>{if(!recordNavigation(url,'will-navigate'))event.preventDefault();});
  contents.on('will-redirect',(event,url)=>{if(!recordNavigation(url,'will-redirect'))event.preventDefault();});
  const active=()=>supplier&&contents===supplier.webContents;
  contents.on('did-start-navigation',(_e,_url,_inPlace,isMainFrame)=>{if(active()&&isMainFrame){dirty();ui();}});
  contents.on('did-navigate-in-page',(_event,_url,isMainFrame)=>{if(active()&&isMainFrame){dirty();ui();}});
  contents.on('did-finish-load',()=>{if(active()){dirty();ui();}});
  contents.on('page-title-updated',()=>{if(active())ui();});
  contents.on('render-process-gone',()=>{if(active()){dirty();ui();}});
  contents.on('destroyed',()=>{if(active())returnToSupplierParent(false);});
}
async function inspect(){
  if(!live())policy.fail('browser_unavailable','请先打开 FormForge 桌面验证版。',503);
  if(!policy.vendorUrl(supplier.webContents.getURL(),testOrigin)) return {...status(),elements:[],text:'请选择供应商入口。',quoteCandidates:[]};
  const evidence=path.join(runtimeRoot,'evidence');fs.mkdirSync(evidence,{recursive:true});
  for(let attempt=0;attempt<2;attempt++){
    const v=++version;
    const observation=await supplier.webContents.executeJavaScriptInIsolatedWorld(999,[{code:`(${observePage.toString()})(${v})`}]);
    const screenshot=await supplier.webContents.capturePage();
    const workbenchScreenshot=supplierFullscreen?null:await workbench.webContents.capturePage();
    if(v!==version)continue;
    const stamp=Date.now();
    const screenshotPath=path.join(evidence,`supplier-${stamp}-${v}.png`);
    const workbenchScreenshotPath=workbenchScreenshot?path.join(evidence,`workbench-${stamp}-${v}.png`):null;
    fs.writeFileSync(screenshotPath,screenshot.toPNG());if(workbenchScreenshot)fs.writeFileSync(workbenchScreenshotPath,workbenchScreenshot.toPNG());
    latestSnapshot={...observation,pageVersion:v};
    ui();return {...status(),...observation,pageVersion:v,snapshotStable:true,screenshotPath,workbenchScreenshotPath};
  }
  latestSnapshot=null;
  return {...status(),snapshotStable:false,elements:[],frames:[],text:'供应商页面正在跳转或加载，请重新观察。',quoteCandidates:[]};
}
async function elementAction(command,printStep=null){
  if(!latestSnapshot||!latestSnapshot.elements.some(x=>x.id===command.elementId)||!command.elementId.startsWith(`v${version}-`))policy.fail('stale_element','页面已变化，请先重新观察。',409);
  const control=latestSnapshot.elements.find(x=>x.id===command.elementId);
  if(policy.commit(control.label)&&!printCommitAllowed(printStep,control.label.trim(),supplier.webContents.getURL()))policy.fail('quote_only','本验证版只允许报价，不提交订单或付款。',403);
  const action=command.action==='upload'?'mark-upload':(command.action==='click'?'prepare-click':(command.action==='fill'?'prepare-fill':command.action));
  const navigationBefore=navigationSequence;
  const nativeInput=command.action==='click'||command.action==='fill';
  if(nativeInput){if(win.isMinimized())win.restore();if(!win.isVisible())win.show();win.focus();supplier.webContents.focus();}
  let preparationTimer;
  const preparation=supplier.webContents.executeJavaScriptInIsolatedWorld(999,[{code:`(${actOnElement.toString()})(${JSON.stringify(command.elementId)},${JSON.stringify(action)},${JSON.stringify(command.value||'')},${JSON.stringify(printStep)})`}]);
  const result=await (nativeInput?Promise.race([preparation,new Promise((_,reject)=>{preparationTimer=setTimeout(()=>reject(Object.assign(new Error('页面渲染准备超时，请重新观察。'),{error:'unsupported_control',status:409})),2500);})]):preparation).finally(()=>clearTimeout(preparationTimer));
  if(result.error)policy.fail(result.error,result.message,result.error==='quote_only'?403:409);
  if(command.action==='click'||command.action==='fill'){
    if(!latestSnapshot||!command.elementId.startsWith(`v${version}-`))policy.fail('stale_element','控件准备期间页面已变化，请重新观察。',409);
    if(!result.point||!Number.isInteger(result.point.x)||!Number.isInteger(result.point.y))policy.fail('unsupported_control','无法定位该控件，请重新观察。');
    win.focus();supplier.webContents.focus();
    const {x,y}=result.point;
    supplier.webContents.sendInputEvent({type:'mouseMove',x,y});
    supplier.webContents.sendInputEvent({type:'mouseDown',x,y,button:'left',clickCount:1});
    supplier.webContents.sendInputEvent({type:'mouseUp',x,y,button:'left',clickCount:1});
    if(command.action==='fill'){
      supplier.webContents.sendInputEvent({type:'keyDown',keyCode:'A',modifiers:['control']});
      supplier.webContents.sendInputEvent({type:'keyUp',keyCode:'A',modifiers:['control']});
      await supplier.webContents.insertText(command.value||'');
      supplier.webContents.sendInputEvent({type:'keyDown',keyCode:'Tab'});
      supplier.webContents.sendInputEvent({type:'keyUp',keyCode:'Tab'});
    }
    let renderTimer;
    try{await Promise.race([supplier.webContents.executeJavaScriptInIsolatedWorld(999,[{code:'new Promise(resolve=>{const timer=setTimeout(resolve,750);requestAnimationFrame(()=>{clearTimeout(timer);resolve();});})'}]),new Promise(resolve=>{renderTimer=setTimeout(resolve,1000);})]);}catch{}finally{clearTimeout(renderTimer);}
  }
  await new Promise(resolve=>setImmediate(resolve));
  if(navigationSequence>navigationBefore&&navigationIssue){dirty();return {...status(),actionOutcome:{status:'blocked',error:'unsupported_destination',navigation:navigationIssue},message:'操作触发了未支持的站点跳转，已阻止；请核对目标地址。'};}
  if(command.action==='upload'){
    const files=policy.checkedFiles(root,command.files);
    if(files.length>1&&!result.multiple)policy.fail('invalid_input','该上传入口每次只接受一个文件。');
    const debug=supplier.webContents.debugger;
    let attached=false;
    try{
      if(!debug.isAttached()){debug.attach('1.3');attached=true;}
      const {root:doc}=await debug.sendCommand('DOM.getDocument',{});
      const {nodeId}=await debug.sendCommand('DOM.querySelector',{nodeId:doc.nodeId,selector:`input[data-formforge-upload="${command.elementId}"]`});
      if(!nodeId)policy.fail('stale_element','上传入口已变化，请重新观察。',409);
      await debug.sendCommand('DOM.setFileInputFiles',{nodeId,files});
    }finally{if(attached&&debug.isAttached())debug.detach();}
    printController.recordUploads(files);dirty();return {...status(),uploadedFiles:files.map(f=>({name:path.basename(f),sha256:crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex')})),message:'文件已交给网页上传控件；供应商处理结果需继续观察。'};
  }
  dirty();return {...status(),actionOutcome:{status:navigationSequence>navigationBefore?'navigation-started':'dispatched',verificationRequired:true},message:'操作已发出，请重新观察供应商实际结果。'};
}
async function command(c,source='agent'){
  if(!c||typeof c.requestId!=='string'||!c.requestId||c.requestId.length>160)policy.fail('invalid_input','每个操作需要 requestId。');
  if(!live())policy.fail('browser_unavailable','请先打开 FormForge 桌面验证版。',503);
  const actions=['open','navigate','inspect','click','fill','select','upload','handoff','resume','back','reload','focus-supplier','split-views','print-prepare','print-submit-unpaid','print-observe'];
  if(!actions.includes(c.action))policy.fail('invalid_input','不支持的桌面浏览器操作。');
  if(c.action==='handoff'){mode='human';dirty();ui();supplier.webContents.focus();return {...status(),message:'已交给用户操作，智能体写操作暂停。'};}
  if(c.action==='resume'){mode='agent';dirty();ui();return {...status(),message:'已恢复智能体控制，请先重新观察。'};}
  if(c.action==='inspect')return inspect();
  if(mode==='human'&&source!=='shell')policy.fail('human_control','用户正在接管，请恢复后再操作。',409);
  if(c.action.startsWith('print-')){loadPrintController();return printController.command(c);}
  if(c.action==='focus-supplier'||c.action==='split-views'){if(win.isMinimized())win.restore();if(!win.isVisible())win.show();win.focus();supplierFullscreen=c.action==='focus-supplier';showSupplier=true;dirty();bounds();ui();return {...status(),message:supplierFullscreen?'供应商页面已全窗显示；工程工作台保持加载。':'已恢复工程工作台与供应商分屏。'};}
  if(['open','navigate'].includes(c.action)){
    const url=c.action==='open'?(testOrigin&&process.env.FORMFORGE_DESKTOP_TEST_PATH?testOrigin+process.env.FORMFORGE_DESKTOP_TEST_PATH:policy.PROVIDERS[c.provider]):c.url;
    recordNavigation(url,'command-'+c.action);
    if(!url||!policy.vendorUrl(url,testOrigin))policy.fail('unsupported_destination','只支持已核对的嘉立创供应商站点。',403);
    if(c.provider){if(!policy.PROVIDERS[c.provider])policy.fail('invalid_input','不支持该供应商。');provider=c.provider;}
    showSupplier=true;bounds();dirty();
    try{await supplier.webContents.loadURL(url);}catch(e){if(e.code!=='ERR_ABORTED')throw e;}
    return inspect();
  }
  if(c.action==='back'){if(returnToSupplierParent())return {...status(),message:'已返回原供应商页面。'};if(supplier.webContents.navigationHistory.canGoBack())supplier.webContents.navigationHistory.goBack();dirty();return status();}
  if(c.action==='reload'){supplier.webContents.reload();dirty();return status();}
  return elementAction(c);
}
function enqueue(c,source='agent'){
  const key=source+':'+c?.requestId;
  const fingerprint=JSON.stringify(c);
  if(results.has(key)){const prior=results.get(key);if(prior.fingerprint!==fingerprint)return Promise.reject(Object.assign(new Error('相同 requestId 不能用于不同操作。'),{error:'request_conflict',status:409}));return prior.pending;}
  const pending=queue.then(()=>command(c,source));queue=pending.catch(()=>{});
  if(c?.requestId){results.set(key,{fingerprint,pending});if(results.size>200)results.delete(results.keys().next().value);}
  return pending;
}
function reply(res,statusCode,data){res.writeHead(statusCode,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));}
async function bridge(){
  server=http.createServer(async(req,res)=>{
    if(req.headers.origin||!['127.0.0.1','::ffff:127.0.0.1'].includes(req.socket.remoteAddress))return reply(res,403,{error:'access_denied',message:'仅允许本机受控连接。'});
    const bearer=(req.headers.authorization||'').replace(/^Bearer /,'');
    if(!/^[a-f0-9]{64}$/.test(bearer)||!crypto.timingSafeEqual(Buffer.from(bearer),Buffer.from(token)))return reply(res,403,{error:'access_denied',message:'需要有效的桌面控制连接。'});
    if(req.method==='GET'&&req.url==='/status')return reply(res,200,status());
    if(testOrigin&&req.method==='POST'&&req.url==='/fixture/minimize'){win.minimize();return reply(res,200,status());}
    if(testOrigin&&req.method==='POST'&&req.url==='/fixture/hide'){win.hide();return reply(res,200,status());}
    if(req.method==='POST'&&req.url==='/shutdown'){reply(res,200,{closing:true,quoteOnly:true});setImmediate(()=>app.quit());return;}
    if(req.method!=='POST'||req.url!=='/command')return reply(res,404,{error:'not_found',message:'未知操作。'});
    let chunks=[],size=0;for await(const chunk of req){size+=chunk.length;if(size>256*1024)return reply(res,413,{error:'invalid_input',message:'请求过大。'});chunks.push(chunk);}
    try{const result=await enqueue(JSON.parse(Buffer.concat(chunks).toString('utf8')));reply(res,200,result);}
    catch(e){fs.appendFileSync(path.join(runtimeRoot,'command-errors.jsonl'),JSON.stringify({at:new Date().toISOString(),name:e.name,error:e.error||'browser_action_failed',message:String(e.message).slice(0,2000),stack:String(e.stack).slice(0,6000)})+'\n');reply(res,e.status||400,{error:e.error||'browser_action_failed',message:e.error?e.message:'页面操作未完成，请观察当前页面或人工接管。'});}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  fs.writeFileSync(path.join(runtimeRoot,'runtime.json'),JSON.stringify({protocol:1,port:server.address().port,token,pid:process.pid,startedAt:new Date().toISOString()}));
}
function loadPrintController(){
  const file=require.resolve('./print-order.cjs'),sha=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  if(printController&&sha===printAdapterSourceSha256)return;
  delete require.cache[file];printAdapterSourceSha256=sha;
  printController=require(file).createPrintController({root,local:runtimeRoot,uploads:printUploads,sourceSha256:sha,getContents:()=>supplier.webContents,status,inspect,elementAction});
}
loadPrintController();
app.whenReady().then(async()=>{
  supplierSession=session.fromPartition(testOrigin?'persist:fixture':'persist:jlc-quotation');
  supplierSession.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
  supplierSession.webRequest.onBeforeRequest((details,callback)=>{
    const cancel=printController.networkBlocked(details);
    if(cancel){lastBlockedRequest={url:policy.safeUrl(details.url),method:details.method,reason:'请求路径命中只报价模式的订单/支付/生产拦截规则；未发送该请求。'};ui();}
    callback({cancel});
  });
  supplierSession.on('will-download',(_event,item)=>{
    const downloads=path.join(runtimeRoot,'downloads');fs.mkdirSync(downloads,{recursive:true});
    item.setSavePath(path.join(downloads,Date.now()+'-'+path.basename(item.getFilename())));
  });
  win=new BrowserWindow({width:1560,height:1000,minWidth:1024,minHeight:650,title:'FormForge · 桌面采购验证版',webPreferences:{preload:path.join(__dirname,'shell-preload.cjs'),contextIsolation:true,sandbox:true,nodeIntegration:false}});
  workbench=new WebContentsView({webPreferences:{contextIsolation:true,sandbox:true,nodeIntegration:false}});
  supplier=new WebContentsView({webPreferences:{session:supplierSession,contextIsolation:true,sandbox:true,nodeIntegration:false,backgroundThrottling:false}});
  win.contentView.addChildView(workbench);win.contentView.addChildView(supplier);
  workbench.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  workbench.webContents.on('will-navigate',(event,url)=>{if(!url.startsWith(workbenchUrl))event.preventDefault();});
  wireSupplier(supplier.webContents);
  ipcMain.handle('desktop:command',async(event,c)=>{if(!trustedSender(event))policy.fail('access_denied','不允许该页面控制浏览器。',403);return enqueue({...c,requestId:c.requestId||crypto.randomUUID()},'shell');});
  ipcMain.handle('desktop:layout',(event,data)=>{if(!trustedSender(event))return;split=Math.max(.25,Math.min(.75,Number(data.split)||.47));showSupplier=data.visible!==false;if(!showSupplier)supplierFullscreen=false;dirty();bounds();ui();return status();});
  ipcMain.handle('desktop:status',event=>trustedSender(event)?status():null);
  win.on('resize',bounds);win.on('closed',()=>{const views=[workbench,...supplierParents,supplier];supplierParents.length=0;win=null;for(const view of views)if(view&&!view.webContents.isDestroyed())view.webContents.close();});
  await win.loadFile(path.join(__dirname,'shell.html'));bounds();
  await workbench.webContents.loadURL(workbenchUrl).catch(()=>{});
  await bridge();ui();
  if(process.env.FORMFORGE_DESKTOP_TEST_ORIGIN)await enqueue({action:'open',provider:'jlc-smt',requestId:'fixture-open'});
}).catch(error=>{fs.appendFileSync(path.join(runtimeRoot,'startup-errors.log'),new Date().toISOString()+' '+error.stack+'\n');app.quit();});
app.on('window-all-closed',()=>app.quit());
app.on('before-quit',event=>{
  if(!quitting&&supplierSession){event.preventDefault();quitting=true;supplierSession.flushStorageData();supplierSession.cookies.flushStore().finally(()=>app.quit());return;}
  server?.close();const file=path.join(runtimeRoot,'runtime.json');try{const data=JSON.parse(fs.readFileSync(file,'utf8'));if(data.pid===process.pid)fs.unlinkSync(file);}catch{}
});
