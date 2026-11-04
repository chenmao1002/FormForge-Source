import {api} from './api';
import type {DrawingScene} from './live-drawing';

type PinFinding={objectId:string;reference:string;pin:string;at:number[];labels:{text:string}[]};
type WireQuality={status:string;totalLengthMm:number;bendCount:number|null;nonConnectingCrossings:unknown[]|null;pinPairPaths:unknown[]|null;totalDetourEstimateMm:number|null;collinearOverlaps?:unknown[]|null};
type FunctionalGroup={id:string;name:string;status:string;symbolIds:string[];references:string[];symbolCount:number;wireQuality:WireQuality;fields:{status:string;conflicts:unknown[]};branchContinuity:{status:string}[]};
type FunctionalFeedback={status:string;groups:FunctionalGroup[];sharedWires?:{wireQuality:WireQuality};unassignedWires?:{wireQuality:WireQuality};crossBlockCrossings?:unknown[]|null};
type Feedback={functionalGroups?:FunctionalFeedback;wireQuality?:WireQuality;status:string;labelOnlyPins?:PinFinding[];unavailable?:{objectId:string;reference?:string;reason:string}[]};
type ReadResult={revisionId:string;sheet:string;sheets:{path:string;sha256:string}[];readability:Feedback};
const node=(tag:string,text:string)=>{const n=document.createElement(tag);n.textContent=text;return n;};
export function matchesReadability(scene:DrawingScene,data:ReadResult){
  return scene.kind==='schematic'&&scene.revisionId===data.revisionId&&scene.sheet===data.sheet&&data.sheets.some(s=>s.path===scene.sheet&&s.sha256===scene.sourceSha256);
}
export function displayedReference(scene:DrawingScene,objectId:string,fallback:string){return scene.objects?.find(o=>o.id===objectId+':field:Reference')?.primitives.find(p=>p.tag==='text')?.text||fallback;}

