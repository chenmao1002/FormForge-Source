// Serialized into the visible supplier frame. Only rendered DOM is inspected.
function inspectDom(){
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';};
  const privateField=e=>/password|验证码|密码|手机|电话|邮箱|姓名|地址|captcha|phone|email|address|otp|invoice|company|tax|contact|receiver|recipient|token|secret/i.test([e.type,e.name,e.id,e.placeholder].join(' '));
  const chunks=[];
  const privateHeading=/^(?:需要发票|发票信息|开票资料|发票抬头|收货地址|联系方式|联系信息|联系人|联系地址|公司名称|统一社会信用代码|纳税人识别号|税号)(?:\s|[：:]|$)/;
  const publicHeading=/^(?:确认订单方式|发货\/快递|选择快递|收据\/送货单|快递方式|仿真图|交期&快递|价格明细|参数检查|基础信息|基本信息|PCB工艺|SMT贴片\/激光钢网|交期|应付总额|应付金额|总计|检查订单|提交订单|确认并提交|立即下单)(?:\s|[：:]|$)/;
  function sections(value,remember=false){
    const output=[];let hidden=[];
    const flush=()=>{if(hidden.length){if(remember)chunks.push(hidden.join('\n'));hidden=[];}};
    for(const line of String(value||'').split(/\r?\n/)){
      const text=line.trim();
      if(privateHeading.test(text)){flush();output.push('[收货或开票资料已隐藏]');hidden.push(line);}
      else if(hidden.length&&!publicHeading.test(text))hidden.push(line);
      else{flush();output.push(line);}
    }
    flush();return output.join('\n');
  }
  let raw=document.body?.innerText||'';
  for(const e of document.querySelectorAll('[class*="address" i],[class*="receive" i],[class*="invoice" i],[class*="contact" i]')){
    const text=e.innerText?.trim();if(text&&text.length>=4&&text.length<5000){chunks.push(text);raw=raw.replaceAll(text,'[收货或开票资料已隐藏]');}
  }
  const body=sections(raw,true);
  const clean=value=>{
    let text=String(value||'').trim();
    if(text.length>=2&&chunks.some(chunk=>chunk.includes(text)))return '[个人资料已隐藏]';
    text=sections(text);
    for(const chunk of chunks)if(chunk.length>=4)text=text.replaceAll(chunk,'[个人资料已隐藏]');
    return text.replace(/\b1[3-9]\d{9}\b/g,'[电话]').replace(/\b\d{5,}[A-Z]\b/g,'[账号]').replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g,'[邮箱]').trim();
  };
  const inputs=[...document.querySelectorAll('input,select,textarea')].filter(e=>visible(e)&&!privateField(e)).map(e=>{
    const safeNumeric=/pcbLengthInput|pcbWidthInput|numInput|quantity|数量|宽度|长度/i.test([e.id,e.name,e.placeholder].join(' '))&&/^\d+(?:\.\d+)?$/.test(e.value||'');
    const copy=e.cloneNode(false);for(const a of [...copy.attributes])if(!['id','type','name','placeholder','role','aria-label'].includes(a.name))copy.removeAttribute(a.name);
    return {tag:e.tagName,type:e.type,id:e.id,placeholder:clean(e.placeholder),name:e.name,
      value:e.type==='file'?[...e.files].map(f=>f.name):safeNumeric?e.value:'[表单值已隐藏]',checked:e.checked,outer:copy.outerHTML.slice(0,400)};
  });
  return {text:clean(body).slice(0,22000),inputs,
    uploads:[...document.querySelectorAll('input[type=file]')].map(e=>({files:[...e.files].map(f=>({name:f.name,size:f.size})),area:clean(e.parentElement?.parentElement?.innerText).slice(0,600)})),
    alerts:[...document.querySelectorAll('.ant-form-explain,.ant-message-notice,.ant-alert-message,[role=alert]')].filter(visible).map(e=>clean(e.innerText)),
    controls:[...document.querySelectorAll('button,[role=button],label,a')].filter(visible).map(e=>({tag:e.tagName,text:clean(e.innerText).slice(0,120)})).filter(e=>e.text),
    rows:[...document.querySelectorAll('tr')].filter(visible).map(e=>clean(e.innerText))};
}
module.exports={inspectDom};
