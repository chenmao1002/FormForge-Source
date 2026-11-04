/** Version-local inheritance evidence; never infer it from a newer library. */
export function symbolResolutionDetails(change:any):HTMLElement|undefined{
  const resolution=change.symbolResolution;
  if(!resolution||!Array.isArray(resolution.chain))return;
  const detail=document.createElement('details');detail.className='source-details';
  const add=(tag:string,text:string)=>{const e=document.createElement(tag);e.textContent=text;detail.append(e);return e;};
  add('summary',`${change.reference} · 查看派生符号来源`);
  add('p','原库保持冻结。按以下顺序解析同库属性继承；图形和引脚来自基础符号。来源与许可仍见本版冻结库记录。');
  for(const item of resolution.chain){
    add('p',item.name);
    add('code','原定义 SHA-256：'+item.sha256).className='library-digest';
  }
  add('code','展开定义 SHA-256：'+resolution.resolvedSha256).className='library-digest';
  add('code','图页内嵌定义 SHA-256：'+resolution.embeddedDefinitionSha256).className='library-digest';
  return detail;
}
