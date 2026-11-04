import {api,fileUrl,type Artifact} from './api';

export interface Primitive {tag:string;attrs:Record<string,string|number>;text?:string}
export interface DrawObject {id:string;layer:string;transform?:string;role?:string;primitives:Primitive[]}
export interface DrawingScene {schemaVersion:number;projectId:string;revisionId:string;kind:'schematic'|'pcb';sheet:string;sheets:string[];instancePath?:string;sheetInstances?:{sheet:string;instancePath:string;label:string}[];sceneHash:string;sourceSha256:string;units:string;bounds:number[];layers:string[];pcbSummary?:{copperLayers:string[];componentCount:number;padCount:number;viaCount:number;trackCount:number;minTrackWidthMm:number|null};validation:{status:string};coverage:{omitted:{type:string;reason:string}[]};mode:'snapshot'|'delta';objects?:DrawObject[];upsert?:DrawObject[];remove?:string[];order?:string[];baseSceneHash?:string}

export function sheetChoices(scene:DrawingScene){return scene.sheetInstances?.map(s=>({...s,value:JSON.stringify([s.sheet,s.instancePath])}))||scene.sheets.map(sheet=>({sheet,instancePath:undefined,label:sheet.replace(/\.kicad_sch$/,''),value:sheet}));}
export function sceneSelection(scene:DrawingScene){return scene.instancePath?JSON.stringify([scene.sheet,scene.instancePath]):scene.sheet;}
export function parseSheetChoice(choice:string){if(choice.startsWith('["')){const [sheet,instancePath]=JSON.parse(choice) as [string,string];return {sheet,instancePath};}return {sheet:choice,instancePath:undefined};}
type DrawingTarget={projectId:string;revisionId:string;kind:'schematic'|'pcb';status:string;nativePreviews?:Artifact[]};

/** Native plots are bound to the selected revision and hierarchical instance. */
export function nativeSheetPreview(scene:DrawingScene,artifacts:Artifact[]){
  const root=scene.sheetInstances?.[0];
  const selected=scene.sheetInstances?.find(s=>s.instancePath===scene.instancePath&&s.sheet===scene.sheet);
  const stem=(root?.sheet||scene.sheets[0]||scene.sheet).split('/').pop()!.replace(/\.kicad_sch$/,'');
  if(scene.sheetInstances?.length&&!selected)return undefined;
  const name=stem+(selected&&selected!==root?'-'+selected.label.split(' / ').join('-'):'')+'.svg';
  const matches=artifacts.filter(a=>a.projectId===scene.projectId&&a.revisionId===scene.revisionId&&a.kind==='schematic_preview'&&a.name===name);
  return matches.sort((a,b)=>b.createdAt.localeCompare(a.createdAt))[0];
}
type PageView={root:SVGGElement;nodes:Map<string,SVGGElement>;viewbox:number[]};

/** State changes are atomic: reject a delta for another document/base. */
export class DrawingState {
  scene?:DrawingScene;
  objects=new Map<string,DrawObject>();
  apply(scene:DrawingScene){
    if(scene.schemaVersion!==1||scene.units!=='mm')throw new Error('不支持此绘图格式');
    const old=this.scene;
    if(scene.mode==='delta'&&(!old||scene.baseSceneHash!==old.sceneHash||old.projectId!==scene.projectId||old.kind!==scene.kind||old.sheet!==scene.sheet||old.instancePath!==scene.instancePath))throw new Error('绘图增量需要重新读取完整快照');
    const objects=scene.mode==='snapshot'?new Map<string,DrawObject>():new Map(this.objects);
    for(const id of scene.remove||[])objects.delete(id);
    for(const item of scene.objects||scene.upsert||[])objects.set(item.id,item);
    if(scene.order){const ordered=new Map<string,DrawObject>();for(const id of scene.order){const item=objects.get(id);if(!item)throw new Error('绘图对象不完整');ordered.set(id,item);}if(ordered.size!==objects.size)throw new Error('绘图对象顺序不完整');this.objects=ordered;}else this.objects=objects;
    this.scene=scene;
  }
}

