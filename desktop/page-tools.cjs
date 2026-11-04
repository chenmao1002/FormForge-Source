// These functions run in an isolated renderer world. No supplier script receives a local bridge.
function observePage(version) {
  const compact = x => String(x || '').replace(/\s+/g,' ').trim();
  const privacy = /密码|验证码|口令|收货|地址|电话|手机|邮箱|联系人|姓名|发票|税号|银行|password|passwd|otp|captcha|verification|phone|mobile|email|address|recipient|invoice|tax|secret|token|authorization/i;
  const maskAccount = x => '****'+x.slice(-4);
  const redact = x => compact(x).replace(/\b\d{6,}[A-Z]\b/g,maskAccount).replace(/\b1[3-9]\d{9}\b/g,'[电话已隐藏]').replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/ig,'[邮箱已隐藏]').replace(/\b\d{15,19}\b/g,'[编号已隐藏]');
  const publicUrl = value => {try{const u=new URL(value,document.baseURI);return ['http:','https:'].includes(u.protocol)?u.origin+u.pathname+(u.hash&&!/[?&=]/.test(u.hash)?u.hash:''):'';}catch{return '';}};
  const visible = el => { const r=el.getBoundingClientRect(); const s=(el.ownerDocument?.defaultView||globalThis).getComputedStyle?.(el)||getComputedStyle(el); return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
  const documents=[{doc:document,frame:null}];
  for(const frame of document.querySelectorAll('iframe')){
    if(!visible(frame))continue;
    try{if(new URL(frame.src,document.baseURI).origin===new URL(document.baseURI).origin&&frame.contentDocument?.body)documents.push({doc:frame.contentDocument,frame});}catch{}
  }
  const selector='button,input,select,textarea,a,label,[role="button"],[role="radio"],[role="checkbox"],[role="option"],[role="combobox"],[tabindex],[onclick],.el-radio,.el-checkbox,.el-select-dropdown__item,.ant-select-item';
  const nodes=[];
  for(const scope of documents){
    const semantic=[...scope.doc.querySelectorAll(selector)];
    const pointer=[...scope.doc.querySelectorAll('body *')].filter(el=>{
      const label=compact(el.innerText);if(!label||label.length>80||!visible(el)||[...el.children].some(child=>compact(child.innerText)))return false;
      if(el.closest(selector))return false;
      return (el.ownerDocument?.defaultView||globalThis).getComputedStyle?.(el)?.cursor==='pointer';
    });
    for(const el of [...semantic,...pointer])if(visible(el)||(el.tagName==='INPUT'&&el.type==='file'))nodes.push({el,frame:scope.frame});
  }
  const rowText=el=>{
    let row=el.closest('tr,[role="row"],.el-table__row,.ant-table-row');
    if(!row){let parent=el.parentElement;for(let depth=0;parent&&depth<4;depth++,parent=parent.parentElement){const value=compact(parent.innerText);if(value.length<=1600&&/\bC\d{3,}\b/.test(value)){row=parent;break;}}}
    const value=compact(row?.innerText);return value&&!privacy.test(value)?redact(value).slice(0,1600):'';
  };
  const items = [];
  globalThis.__ffDesktopElements = new Map();
  globalThis.__ffDesktopVersion = version;
  for (const {el,frame} of nodes.slice(0,600)) {
    const type = compact(el.getAttribute('type'));
    const label = redact(el.getAttribute('aria-label') || el.getAttribute('title') || [...(el.labels||[])].map(x=>x.innerText).join(' ') || el.innerText || el.getAttribute('placeholder') || el.getAttribute('name') || (type==='file'?'上传文件':''));
    const context = [label,el.name,el.id,el.autocomplete,type].join(' ');
    if (type==='password' || privacy.test(context)) continue;
    const id = `v${version}-e${items.length}`;
    globalThis.__ffDesktopElements.set(id,{el,frame});
    let value = '';
    if (el.tagName==='SELECT') value=redact(el.selectedOptions?.[0]?.textContent);
    else if (type==='file') value=[...(el.files||[])].map(f=>f.name).join(', ');
    else if (['number','radio','checkbox'].includes(type) || /数量|长|宽|高|厚|尺寸|件数|片数|quantity|width|height|length/i.test(context)) value=redact(el.value).slice(0,100);
    items.push({id,tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||'',label:label.slice(0,350),type,value,disabled:!!el.disabled,checked:!!el.checked,rowContext:rowText(el),...(frame?{frameSrc:publicUrl(frame.src)}:{}),...(el.tagName==='A'?{href:publicUrl(el.href||el.getAttribute('href'))}:{}),...(el.tagName==='SELECT'?{options:[...el.options].map(o=>({value:o.value,label:redact(o.textContent)}))}:{})});
  }
  const bodyLines = documents.flatMap(scope=>(scope.doc.body?.innerText||'').split('\n')).map(compact).filter(Boolean);
  const relevant = bodyLines.filter(x => /报价|计价|价格|金额|费用|合计|总计|元|￥|¥|RMB|CNY|上传|登录|数量|材质|材料|工艺|尺寸|长宽|交期|购物车|匹配|贴片|插件|库存|文件|打印|模型|缺料|失败|错误|等待|成功|PCB|BOM|SMT|CPL|STL|STEP|Gerber/i.test(x) && !privacy.test(x));
  const text = relevant.map(redact).join('\n').slice(0,18000);
  const needsLogin = bodyLines.some(x=>/请登录|登录后|手机号登录|账号登录|扫码登录/.test(x)) || items.some(x=>/^(登录|立即登录|登录\/注册|注册\/登录)$/.test(x.label));
  const visibleAccount = bodyLines.find(x=>/^\d{6,}[A-Z]$/.test(x));
  const accountLabel = visibleAccount ? maskAccount(visibleAccount) : null;
  const loginStatus = needsLogin ? 'needs-login' : (accountLabel ? 'authenticated' : 'unknown');
  const quoteCandidates = relevant.filter(x=>/[￥¥]\s*\d|\d[\d,.]*\s*元/.test(x)).map(redact).slice(0,50);
  const frames=[...document.querySelectorAll('iframe,frame')].filter(visible).map(el=>{
    let src='';try{const u=new URL(el.src,document.baseURI);if(['http:','https:'].includes(u.protocol))src=u.origin+u.pathname;}catch{}
    return {src,title:redact(el.getAttribute('title')||'').slice(0,200)};
  }).slice(0,30);
  const tableRows=[];
  for(const scope of documents)for(const row of scope.doc.querySelectorAll('tr,[role="row"],.el-table__row,.ant-table-row')){
    const value=compact(row.innerText);if(!visible(row)||!value||privacy.test(value)||tableRows.length>=120)continue;
    tableRows.push({text:redact(value).slice(0,1600),cells:[...row.querySelectorAll('th,td,[role="cell"],[role="columnheader"]')].map(cell=>redact(cell.innerText).slice(0,250)).slice(0,40),...(scope.frame?{frameSrc:publicUrl(scope.frame.src)}:{})});
  }
  return {title:document.title,elements:items,text,loginStatus,accountLabel,quoteCandidates,frames,tableRows};
}

