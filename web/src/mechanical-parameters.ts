import {api,requestId,type Context,type Revision} from './api';
import {appearanceEditor,referencedSources} from './mechanical-appearance';

const make=(tag:string,text='',cls='')=>{const node=document.createElement(tag);node.textContent=text;node.className=cls;return node;};
const labels:Record<string,string>={box:'长方体',cylinder:'圆柱',cone:'锥体',sphere:'球体',extrude:'拉伸',revolve:'旋转',loft:'放样',sweep:'扫掠',union:'合并',cut:'切除',intersect:'求交',fillet:'圆角',chamfer:'倒角',shell:'抽壳',transform:'移动 / 旋转',mirror:'镜像',linear_pattern:'直线阵列',polar_pattern:'环形阵列',step:'导入零件'};
labels.standard_part='标准件';

export interface PartPresentation {id:string;displayName:string;colorGroup?:string}
interface ColorGroup {id:string;label:string;parts:any[];specifications:string[]}

export function partColorGroups(design:any,presentation:PartPresentation[]):ColorGroup[]{
  const rows=new Map(presentation.map(row=>[row.id,row]));
  const groups=new Map<string,ColorGroup>();
  for(const part of design.parts||[]){
    const row=rows.get(part.name),screw=row?.colorGroup==='screws';
    const id=screw?'screws':'part:'+part.name;
    if(!groups.has(id))groups.set(id,{id,label:screw?'螺钉统一颜色':row?.displayName||part.display_name||part.name,parts:[],specifications:[]});
    const group=groups.get(id)!;group.parts.push(part);
    if(screw&&row)group.specifications.push(row.displayName);
  }
  return [...groups.values()];
}

export function applyGroupColor(design:any,group:ColorGroup,hex:string){
  const color=[1,3,5].map(offset=>parseInt(hex.slice(offset,offset+2),16)/255);
  for(const part of group.parts)part.color=[...color];
  if(group.id==='screws')design.screw_color=[...color];
}

function section(title:string,key:string,defaultOpen=false){
  const details=document.createElement('details');details.dataset.parameterSection=key;
  try{const saved=localStorage.getItem('formforge-mechanical-section-'+key);details.open=saved===null?defaultOpen:saved==='open';}catch{details.open=defaultOpen;}
  details.append(make('summary',title));
  details.ontoggle=()=>{try{localStorage.setItem('formforge-mechanical-section-'+key,details.open?'open':'closed');}catch{}};
  return details;
}

export function parameterValue(value:string):number|string{
  const number=Number(value);
  return value.trim()!==''&&Number.isFinite(number)?number:value;
}

// Match the native FeatureDesign OutputPart default without writing it back
// into a design whose caller intentionally omitted the optional color.
export function partColorHex(color?:readonly number[]):string{
  return '#'+(color??[.32,.55,.68]).map(v=>Math.round(v*255).toString(16).padStart(2,'0')).join('');
}

export function mechanicalRebuildRequest(read:any,project:Context['project'],design:any){
  if(read.pcbDependency&&!read.rebuildPcbBinding?.pcbRevisionId)throw new Error('缺少原版本PCB绑定，刷新后再重建；不会静默解除依赖。');
  const sources=(read.sources||[]).map(({id,path,sha256,uri,version,license,note}:any)=>({id,path,sha256,uri,version,license,note}));
  return {...structuredClone(read.rebuildPcbBinding||{}),expectedProjectVersion:project.version,expectedMechanicalRevisionId:project.moduleHeads?.mechanical||null,
    design:structuredClone(design),sources,message:'调整模型参数与外观',requestId:requestId()};
}

export class MechanicalParameters{
  private generation=0;
  private key='';
  private draft:any;
  private sources:any[]=[];
  private current:Context|null=null;
  private pending=false;
  private baseline:any;
  constructor(private host:HTMLElement,private refresh:()=>Promise<void>,private notify:(message:string,error?:boolean)=>void){}

  async render(context:Context|null,revision?:Revision){
    this.current=context;
    const generation=++this.generation;
    if(!context||!revision||!['feature_model','assembly'].includes(revision.mechanicalDesign?.kind||'')){
      this.key='';this.host.replaceChildren();this.host.hidden=true;return;
    }
    this.host.hidden=false;
    const key=context.project.id+':'+revision.id;
    if(this.key===key)return;
    this.host.replaceChildren(make('p','读取模型参数…','field-note'));
    try{
      const read=await api(`projects/${context.project.id}/mechanical?revisionId=${revision.id}`);
      if(generation!==this.generation)return;
      this.key=key;this.draft=structuredClone(read.design);this.baseline=structuredClone(read.design);this.sources=read.sources||[];
      this.form(read);
    }catch(error){if(generation===this.generation)this.host.replaceChildren(make('p',(error as Error).message,'connection-problem'));}
  }