const ns='http://www.w3.org/2000/svg';
const tags=new Set(['line','path','rect','circle','ellipse','text']);
const attributes=new Set(['x','y','x1','y1','x2','y2','cx','cy','r','rx','ry','width','height','d','stroke','stroke-width','stroke-linecap','stroke-linejoin','stroke-dasharray','fill','fill-rule','opacity','font-size','font-family','text-anchor','dominant-baseline','transform']);
const escapeMarkup=(value:string|number)=>String(value).replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]!));
function primitive(item:Primitive){
  if(!tags.has(item.tag))throw new Error('不支持的绘图图元');
  const element=document.createElementNS(ns,item.tag);
  for(const [name,value]of Object.entries(item.attrs)){if(!attributes.has(name))throw new Error('不支持的绘图属性');element.setAttribute(name,String(value));}
  if(item.text!==undefined)element.textContent=item.text;
  return element;
}
function primitiveMarkup(item:Primitive){
  if(!tags.has(item.tag))throw new Error('不支持的绘图图元');
  const attrs=Object.entries(item.attrs).map(([name,value])=>{if(!attributes.has(name))throw new Error('不支持的绘图属性');return ` ${name}="${escapeMarkup(value)}"`;}).join('');
  return `<${item.tag}${attrs}>${item.text===undefined?'':escapeMarkup(item.text)}</${item.tag}>`;
}
function objectMarkup(item:DrawObject){return `<g${item.transform?` transform="${escapeMarkup(item.transform)}"`:''}>${item.primitives.map(primitiveMarkup).join('')}</g>`;}

export function paintOrder(scene:DrawingScene,objects:DrawObject[]){
  if(scene.kind!=='pcb')return objects;
  const copper=scene.pcbSummary?.copperLayers||scene.layers.filter(l=>l.endsWith('.Cu')).sort((a,b)=>copperRank(a)-copperRank(b));
  const rank=(layer:string)=>layer==='Edge.Cuts'?1002:layer==='F.SilkS'?1001:layer==='B.SilkS'?-1:copper.includes(layer)?copper.length-1-copper.indexOf(layer):1000;
  return [...objects].sort((a,b)=>rank(a.layer)-rank(b.layer));
}
const copperRank=(name:string)=>name==='F.Cu'?0:name==='B.Cu'?1000:Number(name.match(/^In(\d+)\.Cu$/)?.[1]||500);

