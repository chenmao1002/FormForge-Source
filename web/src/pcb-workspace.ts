import type {Context,Revision} from './api';
import type {DrawingScene} from './live-drawing';
import {planningPanel} from './pcb-planning';

const el=(id:string)=>document.getElementById(id)!;
export class PcbWorkspace {
  private key='';
  private planningKey='';
  private planningContext:Context|null=null;
  private scenes=new Map<string,DrawingScene>();
  constructor(){
    document.addEventListener('drawing-scene-ready',event=>{
      const scene=(event as CustomEvent<DrawingScene>).detail;
      if(scene.kind!=='pcb')return;
      const key=scene.projectId+':'+scene.revisionId;this.scenes.set(key,scene);
      if(this.scenes.size>16)this.scenes.delete(this.scenes.keys().next().value!);
      if(this.key===key)this.metrics(scene);
    });
  }
  render(view:string,context:Context|null,revision?:Revision){
    const active=view==='pcb';el('pcb-overview').hidden=!active;
    this.key=active&&context&&revision?context.project.id+':'+revision.id:'';
    this.planningContext=context;
    const planningKey=active&&revision?.entrypoints.board?this.key:'';
    if(planningKey!==this.planningKey){
      this.planningKey=planningKey;el('pcb-planning-panel').replaceChildren();
      if(planningKey&&context&&revision)el('pcb-planning-panel').append(planningPanel(context,revision,()=>this.planningContext!));
    }
    if(!active)return;
    el('pcb-file').textContent=revision?.entrypoints.board||'尚无 PCB';
    el('pcb-file').translate=false;
    const file=revision?.manifest?.find(f=>f.path===revision.entrypoints.board);
    el('pcb-file-meta').textContent=file?`${(file.size/1024).toFixed(1)} KB · ${revision!.createdAt.slice(0,10)}`:'图纸与报告绑定所选版本';
    for(const id of ['pads','vias','layers','width'])el('pcb-'+id).textContent='—';
    el('pcb-summary-note').textContent=revision?.entrypoints.board?'正在读取板级统计…':'此版本尚无 PCB 文件';
    const cached=this.scenes.get(this.key);if(cached)this.metrics(cached);
  }
  private metrics(scene:DrawingScene){
    const summary=scene.pcbSummary;if(!summary)return;
    el('pcb-pads').textContent=String(summary.padCount);el('pcb-vias').textContent=String(summary.viaCount);
    el('pcb-layers').textContent=String(summary.copperLayers.length);
    el('pcb-width').textContent=summary.minTrackWidthMm===null?'—':`${Number(summary.minTrackWidthMm.toFixed(4))} mm`;
    el('pcb-summary-note').textContent=`${summary.componentCount} 个器件 · ${summary.trackCount} 段走线 · 最小线宽为本版实际走线宽度`;
  }
}