  private form(read:any){
    const panel=section('模型参数','panel');panel.className='mechanical-parameter-panel';
    const form=document.createElement('form');form.className='mechanical-parameter-form';
    form.append(make('p','调整尺寸或颜色，生成新的设计版本。原版本会保留。','field-note'));
    const parameters=Object.entries(this.draft.parameters||{});
    const dimensions=section(`尺寸参数 · ${parameters.length}`,'dimensions',true);
    const fields=make('div','','mechanical-parameter-grid');
    for(const [name,value] of parameters){
      const label=make('label',this.draft.parameter_labels?.[name]||name);
      const input=document.createElement('input');input.type='text';input.value=String(value);input.required=true;input.dataset.parameter=name;
      input.oninput=()=>{this.draft.parameters[name]=parameterValue(input.value);};label.append(input);
      const measured=read.result?.measures?.parameters?.[name];
      if(typeof value==='string'&&measured!==undefined)label.append(make('small',`本版计算值 ${Number(measured.toFixed(4))}`,'field-note'));
      fields.append(label);
    }
    if(!parameters.length)fields.append(make('p','此模型尚未设置尺寸参数，可由 Agent 添加。','field-note'));
    dimensions.append(fields);if(this.draft.kind==='feature_model')form.append(dimensions);
    const groups=partColorGroups(this.draft,read.partPresentation||[]);
    const appearance=section(`外观颜色 · ${groups.length}`,'appearance',true);
    const colors=make('div','','mechanical-parameter-grid mechanical-color-grid');
    for(const group of groups){
      const screw=group.id==='screws';
      const label=make('label',screw?group.label:group.label+' · 颜色');
      const input=document.createElement('input');input.type='color';input.value=partColorHex(screw?this.draft.screw_color??group.parts[0].color:group.parts[0].color);
      input.dataset.colorGroup=group.id;
      input.setAttribute('aria-label',screw?group.label:group.label+' 颜色');
      input.oninput=()=>applyGroupColor(this.draft,group,input.value);label.append(input);
      if(screw){
        label.append(make('small',`统一调整 ${group.parts.length} 颗螺钉`,'field-note'));
        const counts=new Map<string,number>();for(const spec of group.specifications)counts.set(spec,(counts.get(spec)||0)+1);
        label.append(make('small',[...counts].map(([spec,count])=>`${spec} × ${count}`).join(' · '),'mechanical-screw-specs'));
      }
      colors.append(label);
    }
    const tools=make('div','','mechanical-color-tools');
    const search=document.createElement('input');search.type='search';search.placeholder='搜索颜色或零件…';search.setAttribute('aria-label','搜索颜色或零件');
    const empty=make('p','没有匹配的零件','field-note');empty.hidden=true;
    const colorLabels=Array.from(colors.children) as HTMLElement[];
    const colorNames=groups.map(g=>(g.label+' '+g.id).toLocaleLowerCase());
    search.oninput=()=>{const query=search.value.trim().toLocaleLowerCase();colorLabels.forEach((label,i)=>label.hidden=!colorNames[i].includes(query));empty.hidden=colorLabels.some(label=>!label.hidden);};
    tools.append(search);
    for(const [mode,title] of [['grid','网格'],['list','列表']]){
      const toggle=make('button',title,'button secondary') as HTMLButtonElement;toggle.type='button';toggle.setAttribute('aria-pressed',String(mode==='grid'));
      toggle.onclick=()=>{colors.classList.toggle('color-list',mode==='list');tools.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b===toggle)));};tools.append(toggle);
    }
    appearance.append(tools,colors,empty);form.append(appearance);
    const editorKey=this.key;
    const change=()=>form.dispatchEvent(new Event('input',{bubbles:true}));
    form.append(appearanceEditor(this.draft,this.sources,this.current!,read,this.current?.revisions.find(r=>r.id===read.revisionId)?.createdByJobId,
      change,value=>{this.pending=value;change();},()=>this.key===editorKey));
    const features=this.draft.features||[];
    const details=section(`建模特征 · ${features.length} 步`,'features');
    const list=document.createElement('ol');list.className='mechanical-feature-list';for(const feature of features)list.append(make('li',`${labels[feature.op]||feature.op} · ${feature.id}`));details.append(list);if(features.length)form.append(details);
    const footer=make('div','','mechanical-edit-footer');
    const status=make('span','参数与所选版本一致','field-note');status.setAttribute('role','status');
    const button=make('button','应用参数并重建','button primary') as HTMLButtonElement;button.type='submit';button.disabled=true;
    const reset=make('button','还原修改','button secondary') as HTMLButtonElement;reset.type='button';reset.disabled=true;
    const dirty=()=>{const changed=JSON.stringify(this.draft)!==JSON.stringify(this.baseline);button.disabled=this.pending||!changed;reset.disabled=this.pending||!changed;status.textContent=changed?'有未应用修改 · 重建后更新模型':'参数与所选版本一致';};
    form.addEventListener('input',dirty);
    reset.onclick=()=>{this.draft=structuredClone(this.baseline);this.form(read);};
    footer.append(status,reset,button);form.append(footer);
    form.onsubmit=async event=>{
      event.preventDefault();if(this.pending||!this.current)return;
      const project=this.current.project;const submittedKey=this.key;
      this.pending=true;button.disabled=true;reset.disabled=true;button.textContent='正在提交…';
      const submittedDesign=structuredClone(this.draft);
      try{
        await api(`projects/${project.id}/mechanical-designs`,'POST',mechanicalRebuildRequest({...read,sources:referencedSources(submittedDesign,this.sources)},project,submittedDesign));
        this.notify('已开始重建，完成后显示新版本。');await this.refresh();
      }catch(error){this.notify((error as Error).message,true);}
      finally{this.pending=false;if(this.key===submittedKey){dirty();button.textContent='应用参数并重建';}}
    };
    panel.append(form);this.host.replaceChildren(panel);
  }
}