export class LiveDrawing {
  readonly element=document.createElement('div');
  readonly state=new DrawingState();
  private svg=document.createElementNS(ns,'svg');
  private root=document.createElementNS(ns,'g');
  private message=document.createElement('span');
  private sheets=document.createElement('select');
  private layers=document.createElement('div');
  private selectedLayer='F.Cu';
  private copperLayers:string[]=[];
  private sheetRail=document.createElement('nav');
  private pageLabel=document.createElement('span');
  private thumbnails=new Map<string,SVGSVGElement>();
  private thumbnailController?:AbortController;
  private scenes=new Map<string,DrawingScene>();
  private pageViews=new Map<string,PageView>();
  private nodes=new Map<string,SVGGElement>();
  private ticket=0;
  private displayedScope='';
  private requestKey='';
  private completedKey='';
  private viewbox:number[]=[];
  private target?:DrawingTarget;
  private pending=new Map<string,Promise<boolean>>();
  private controller?:AbortController;
  private sheetTimer?:number;
  private drag?:{x:number;y:number;box:number[]};
  private focusMarker?:SVGCircleElement;
  private nativeImage=document.createElementNS(ns,'image');
  private nativeButton=document.createElement('button');
  private approximateButton=document.createElement('button');
  private nativeBounds?:number[];
  private nativeKey='';
  private nativePreferred=true;
  private nativeTicket=0;
  private sceneMessage='';
  constructor(host:HTMLElement){
    this.element.className='live-drawing';this.element.hidden=true;this.element.style.cssText='position:absolute;inset:0;display:flex;flex-direction:column;z-index:2;';
    const style=document.createElement('style');style.textContent='.live-drawing[hidden]{display:none!important}.live-drawing select{max-width:240px;font:inherit;padding:3px}.live-drawing svg{width:100%;height:100%;min-height:0;flex:1;touch-action:none;user-select:none}.live-drawing-status{display:flex;gap:10px;align-items:center;padding:8px 12px;font-size:12px;background:#f5f7f2;color:#29433a;flex-wrap:wrap}';
    const toolbar=document.createElement('div');toolbar.className='live-drawing-status';this.sheets.setAttribute('aria-label','原理图图页');this.layers.setAttribute('aria-label','PCB图层');this.layers.className='pcb-layer-buttons';this.layers.setAttribute('role','group');
    this.pageLabel.className='drawing-page-label';
    this.nativeButton.textContent='原生图';this.approximateButton.textContent='实时图（近似）';
    this.nativeButton.className=this.approximateButton.className='schematic-presentation-button';
    this.nativeButton.onclick=()=>{this.nativePreferred=true;this.applyNativeView(true);};
    this.approximateButton.onclick=()=>{this.nativePreferred=false;this.applyNativeView(true);};
    toolbar.append(this.pageLabel,this.sheets,this.layers,this.nativeButton,this.approximateButton);
    this.sheetRail.className='drawing-sheet-rail';this.sheetRail.setAttribute('aria-label','图页列表');
    const body=document.createElement('div');body.className='live-drawing-body';this.svg.classList.add('drawing-main');this.svg.setAttribute('aria-label','当前版本图纸');this.svg.setAttribute('role','img');this.svg.append(this.root);body.append(this.sheetRail,this.svg);
    this.message.className='drawing-evidence';this.message.setAttribute('role','status');this.element.append(style,toolbar,body,this.message);host.append(this.element);
    this.sheets.onchange=()=>{this.completedKey='';this.scheduleSheet(this.sheets.value);};
    this.svg.addEventListener('wheel',event=>{event.preventDefault();const rect=this.svg.getBoundingClientRect();this.zoom(event.deltaY>0?1/1.1:1.1,(event.clientX-rect.left)/rect.width,(event.clientY-rect.top)/rect.height);},{passive:false});
    this.svg.onpointerdown=event=>{this.svg.setPointerCapture(event.pointerId);this.drag={x:event.clientX,y:event.clientY,box:[...this.viewbox]};};
    this.svg.onpointermove=event=>{if(!this.drag)return;const r=this.svg.getBoundingClientRect();this.viewbox=[this.drag.box[0]-(event.clientX-this.drag.x)*this.drag.box[2]/r.width,this.drag.box[1]-(event.clientY-this.drag.y)*this.drag.box[3]/r.height,...this.drag.box.slice(2)];this.updateViewbox();};
    this.svg.onpointerup=this.svg.onpointercancel=()=>{this.drag=undefined;};
    document.addEventListener('schematic-pin-focus',event=>{
      const detail=(event as CustomEvent).detail,scene=this.state.scene;
      if(!scene||this.element.hidden||scene.kind!=='schematic'||scene.projectId!==detail.projectId||scene.revisionId!==detail.revisionId||scene.sheet!==detail.sheet||(detail.instancePath!==undefined&&detail.instancePath!==scene.instancePath)||scene.sourceSha256!==detail.sourceSha256||!this.state.objects.has(detail.objectId)||!Array.isArray(detail.at)||detail.at.length!==2||!detail.at.every(Number.isFinite))return;
      this.nativePreferred=false;this.applyNativeView(false);
      const [x,y]=detail.at;this.viewbox=[x-20,y-12.5,40,25];this.updateViewbox();this.focusMarker?.remove();
      const marker=document.createElementNS(ns,'circle');marker.setAttribute('cx',String(x));marker.setAttribute('cy',String(y));marker.setAttribute('r','1.5');marker.setAttribute('fill','none');marker.setAttribute('stroke','#d17718');marker.setAttribute('stroke-width','.35');marker.setAttribute('pointer-events','none');marker.classList.add('schematic-pin-focus');this.svg.append(marker);this.focusMarker=marker;
    });
  }
  get active(){return !this.element.hidden;}
  private scheduleSheet(sheet:string){
    if(!this.target)return;
    this.thumbnailController?.abort();
    if(this.sheetTimer)window.clearTimeout(this.sheetTimer);
    if(this.scenes.has(this.sceneKey(this.target.projectId,this.target.revisionId,this.target.kind,sheet))){this.sheetTimer=undefined;void this.show(this.target,sheet);return;}
    this.sheetTimer=window.setTimeout(()=>{this.sheetTimer=undefined;void this.show(this.target!,sheet);},80);
  }
  private renderSheets(scene:DrawingScene){
    const choices=sheetChoices(scene),selected=sceneSelection(scene);
    const scrollTop=this.sheetRail.scrollTop;
    const schematic=scene.kind==='schematic',multiple=schematic&&choices.length>1;this.element.dataset.kind=scene.kind;this.element.dataset.multipleSheets=String(multiple);this.sheetRail.hidden=!multiple;this.pageLabel.hidden=!multiple;
    if(!schematic)return;
    const index=choices.findIndex(s=>s.value===selected);this.pageLabel.textContent=`${index+1} / ${choices.length}`;
    const prefix=scene.projectId+':'+scene.revisionId+':';
    const thumb=document.createElementNS(ns,'svg');thumb.setAttribute('viewBox',scene.bounds.join(' '));const thumbnailRoot=this.root.cloneNode(true) as SVGGElement;thumbnailRoot.style.display='';thumb.append(thumbnailRoot);thumb.setAttribute('aria-hidden','true');
    this.thumbnails.set(prefix+selected,thumb);if(this.thumbnails.size>32)this.thumbnails.delete(this.thumbnails.keys().next().value!);
    this.sheetRail.replaceChildren(...choices.map((choice,i)=>{
      const button=document.createElement('button');button.className='drawing-sheet';button.title=choice.label;button.dataset.sheetChoice=choice.value;button.translate=false;
      if(choice.value===selected)button.setAttribute('aria-current','page');
      const preview=this.thumbnails.get(prefix+choice.value);
      if(preview)button.append(preview.cloneNode(true));else{const placeholder=document.createElement('span');placeholder.className='sheet-placeholder';placeholder.textContent=String(i+1).padStart(2,'0');button.append(placeholder);}
      const label=document.createElement('span');label.textContent=`${i+1}. ${choice.label}`;button.append(label);button.onclick=()=>this.scheduleSheet(choice.value);return button;
    }));
    this.sheetRail.scrollTop=scrollTop;
  }
  deactivate(){this.nativeTicket++;this.layers.hidden=true;this.thumbnailController?.abort();this.element.hidden=true;this.ticket++;this.requestKey='';if(this.sheetTimer)window.clearTimeout(this.sheetTimer);this.sheetTimer=undefined;this.controller?.abort();this.controller=undefined;this.pending.clear();}
  private async loadThumbnails(scene:DrawingScene){
    this.thumbnailController?.abort();
    if(scene.kind!=='schematic'||this.element.hidden)return;
    const controller=new AbortController();this.thumbnailController=controller;
    const prefix=scene.projectId+':'+scene.revisionId+':';
    try{
      for(const choice of sheetChoices(scene)){
        if(controller.signal.aborted)return;
        if(this.thumbnails.has(prefix+choice.value))continue;
        // Yield between pages; foreground navigation aborts this queue immediately.
        await new Promise(resolve=>window.setTimeout(resolve,40));
        if(controller.signal.aborted)return;
        const cacheKey=this.sceneKey(scene.projectId,scene.revisionId,'schematic',choice.value);
        try{
          const params=new URLSearchParams({revisionId:scene.revisionId,kind:'schematic',sheet:choice.sheet});if(choice.instancePath)params.set('instancePath',choice.instancePath);
          const preview=this.scenes.get(cacheKey)||await api<DrawingScene>(`projects/${scene.projectId}/drawing-scene?${params}`,'GET',undefined,controller.signal);
          if(controller.signal.aborted)return;
          if(preview.projectId!==scene.projectId||preview.revisionId!==scene.revisionId||preview.sheet!==choice.sheet||(choice.instancePath&&preview.instancePath!==choice.instancePath)||preview.kind!=='schematic'||preview.mode!=='snapshot')continue;
          const state=new DrawingState();state.apply(preview);
          const thumb=document.createElementNS(ns,'svg');thumb.setAttribute('viewBox',preview.bounds.join(' '));thumb.setAttribute('aria-hidden','true');
          thumb.innerHTML=Array.from(state.objects.values()).map(objectMarkup).join('');
          this.thumbnails.set(prefix+choice.value,thumb);if(this.thumbnails.size>32)this.thumbnails.delete(this.thumbnails.keys().next().value!);
          this.scenes.set(cacheKey,preview);if(this.scenes.size>16)this.scenes.delete(this.scenes.keys().next().value!);
          const button=Array.from(this.sheetRail.querySelectorAll<HTMLButtonElement>('button')).find(item=>item.dataset.sheetChoice===choice.value);
          button?.firstElementChild?.replaceWith(thumb.cloneNode(true));
        }catch(error){
          if(controller.signal.aborted)return;
          // A failed preview must not stop later pages; clicking it retries foreground loading.
        }
      }
    }finally{if(this.thumbnailController===controller)this.thumbnailController=undefined;}
  }
  private updateViewbox(){this.svg.setAttribute('viewBox',this.viewbox.join(' '));}
  reset(){if(this.state.scene){this.viewbox=[...(this.nativePreferred&&this.nativeBounds?this.nativeBounds:this.state.scene.bounds)];this.updateViewbox();}}
  private applyNativeView(reset:boolean){
    const native=this.state.scene?.kind==='schematic'&&this.nativePreferred&&!!this.nativeBounds;
    this.nativeImage.style.display=native?'':'none';this.root.style.display=native?'none':'';
    this.nativeButton.setAttribute('aria-pressed',String(native));this.approximateButton.setAttribute('aria-pressed',String(!native));
    this.element.dataset.schematicPresentation=native?'native':'approximate';
    this.message.textContent=native?`版本 ${this.state.scene!.revisionId.slice(-8)} · KiCad 原生图 · 含图纸框和标题栏`:this.sceneMessage;
    if(reset)this.reset();
  }
  private async renderNative(scene:DrawingScene){
    const ticket=++this.nativeTicket;
    this.nativeButton.hidden=this.approximateButton.hidden=scene.kind!=='schematic';
    const artifact=scene.kind==='schematic'?nativeSheetPreview(scene,this.target?.nativePreviews||[]):undefined;
    const key=artifact?.id||'';
    if(key!==this.nativeKey){this.nativeBounds=undefined;this.nativeImage.removeAttribute('href');this.nativeKey=key;}
    this.nativeButton.disabled=!artifact;
    this.svg.append(this.nativeImage);this.applyNativeView(false);
    if(!artifact){this.applyNativeView(true);return;}
    if(this.nativeBounds)return;
    try{
      const response=await fetch(fileUrl(artifact));if(!response.ok)throw new Error('无法读取原生图');
      const source=await response.text();
      const svg=new DOMParser().parseFromString(source,'image/svg+xml').documentElement;
      const bounds=(svg.getAttribute('viewBox')||'').trim().split(/\s+/).map(Number);
      if(svg.localName!=='svg'||bounds.length!==4||!bounds.every(Number.isFinite)||bounds[2]<=0||bounds[3]<=0)throw new Error('原生图尺寸无效');
      // Display an isolated image; never inject native SVG markup into the document.
      await new Promise<void>((resolve,reject)=>{const image=new Image();image.onload=()=>resolve();image.onerror=()=>reject(new Error('原生图加载失败'));image.src=fileUrl(artifact);});
      if(ticket!==this.nativeTicket)return;
      this.nativeBounds=bounds;this.nativeImage.setAttribute('href',fileUrl(artifact));
      for(const [i,name]of ['x','y','width','height'].entries())this.nativeImage.setAttribute(name,String(bounds[i]));
      this.applyNativeView(true);
    }catch(error){if(ticket!==this.nativeTicket)return;this.nativeButton.disabled=true;this.sceneMessage+=` · 原生图未加载：${(error as Error).message}`;this.applyNativeView(false);}
  }
  zoom(factor:number,x=.5,y=.5){if(this.viewbox.length!==4)return;const [a,b,w,h]=this.viewbox;const nw=w/factor,nh=h/factor;if(nw<.1||nw>100000)return;this.viewbox=[a+(w-nw)*x,b+(h-nh)*y,nw,nh];this.updateViewbox();}
  private filterLayers(){
    const selected=this.selectedLayer,all=selected==='all';
    for(const [id,node]of this.nodes){const layer=this.state.objects.get(id)!.layer;node.style.display=this.state.scene?.kind==='schematic'||layer==='Edge.Cuts'||(all?(this.copperLayers.includes(layer)||['F.SilkS','B.SilkS'].includes(layer)):layer===selected||layer===(selected==='B.Cu'?'B.SilkS':selected==='F.Cu'?'F.SilkS':''))?'':'none';}
    this.layers.querySelectorAll<HTMLButtonElement>('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.layer===selected)));
  }
  private renderLayers(scene:DrawingScene){
    this.layers.hidden=scene.kind!=='pcb';const host=document.getElementById('pcb-layer-toolbar');if(host){host.hidden=scene.kind!=='pcb';if(scene.kind==='pcb')host.append(this.layers);}if(scene.kind!=='pcb')return;
    this.copperLayers=[...(scene.pcbSummary?.copperLayers||scene.layers.filter(l=>l.endsWith('.Cu')))].sort((a,b)=>copperRank(a)-copperRank(b));
    if(this.selectedLayer!=='all'&&!this.copperLayers.includes(this.selectedLayer))this.selectedLayer=this.copperLayers[0]||'F.Cu';
    this.layers.replaceChildren(...[...this.copperLayers,'all'].map(name=>{
      const button=document.createElement('button');button.type='button';button.dataset.layer=name;
      button.textContent=name==='all'?'全部':name==='F.Cu'?'顶层':name==='B.Cu'?'底层':`内层${this.copperLayers.indexOf(name)}`;
      button.title=name==='all'?'全部铜层':name;button.setAttribute('aria-pressed',String(name===this.selectedLayer));
      const object=Array.from(this.state.objects.values()).find(o=>o.layer===name&&o.primitives.some(p=>p.attrs.stroke||p.attrs.fill));
      const attrs=object?.primitives[0]?.attrs;
      const color=String(attrs?.stroke||attrs?.fill||({'F.Cu':'#dc7653','B.Cu':'#688ce6'}[name]||'#ac99ce'));
      button.style.setProperty('--layer-color',name==='all'?'#008b70':color);
      button.onclick=()=>{this.selectedLayer=name;this.filterLayers();};
      button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const buttons=[...this.layers.querySelectorAll('button')],index=buttons.indexOf(button),next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[next].click();buttons[next].focus();};
      return button;
    }));
  }
  private sceneKey(projectId:string,revisionId:string,kind:string,sheet:string){return [projectId,revisionId,kind,sheet].join(':');}
  private renderScene(scene:DrawingScene,scope:string,key:string,same:boolean){
    this.focusMarker?.remove();
    const previousScene=this.state.scene;
    const targetCacheKey=this.sceneKey(scene.projectId,scene.revisionId,scene.kind,sceneSelection(scene));
    const currentCacheKey=previousScene?this.sceneKey(previousScene.projectId,previousScene.revisionId,previousScene.kind,sceneSelection(previousScene)):'';
    if(currentCacheKey&&currentCacheKey!==targetCacheKey)this.pageViews.set(currentCacheKey,{root:this.root,nodes:this.nodes,viewbox:[...this.viewbox]});
    const cachedView=currentCacheKey!==targetCacheKey?this.pageViews.get(targetCacheKey):undefined;
    this.state.apply(scene);
    const sameSheet=same&&previousScene?.sheet===scene.sheet&&previousScene?.instancePath===scene.instancePath;
    if(cachedView&&scene.mode==='snapshot'){
      this.root=cachedView.root;this.nodes=cachedView.nodes;this.viewbox=[...cachedView.viewbox];this.svg.replaceChildren(this.root);this.updateViewbox();
    }else{
      if(currentCacheKey!==targetCacheKey){this.root=document.createElementNS(ns,'g');this.nodes=new Map();this.svg.replaceChildren(this.root);}
      if(scene.mode==='snapshot'){
        // Sort before creating DOM; moving thousands of existing nodes would
        // repeatedly trigger full-subtree processing by page observers.
        const items=paintOrder(scene,Array.from(this.state.objects.values()));this.root.innerHTML=items.map(objectMarkup).join('');this.nodes.clear();
        Array.from(this.root.children).forEach((element,index)=>{const node=element as SVGGElement;node.dataset.objectId=items[index].id;this.nodes.set(items[index].id,node);});
      }else{
        const wanted=new Set(this.state.objects.keys());for(const [id,node]of this.nodes)if(!wanted.has(id)){node.remove();this.nodes.delete(id);}
        for(const item of scene.upsert||[]){let node=this.nodes.get(item.id);if(!node){node=document.createElementNS(ns,'g');node.dataset.objectId=item.id;this.nodes.set(item.id,node);}node.replaceChildren(...item.primitives.map(primitive));if(item.transform)node.setAttribute('transform',item.transform);else node.removeAttribute('transform');}
        // insertBefore only moves nodes when order changed; unchanged objects keep
        // their DOM identity, selection and browser geometry caches.
        let cursor=this.root.firstChild;
        for(const item of paintOrder(scene,Array.from(this.state.objects.values()))){const node=this.nodes.get(item.id)!;if(node===cursor)cursor=cursor.nextSibling;else this.root.insertBefore(node,cursor);}
      }
      if(!sameSheet)this.reset();
    }
    this.displayedScope=scope;
    const choices=sheetChoices(scene);this.sheets.replaceChildren(...choices.map(choice=>{const option=document.createElement('option');option.value=choice.value;option.textContent=choice.label;return option;}));this.sheets.value=sceneSelection(scene);this.sheets.hidden=scene.kind!=='schematic'||choices.length<2;
    this.renderLayers(scene);
    this.svg.style.background=scene.kind==='pcb'?'#132b25':'#fffef8';this.filterLayers();
    this.renderSheets(scene);
    const snapshot:DrawingScene={...scene,mode:'snapshot',objects:Array.from(this.state.objects.values()),upsert:undefined,remove:undefined,order:undefined,baseSceneHash:undefined};
    const cacheKey=this.sceneKey(scene.projectId,scene.revisionId,scene.kind,sceneSelection(scene));this.scenes.delete(cacheKey);this.scenes.set(cacheKey,snapshot);if(this.scenes.size>16)this.scenes.delete(this.scenes.keys().next().value!);
    this.pageViews.delete(cacheKey);this.pageViews.set(cacheKey,{root:this.root,nodes:this.nodes,viewbox:[...this.viewbox]});if(this.pageViews.size>16)this.pageViews.delete(this.pageViews.keys().next().value!);
    document.dispatchEvent(new CustomEvent('drawing-scene-ready',{detail:snapshot}));
    const omitted=scene.coverage.omitted.length;
    const status=scene.validation.status;
    this.sceneMessage=`版本 ${scene.revisionId.slice(-8)} · ${status==='pending'?'候选检查中':['failed','cancelled','interrupted','needs_review'].includes(status)?'候选未采纳':status==='draft'?'PCB草稿':status==='passed'?'本版检查通过':'未关联检查结果'}${omitted?` · ${omitted} 项未覆盖，详见原生图`:''} · 实时近似图，不含图纸框和标题栏`;
    this.message.textContent=this.sceneMessage;void this.renderNative(scene);
    this.message.title=scene.coverage.omitted.map(o=>o.type+': '+o.reason).join('\n');
    this.element.hidden=false;this.completedKey=key;
  }
  show(target:DrawingTarget,sheet?:string):Promise<boolean>{
    const scope=target.projectId+':'+target.kind;
    sheet=sheet||(scope===this.displayedScope&&this.state.scene?.revisionId===target.revisionId?this.sheets.value||undefined:undefined);
    const key=[target.projectId,target.revisionId,target.kind,target.status,sheet||''].join(':');
    const running=this.pending.get(key);if(running)return running;
    this.thumbnailController?.abort();
    this.controller?.abort();const controller=new AbortController();this.controller=controller;
    const request=this.load(target,sheet,controller.signal).finally(()=>{if(this.pending.get(key)===request)this.pending.delete(key);if(this.controller===controller){this.controller=undefined;if(this.state.scene&&!this.element.hidden)void this.loadThumbnails(this.state.scene);}});
    this.pending.set(key,request);return request;
  }
  private async load(target:DrawingTarget,sheet:string|undefined,signal:AbortSignal):Promise<boolean>{
    const scope=target.projectId+':'+target.kind;
    const same=scope===this.displayedScope;
    if(!same){this.nativePreferred=true;this.element.hidden=true;this.completedKey='';this.displayedScope='';this.state.scene=undefined;this.state.objects.clear();this.nodes=new Map();this.root=document.createElementNS(ns,'g');this.svg.replaceChildren(this.root);this.sheets.replaceChildren();}
    this.target=target;
    const key=[scope,target.revisionId,sheet||'',target.status].join(':');
    if(key===this.completedKey){this.element.hidden=false;if(this.state.scene){void this.renderNative(this.state.scene);this.renderSheets(this.state.scene);this.renderLayers(this.state.scene);document.dispatchEvent(new CustomEvent('drawing-scene-ready',{detail:this.state.scene}));}return true;}
    if(key===this.requestKey)return same&&!!this.state.scene;
    const ticket=++this.ticket;this.requestKey=key;
    const cached=sheet?this.scenes.get(this.sceneKey(target.projectId,target.revisionId,target.kind,sheet)):undefined;
    if(cached){this.renderScene({...cached,validation:{...cached.validation,status:target.status}},scope,key,same);this.requestKey='';return true;}
    if(same&&this.state.scene){this.element.hidden=false;this.message.textContent=`正在读取图页 · 当前显示 ${this.state.scene.revisionId.slice(-8)}`;}
    try{
      const params=new URLSearchParams({revisionId:target.revisionId,kind:target.kind});if(sheet){const choice=parseSheetChoice(sheet);params.set('sheet',choice.sheet);if(choice.instancePath)params.set('instancePath',choice.instancePath);}
      const current=this.state.scene;if(current&&current.projectId===target.projectId&&current.revisionId===target.revisionId&&current.kind===target.kind&&(!sheet||sceneSelection(current)===sheet))params.set('since',current.sceneHash);
      const scene=await api<DrawingScene>(`projects/${target.projectId}/drawing-scene?${params}`,'GET',undefined,signal);
      if(ticket!==this.ticket)return false;
      this.renderScene(scene,scope,key,same);return true;
    }catch(error){
      if(signal.aborted||(error as Error).name==='AbortError')return false;
      if(ticket!==this.ticket)return false;
      if(same&&this.state.scene){this.message.textContent=`读取失败 · 当前仍显示 ${this.state.scene.revisionId.slice(-8)} · ${(error as Error).message}`;return true;}
      this.element.hidden=true;return false;
    }finally{if(ticket===this.ticket)this.requestKey='';}
  }
}