/** Read-only feedback follows the displayed page, never an unbound latest revision. */
export class SchematicReadability {
  private panel=document.createElement('details');
  private body=document.createElement('div');
  private key='';
  private ticket=0;
  private cache=new Map<string,Promise<ReadResult>>();
  constructor(host:HTMLElement){
    this.panel.id='schematic-readability';this.panel.append(node('summary','图面连接提示'),this.body);host.append(this.panel);
    this.clear();
  }
  clear(){this.ticket++;this.key='';this.body.replaceChildren(node('p','选择图页后读取图面提示。'));}
  async show(scene:DrawingScene){
    const sourceKey=[scene.projectId,scene.revisionId,scene.sheet,scene.sourceSha256].join(':');
    const key=sourceKey+':'+(scene.instancePath||'');
    if(scene.kind!=='schematic'||key===this.key)return;
    this.key=key;const ticket=++this.ticket;
    this.body.replaceChildren(node('p','正在读取图面提示…'));
    let pending=this.cache.get(sourceKey);
    if(!pending){
      const params=new URLSearchParams({revisionId:scene.revisionId,sheet:scene.sheet,limit:'1'});
      pending=api<ReadResult>(`projects/${scene.projectId}/schematic?${params}`);this.cache.set(sourceKey,pending);
      if(this.cache.size>16)this.cache.delete(this.cache.keys().next().value!);
    }
    try{
      const data=await pending;
      if(ticket!==this.ticket)return;
      if(!matchesReadability(scene,data))throw new Error('mismatched feedback');
      const feedback=data.readability;
      this.body.replaceChildren();
      const identity=node('p',`${scene.sheet} · ${scene.revisionId.slice(-8)}`);identity.translate=false;this.body.append(identity);
      this.body.append(node('p','以下提示仅反映局部实体连接；跨组标签可能合理，仍需复核原生图。'));
      if(!feedback||feedback.status==='unavailable'||!feedback.labelOnlyPins){
        this.body.append(node('p','本页逐引脚分析不可用。'));
      }else{
        const count=node('p','待复核引脚');count.append(node('strong',` ${feedback.labelOnlyPins.length}`));this.body.append(count);
        const list=document.createElement('div');list.className='schematic-pin-findings';
        for(const pin of feedback.labelOnlyPins){
          const button=document.createElement('button');
          const caption=node('span',`${displayedReference(scene,pin.objectId,pin.reference)}.${pin.pin} · ${pin.labels.map(l=>l.text).join(', ')}`);caption.translate=false;button.append(caption);
          button.type='button';button.title='定位引脚';
          button.onclick=()=>document.dispatchEvent(new CustomEvent('schematic-pin-focus',{detail:{projectId:scene.projectId,revisionId:scene.revisionId,sheet:scene.sheet,instancePath:scene.instancePath,sourceSha256:scene.sourceSha256,objectId:pin.objectId,at:pin.at}}));
          list.append(button);
        }
        this.body.append(list);
        if(!feedback.labelOnlyPins.length)this.body.append(node('p','未检测到标签独立引脚；这不代表图面复核已完成。'));
      }
      if(feedback?.wireQuality){
        const quality=feedback.wireQuality,measure=node('p','');
        measure.append(node('span','实体线总长'),node('strong',` ${quality.totalLengthMm.toFixed(2)} mm`));
        if(quality.status==='available'){
          measure.append(node('span',' · 转弯'),node('strong',` ${quality.bendCount}`),node('span',' · 非连接交叉'),node('strong',` ${quality.nonConnectingCrossings?.length}`));
          this.body.append(measure);
          if(quality.pinPairPaths?.length){const paths=node('p','');paths.append(node('span','可比较路径'),node('strong',` ${quality.pinPairPaths.length}`),node('span',' · 绕行余量'),node('strong',` ${(quality.totalDetourEstimateMm||0).toFixed(2)} mm`));this.body.append(paths);}
          else this.body.append(node('p','本页没有可比较的双引脚实体路径。'));
          this.body.append(node('p','仅统计本页已知实体几何；绕行余量需结合路径覆盖与原生图判断。'));
        }else{this.body.append(measure,node('p','本页几何分析超出预算，其余统计不可用。'));}
      }
      const functional=feedback?.functionalGroups;
      if(functional){
        this.body.append(node('h4','功能块分项统计'));
        if(functional.status==='not_declared')this.body.append(node('p','本页未登记功能块，不根据器件位置或网名猜测归属。'));
        else if(functional.status==='unavailable')this.body.append(node('p','本页功能块分析不可用，请查看完整报告和原生图。'));
        else{
          this.body.append(node('p','导线按实体连接归属；跨块与未归属导线单列。字段提示为全页包络估计，不代表原生遮挡结论。'));
          for(const group of functional.groups){
            const card=document.createElement('details'),title=node('summary',group.name);title.translate=false;card.append(title);
            const refs=node('p',group.symbolIds.map((id,i)=>displayedReference(scene,id,group.references[i]||id)).join(', '));refs.translate=false;card.append(refs);
            if(group.status!=='available')card.append(node('p','成员或几何不完整，统计仅覆盖已知对象。'));
            const quality=group.wireQuality,line=node('p','');
            line.append(node('span','实体线总长'),node('strong',` ${quality.totalLengthMm.toFixed(2)} mm`));
            if(quality.status==='available')line.append(node('span',' · 转弯'),node('strong',` ${quality.bendCount}`),node('span',' · 非连接交叉'),node('strong',` ${quality.nonConnectingCrossings?.length}`),node('span',' · 重叠'),node('strong',` ${quality.collinearOverlaps?.length}`));
            card.append(line);
            if(quality.status!=='available')card.append(node('p','功能块几何分析超出预算，其余统计不可用。'));
            else if(quality.pinPairPaths?.length){const paths=node('p','');paths.append(node('span','可比较路径'),node('strong',` ${quality.pinPairPaths.length}`),node('span',' · 绕行余量'),node('strong',` ${(quality.totalDetourEstimateMm||0).toFixed(2)} mm`));card.append(paths);}
            else card.append(node('p','本功能块没有可比较的双引脚实体路径。'));
            const fields=node('p','');fields.append(node('span','字段提示'),node('strong',` ${group.fields.conflicts.length}`));card.append(fields);
            if(group.fields.status==='partial')card.append(node('p','存在未覆盖的文字或符号包络，需原生图复核。'));
            if(group.branchContinuity.length){const branches=node('p','');branches.append(node('span','声明支路实体路径'),node('strong',` ${group.branchContinuity.filter(b=>b.status==='connected').length}/${group.branchContinuity.length}`));card.append(branches);}
            this.body.append(card);
          }
          for(const [label,bucket] of [['跨块导线',functional.sharedWires],['未归属导线',functional.unassignedWires]] as const){if(bucket){const row=node('p','');row.append(node('span',label),node('strong',` ${bucket.wireQuality.totalLengthMm.toFixed(2)} mm`));this.body.append(row);}}
          if(functional.crossBlockCrossings){const row=node('p','');row.append(node('span','跨块非连接交叉'),node('strong',` ${functional.crossBlockCrossings.length}`));this.body.append(row);}
        }
      }
      if(feedback?.unavailable?.length){
        this.body.append(node('p','以下对象无法分析：'));
        for(const item of feedback.unavailable){const row=node('p',`${displayedReference(scene,item.objectId,item.reference||item.objectId)} · ${item.reason}`);row.translate=false;this.body.append(row);}
      }
    }catch{
      this.cache.delete(sourceKey);
      if(ticket!==this.ticket)return;
      this.key='';this.body.replaceChildren(node('p','图面提示读取失败。'));
      const retry=node('button','重试加载') as HTMLButtonElement;retry.onclick=()=>void this.show(scene);this.body.append(retry);
    }
  }
}
