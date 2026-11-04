import {api,fileUrl,type Context,type Revision} from './api';
import {SchematicReadability} from './schematic-readability';
import {SchematicCandidates} from './schematic-candidates';

const el=(id:string)=>document.getElementById(id)!;
const node=(tag:string,cls='',text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
export class SchematicWorkspace {
  private key='';
  private tab='preview';
  private readability:SchematicReadability;
  private candidates:SchematicCandidates;
  private context:Context|null=null;
  private cache=new Map<string,Promise<{components:any[];total:number}>>();
  constructor(){
    this.readability=new SchematicReadability(el('schematic-check-card'));
    this.candidates=new SchematicCandidates(el('schematic-candidates'));
    const buttons=[...document.querySelectorAll<HTMLButtonElement>('button[data-schematic-tab]')];
    buttons.forEach((button,index)=>{
      button.onclick=()=>this.select(button.dataset.schematicTab!);
      button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[next].click();buttons[next].focus();};
    });
    el('drawing-expand').onclick=()=>this.expand(!el('viewer-panel').classList.contains('drawing-expanded'));
    document.addEventListener('keydown',e=>{if(e.key==='Escape')this.expand(false);});
    document.addEventListener('drawing-scene-ready',event=>{const scene=(event as CustomEvent).detail;if(scene.kind==='schematic'&&this.key===scene.projectId+':'+scene.revisionId){el('schematic-pages').textContent=String(scene.sheetInstances?.length||scene.sheets.length);void this.readability.show(scene);}});
    document.addEventListener('schematic-pin-focus',()=>{if(document.body.dataset.view==='schematic'){this.select('preview');el('viewer-panel').scrollIntoView({block:'nearest'});}});
    this.select('preview');
  }
  private expand(expanded:boolean){el('viewer-panel').classList.toggle('drawing-expanded',expanded);el('drawing-expand').setAttribute('aria-pressed',String(expanded));el('drawing-expand').textContent=expanded?'退出大图':'展开图纸';}
  private select(tab:string){
    this.tab=tab;document.body.dataset.schematicTab=tab;
    this.candidates.render(tab==='layout'?this.context:null);
    (el('revision-evidence') as HTMLDetailsElement).open=tab!=='preview';
    document.querySelectorAll<HTMLButtonElement>('button[data-schematic-tab]').forEach(b=>{const selected=b.dataset.schematicTab===tab;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;});
    for(const id of tab==='checks'?['engineering-review','connection-details']:tab==='libraries'?['library-details']:tab==='history'?['design-specification']:[])(el(id) as HTMLDetailsElement).open=true;
    if(tab==='history')(document.querySelector('.revision-detail-disclosure') as HTMLDetailsElement).open=true;
  }
  render(view:string,context:Context|null,revision?:Revision){
    this.context=context;
    const active=view==='schematic';document.body.classList.toggle('schematic-view',active);
    el('view-title').translate=!active;if(active)el('view-title').textContent=context?.project.name||'原理图';
    el('schematic-navigation').hidden=!active;el('schematic-overview').hidden=!active;el('schematic-check-card').hidden=!active;el('schematic-extra').hidden=!active;
    el('drawing-expand').hidden=!active;
    if(!active){this.key='';this.readability.clear();this.candidates.render(null);this.expand(false);return;}
    const key=[context?.project.id,revision?.id].join(':');
    if(key!==this.key){this.readability.clear();this.key=key;this.select('preview');el('schematic-symbols').textContent='—';el('schematic-nets').textContent='—';el('schematic-pages').textContent='—';}
    this.candidates.render(this.tab==='layout'?context:null);
    const files=revision?.manifest?.filter(f=>f.path.endsWith('.kicad_sch'))||[];
    el('schematic-file').textContent=revision?.entrypoints.schematic||'尚无原理图';
    const file=files.find(f=>f.path===revision?.entrypoints.schematic);
    el('schematic-file-meta').textContent=file?`${(file.size/1024).toFixed(1)} KB · ${revision!.createdAt.slice(0,10)}`:'图纸与报告绑定所选版本';
    el('schematic-files').textContent=revision?.manifest?String(files.length):'—';
    el('schematic-pages').textContent=files.length?String(files.length):'—';
    const artifacts=context?.artifacts.filter(a=>a.revisionId===revision?.id)||[];
    const checks=context?.jobs.find(j=>j.result?.checks?.some((c:any)=>c.kind==='erc')&&(j.result?.revisionId||j.inputRevision)===revision?.id)?.result?.checks;
    const erc=checks?.find((c:any)=>c.kind==='erc');
    const report=artifacts.find(a=>a.name==='erc.json'&&a.kind==='report');
    const status=el('schematic-erc-status');status.textContent=erc?(erc.status==='passed'?'ERC 通过':'ERC 需处理'):'暂无同版 ERC 结果';status.className='badge '+(erc?(erc.status==='passed'?'success':'warning'):'neutral');
    el('schematic-errors').textContent=erc?String(erc.errors):'—';el('schematic-warnings').textContent=erc?String(erc.warnings):'—';
    el('schematic-check-version').textContent=revision?'修订 '+revision.id.slice(-8):'尚无输入版本';
    const reportLink=el('schematic-erc-report') as HTMLAnchorElement;reportLink.hidden=!report;if(report)reportLink.href=fileUrl(report)+'?download=true';
    const findings=el('schematic-check-items');findings.replaceChildren();
    for(const item of (erc?.items||[]).slice(0,3)){const p=node('p','field-note',item.description||item.type);p.translate=false;findings.append(p);}
    const extra=el('schematic-extra');extra.replaceChildren();
    const componentList=node('div','schematic-component-list');componentList.append(node('h2','','器件位号'),node('p','field-note','尚无同版原生网表'));extra.append(componentList);
    const libraryEmpty=node('p','schematic-library-empty field-note','本版没有登记外部符号库。图纸内嵌符号随原生工程保存。');extra.append(libraryEmpty);
    libraryEmpty.hidden=!!revision?.symbolLibraries?.length;
    const fileList=node('div','schematic-file-list');
    for(const a of artifacts.filter(a=>['schematic_pdf','schematic_preview','bom','netlist','baseline_netlist'].includes(a.kind))){const link=node('a','schematic-file-link') as HTMLAnchorElement;link.href=fileUrl(a)+'?download=true';const label=node('span','',a.name);label.translate=false;link.append(label,node('small','',`${(a.size/1024).toFixed(1)} KB`),node('span','','↓'));fileList.append(link);}
    if(!fileList.children.length)fileList.append(node('p','field-note','此版本尚无可下载文件。'));extra.append(fileList);
    const history=node('div','schematic-history-list');
    for(const r of context?.revisions.filter(r=>(r.module||'eda')==='eda')||[]){const b=node('button','schematic-history-item'+(r.id===revision?.id?' selected':'')) as HTMLButtonElement;b.append(node('strong','','修订 '+r.id.slice(-8)),node('span','',r.createdAt.slice(0,16).replace('T',' ')),node('span','small-tag',r.id===context?.project.headRevisionId?'当前版本':r.validation?.status==='pending'?'检查中':r.validation?.status==='ready'?'待审阅':r.validation?.adopted===false?'未采纳':'历史版本'));b.onclick=()=>{const select=el('preview-revision') as HTMLSelectElement;select.value=r.id;select.dispatchEvent(new Event('change'));};history.append(b);}
    extra.append(history);
    if(!history.children.length)history.append(node('p','field-note','尚无输入版本'));
    const netlist=artifacts.find(a=>['netlist','baseline_netlist'].includes(a.kind));
    el('schematic-net-note').textContent=netlist?'连接分组含独立引脚':'尚无同版原生网表';
    if(context&&revision&&netlist){
      const cacheKey=key+':'+netlist.sha256;
      let pending=this.cache.get(cacheKey);
      if(!pending){pending=api(`projects/${context.project.id}/schematic-connectivity?revisionId=${encodeURIComponent(revision.id)}&limit=1`);this.cache.set(cacheKey,pending);if(this.cache.size>24)this.cache.delete(this.cache.keys().next().value!);}
      void pending.then(graph=>{if(this.key!==key)return;el('schematic-symbols').textContent=String(graph.components.length);el('schematic-nets').textContent=String(graph.total);componentList.replaceChildren(node('h2','','器件位号'));const refs=node('div','schematic-component-refs');for(const component of graph.components){const ref=node('span','small-tag',component.reference);ref.translate=false;refs.append(ref);}componentList.append(refs);}).catch(()=>{this.cache.delete(cacheKey);if(this.key===key)el('schematic-net-note').textContent='网表统计读取失败，可切换页面重试';});
    }
  }
}