function actOnElement(id, action, value, printStep) {
  const entry=globalThis.__ffDesktopElements?.get(id);
  const el=entry?.el||entry,frame=entry?.frame||null;
  if (!el || !el.isConnected || !id.startsWith(`v${globalThis.__ffDesktopVersion}-`)) return {error:'stale_element',message:'页面已变化，请先重新观察。'};
  const owner=el.ownerDocument||document;
  const frameCurrent=()=>{if(!frame)return true;try{return frame.isConnected&&new URL(frame.src,document.baseURI).origin===new URL(document.baseURI).origin&&frame.contentDocument===owner;}catch{return false;}};
  if(!frameCurrent())return {error:'stale_element',message:'同源内嵌页面已变化，请重新观察。'};
  const text=[el.innerText,el.getAttribute('aria-label'),el.getAttribute('title'),el.name,el.id,el.getAttribute('placeholder')].join(' ');
  if (/密码|验证码|口令|收货|地址|电话|手机|邮箱|联系人|姓名|发票|税号|银行|password|otp|captcha|phone|mobile|email|address|recipient|invoice|secret|token/i.test(text) || el.type==='password') return {error:'unsupported_control',message:'登录和个人资料由用户接管填写。'};
  const filling=action==='fill'||action==='prepare-fill';
  const noteContext=text+' '+(el.closest('.ant-form-item,.el-form-item,.form-item')?.querySelector('label')?.innerText||'');
  if(filling&&/备注|留言|remark|memo/i.test(noteContext))return {error:'unsupported_control',message:'不填写或修改供应商备注；平台既有内容保持原样。'};
  if (el.disabled || (filling&&el.readOnly) || ['password','email','tel'].includes(el.type)) return {error:'unsupported_control',message:'该控件不可编辑，或需要用户接管。'};
  const blocked=/提交.{0,8}订单|确认.{0,8}下单|立即下单|确认.{0,8}订单|确认.{0,8}支付|立即支付|去支付|确认付款|立即付款|自动扣款|放行生产|确认生产|submit\s*order|place\s*order|pay\s*now|checkout|release\s*production/i;
  const dialog=el.closest('[role="dialog"],.el-dialog,.ant-modal,.modal');
  const dialogTitle=dialog?.querySelector('[role="heading"],.el-dialog__title,.ant-modal-title,.modal-title,h1,h2,h3,h4')?.textContent||'';
  const formContext=el.form?[el.form.getAttribute('action'),el.form.getAttribute('aria-label'),el.form.querySelector('legend,h1,h2,h3,h4')?.textContent].join(' '):'';
  const genericConfirm=/^\s*(确定|确认|提交|继续|下一步|OK|Confirm|Submit)\s*$/i.test((el.innerText||el.value||'').trim());
  const label=(el.innerText||'').trim();
  const printCommit=!!printStep&&!frame&&action==='prepare-click'&&location.hostname==='www.jlc-3dp.cn'&&(printStep==='checkout'&&location.pathname==='/placeOrder'&&label==='提交订单'||printStep==='submit'&&location.pathname==='/submitOrder'&&label==='立即下单'||printStep==='manual'&&location.pathname==='/submitOrder'&&label==='人工确认订单');
  const dialogText=(dialog?.innerText||'').slice(0,6000);
  // JLC's material picker says "仅支持样品订单" in a material description.
  // Saving this explicit parameter dialog only updates the pending quote.
  const printParameters=/^\s*编辑打印参数\s*$/.test(dialogTitle)&&/选择材料[：:]/.test(dialogText)&&/数量[：:]/.test(dialogText)&&!blocked.test(dialogText);
  if (!printCommit&&(blocked.test(text) || blocked.test(formContext) || (genericConfirm && !printParameters && /订单|下单|支付|付款|扣款|生产|order|payment|checkout/i.test(dialogTitle+' '+formContext+' '+dialogText)))) return {error:'quote_only',message:'本验证版只允许报价，不提交订单或付款。'};
  if (genericConfirm && !printParameters && !/报价|计价|询价|匹配|上传|quote|price|calculate/i.test(dialogTitle+' '+formContext+' '+dialogText)) return {error:'unsupported_control',message:'该确认控件的报价用途不明确，请人工接管。'};
  if (action==='click' || action==='prepare-click' || filling) {
    if (filling && (!['INPUT','TEXTAREA'].includes(el.tagName) || ['file','hidden','submit','button','checkbox','radio','password'].includes(el.type))) return {error:'unsupported_control',message:'该控件不能填写。'};
    if (el.type==='file') return {error:'unsupported_control',message:'请使用上传动作选择已核对的制造文件。'};
    if (!String(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || [...(el.labels||[])].map(x=>x.innerText).join(' ') || el.getAttribute('placeholder') || el.value || el.getAttribute('href') || '').trim() && !['checkbox','radio'].includes(el.type)) return {error:'unsupported_control',message:'未识别控件用途，请人工接管。'};
    const preparedType=el.type;
    return (async()=>{
      if(frame)frame.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
      el.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
      // A renderer can withhold rAF while occluded; the fresh geometry and hit checks below remain mandatory.
      await new Promise(resolve=>{let settled=false;const finish=()=>{if(settled)return;settled=true;clearTimeout(timer);resolve();};const timer=setTimeout(finish,1200);requestAnimationFrame(()=>requestAnimationFrame(finish));});
      if(!el.isConnected||!frameCurrent())return {error:'stale_element',message:'控件或同源内嵌页面已变化，请重新观察。'};
      const currentText=[el.innerText,el.getAttribute('aria-label'),el.getAttribute('title'),el.name,el.id,el.getAttribute('placeholder')].join(' ');
      const currentDialog=el.closest('[role="dialog"],.el-dialog,.ant-modal,.modal');
      const currentFormContext=el.form?[el.form.getAttribute('action'),el.form.getAttribute('aria-label'),el.form.querySelector('legend,h1,h2,h3,h4')?.textContent].join(' '):'';
      if(el.disabled||(filling&&el.readOnly)||el.type!==preparedType||currentText!==text||currentDialog!==dialog||(currentDialog?.innerText||'').slice(0,6000)!==dialogText||currentFormContext!==formContext)return {error:'stale_element',message:'控件或确认上下文已变化，请重新观察。'};
      const r=el.getBoundingClientRect();
      const ownerWindow=owner.defaultView||globalThis;
      const left=Math.max(0,r.left),right=Math.min(ownerWindow.innerWidth??innerWidth,r.right),top=Math.max(0,r.top),bottom=Math.min(ownerWindow.innerHeight??innerHeight,r.bottom);
      if(right-left<1||bottom-top<1)return {error:'unsupported_control',message:'控件不在可操作区域，请重新观察或人工接管。'};
      let x=Math.floor((left+right)/2),y=Math.floor((top+bottom)/2);
      const hit=owner.elementFromPoint(x,y);
      // Styled radios/checkboxes commonly put the visible indicator over the
      // native input. A hit inside its own associated label still activates
      // this exact control; unrelated overlays remain rejected.
      const ownLabelHit=['checkbox','radio'].includes(el.type)&&[...(el.labels||[])].some(label=>label.control===el&&(hit===label||label.contains(hit)));
      if(!hit||(hit!==el&&!el.contains(hit)&&!ownLabelHit))return {error:'unsupported_control',message:'控件被其他内容遮挡，请重新观察或人工接管。'};
      if(frame){
        const rect=frame.getBoundingClientRect(),scaleX=frame.offsetWidth?rect.width/frame.offsetWidth:1,scaleY=frame.offsetHeight?rect.height/frame.offsetHeight:1;
        x=Math.floor(rect.left+(frame.clientLeft+x)*scaleX);y=Math.floor(rect.top+(frame.clientTop+y)*scaleY);
        if(x<0||y<0||x>=innerWidth||y>=innerHeight||document.elementFromPoint(x,y)!==frame)return {error:'unsupported_control',message:'内嵌控件被外层页面遮挡或超出可操作区域。'};
      }
      return {ok:true,point:{x,y}};
    })();
  } else if (action==='select') {
    if (el.tagName!=='SELECT' || ![...el.options].some(o=>o.value===value)) return {error:'unsupported_control',message:'该选项不存在。'};
    el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
  } else if (action==='mark-upload') {
    if(frame)return {error:'unsupported_control',message:'同源内嵌页面上传暂不支持，请使用顶层上传入口。'};
    if (el.tagName!=='INPUT' || el.type!=='file') return {error:'unsupported_control',message:'该控件不是文件上传入口。'};
    el.setAttribute('data-formforge-upload',id);return {ok:true,multiple:!!el.multiple};
  }
  return {ok:true};
}
module.exports = { observePage, actOnElement };
