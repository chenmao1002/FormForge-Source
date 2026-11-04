const bridge=window.formforgeDesktop;
let visible=true,supplierFullscreen=false;
const message=document.getElementById('message');
async function execute(action,extra={}){try{const s=await bridge.command({action,...extra});render(s);if(s.message)message.textContent=s.message;}catch(e){message.textContent=e.message.replace(/^Error invoking remote method.*?: Error: /,'');}}
function render(s){supplierFullscreen=!!s.supplierFullscreen;visible=s.viewMode!=='workbench';document.getElementById('focus').textContent=supplierFullscreen?'恢复分屏':'供应商全窗';document.getElementById('split').disabled=supplierFullscreen;document.getElementById('toggle').textContent=visible?'收起供应商':'展开供应商';document.getElementById('mode').textContent=s.mode==='human'?'您正在接管 · 智能体操作暂停':s.printOrderingEnabled?'智能体控制 · 报价及3D待付款':'智能体控制 · 仅报价';document.getElementById('url').textContent=s.url||'';document.getElementById('handoff').disabled=s.mode==='human';document.getElementById('resume').disabled=s.mode!=='human';if(s.navigationIssue)message.textContent='已阻止未支持的跳转：'+s.navigationIssue.targetUrl;}
document.getElementById('open').onclick=()=>execute('open',{provider:document.getElementById('provider').value});
for(const action of ['back','reload','handoff','resume'])document.getElementById(action).onclick=()=>execute(action);
document.getElementById('focus').onclick=()=>execute(supplierFullscreen?'split-views':'focus-supplier');
document.getElementById('split').oninput=e=>bridge.layout({split:Number(e.target.value)/100,visible});
document.getElementById('toggle').onclick=()=>{visible=!visible;bridge.layout({visible,split:Number(document.getElementById('split').value)/100});document.getElementById('toggle').textContent=visible?'收起供应商':'展开供应商';};
bridge.onStatus(render);bridge.status().then(render);
