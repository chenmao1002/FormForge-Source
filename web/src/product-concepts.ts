import {api,type Context} from './api';
export interface ProductConcepts {
  version:number;designBinding:string;stage:string;stale:boolean;brief?:string;constraints?:string[];
  references:{id:string;title:string;url:string;image:{sourceId:string;sha256:string};observed:string;adopt:string;avoid:string}[];
  concepts:{id:string;title:string;image:{sourceId:string;sha256:string};benefits:string;risks:string;prompt:string;referenceIds:string[]}[];
  selection?:{conceptId:string;sourceText:string}|null;
}
const el=(tag:string,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
export function renderProductConcepts(host:HTMLElement,context:Context|null){
  host.replaceChildren();host.hidden=!context;
  host.dataset.project=context?.project.id||'';
  if(!context)return;
  const state=context.productConcepts;
  host.append(el('h2','参考调研与概念方案'));
  host.append(el('p','同类产品参考 → 2～3 个概念方向 → 用户选择 → 可编辑 CAD → 外观与工程复核'));
  if(!state?.version){host.append(el('p','尚未开始：由 Agent 搜索同类产品图片，记录借鉴点与限制，再生成概念图供选择。'));return;}
  const label:Record<string,string>={research:'参考调研中',awaiting_selection:'等待你选择方案',selected:'已选定造型方向',needs_review:'需求已变化，请重新审查方案'};
  host.append(el('strong',label[state.stage]||state.stage),el('p',state.brief||''));
  const image=(sourceId:string,title:string)=>{
    const link=el('a') as HTMLAnchorElement;link.href=`/api/projects/${encodeURIComponent(context.project.id)}/concept-images/${encodeURIComponent(sourceId)}`;link.target='_blank';link.rel='noopener';
    const img=el('img') as HTMLImageElement;img.src=link.href;img.alt=title;img.loading='lazy';img.style.cssText='width:100%;height:auto;border-radius:12px';link.append(img);return link;
  };
  const refs=el('details');refs.append(el('summary',`同类产品参考（${state.references.length}）`));
  for(const r of state.references){const card=el('article');card.className='concept-card';card.append(el('h3',r.title),image(r.image.sourceId,r.title),el('p',r.observed),el('p','借鉴：'+r.adopt),el('p','不直接采用：'+r.avoid));const a=el('a','查看原始来源') as HTMLAnchorElement;a.href=r.url;a.target='_blank';a.rel='noopener noreferrer';card.append(a);refs.append(card);}
  host.append(refs);
  const grid=el('div');grid.className='concept-grid';
  for(const c of state.concepts){
    const card=el('article');card.className='concept-card';card.append(el('h3',c.title),image(c.image.sourceId,c.title),el('p',c.benefits),el('p','结构与图像限制：'+c.risks));
    const provenance=el('details');provenance.append(el('summary','生成提示词与参考'),el('p',c.referenceIds.join(' · ')),el('p',c.prompt));card.append(provenance);
    const button=el('button',state.selection?.conceptId===c.id?'已选定':'选择此方向') as HTMLButtonElement;button.className='button secondary';button.disabled=state.stale||state.selection?.conceptId===c.id;
    button.onclick=async()=>{
      button.disabled=true;
      try{const result=await api<ProductConcepts>(`projects/${context.project.id}/product-concepts/selection`,'POST',{expectedVersion:state.version,expectedDesignBinding:state.designBinding,conceptId:c.id,sourceText:`用户在方案卡片选择：${c.title}`});
        if(host.dataset.project===context.project.id){context.productConcepts=result;renderProductConcepts(host,context);}
      }catch(e){if(host.dataset.project===context.project.id){card.append(el('p',String(e)));button.disabled=false;}}
    };card.append(button);grid.append(card);
  }
  host.dataset.project=context.project.id;host.append(grid);
  const constraints=el('details');constraints.append(el('summary','保留的产品约束'));for(const c of state.constraints||[])constraints.append(el('p',c));host.append(constraints);
  host.append(el('p','选择只确认造型方向，不会自动修改正式 CAD。概念图不证明内部空间、真实端子、打印壁厚或装配可行。'));
}
