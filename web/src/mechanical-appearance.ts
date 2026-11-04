import {api,requestId,fileUrl,type Context} from './api';

const node=(tag:string,text='')=>{const el=document.createElement(tag);el.textContent=text;return el;};
const hash=async(bytes:Uint8Array)=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes as BufferSource)),b=>b.toString(16).padStart(2,'0')).join('');

export function referencedSources(design:any,sources:any[]){
  const paths=new Set<string>();
  const walk=(value:any)=>{if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')for(const [key,v] of Object.entries(value)){
    if((key==='step'||key==='image')&&typeof v==='string')paths.add(v);
    else if(key==='evidence_files'&&Array.isArray(v))v.forEach(p=>paths.add(p));
    else walk(v);
  }};walk(design);
  return sources.filter(s=>paths.has(s.frozenPath||`inputs/${s.id}${s.path.slice(s.path.lastIndexOf('.')).toLowerCase()}`));
}

export function defaultDecal(part:any,image:string,sha:string,id:string){
  const bounds=part.boundsCADmm;
  if(!bounds)throw new Error('缺少零件尺寸，请先完成一次模型构建。');
  return {id,part:part.id,image,image_sha256:sha,center_mm:[(bounds.min[0]+bounds.max[0])/2,(bounds.min[1]+bounds.max[1])/2,bounds.max[2]],
    normal:[0,0,1],up:[0,1,0],width_mm:Math.max(.1,(bounds.max[0]-bounds.min[0])*.25),height_mm:Math.max(.1,(bounds.max[1]-bounds.min[1])*.2),depth_mm:1,rotation_deg:0,opacity:1};
}

async function uploadImage(projectId:string,file:File,license:string){
  if(!/\.(png|jpe?g)$/i.test(file.name)||file.size>16*1024*1024||!file.size)throw new Error('请选择不超过 16 MB 的 PNG / JPEG 图片。');
  const bytes=new Uint8Array(await file.arrayBuffer());
  const path=`projects/${projectId}/source-transfers`;
  let upload=await api(path,'POST',{action:'begin',name:file.name,uri:'upload://'+encodeURIComponent(file.name),version:'user-upload',license,
    note:'用户上传的外观参考或表面贴花；不作为尺寸或制造证明。',origin:'third-party-declared',size:bytes.length,sha256:await hash(bytes),requestId:requestId()});
  for(let offset=upload.receivedBytes;offset<bytes.length;offset+=upload.maxChunkBytes){
    const chunk=bytes.slice(offset,offset+upload.maxChunkBytes);
    let raw='';for(let i=0;i<chunk.length;i+=8192)raw+=String.fromCharCode(...chunk.subarray(i,i+8192));
    upload=await api(path,'POST',{action:'append',uploadId:upload.id,offset,sha256:await hash(chunk),contentBase64:btoa(raw)});
  }
  const committed=await api(path,'POST',{action:'commit',uploadId:upload.id});
  return {...committed.source.bindings.mechanical,frozenPath:`inputs/${committed.source.id}${file.name.slice(file.name.lastIndexOf('.')).toLowerCase()}`,previewUrl:committed.source.downloadUrl};
}

export function appearanceEditor(design:any,sources:any[],context:Context,read:any,jobId:string|undefined,changed:()=>void,busy:(value:boolean)=>void,isCurrent:()=>boolean){
  const section=node('details') as HTMLDetailsElement;section.dataset.parameterSection='surface';section.className='appearance-editor';
  section.append(node('summary','外观参考、材质与贴花'));
  const content=node('div');section.append(content);
  const plan=()=>design.appearance??=( {brief:'',references:[],materials:[],decals:[]} );
  const parts=(read.result?.parts||[]).filter((p:any)=>p.id!==read.rebuildPcbBinding?.pcbPart);
  const field=(parent:HTMLElement,title:string,value:any,update:(v:string)=>void,type='text')=>{
    const label=node('label',title),input=document.createElement(type==='textarea'?'textarea':'input');
    if(input instanceof HTMLInputElement){input.type=type;if(type==='number')input.step='any';}
    input.value=String(value??'');input.oninput=()=>{update(input.value);changed();};label.append(input);parent.append(label);return input;
  };
  const remove=(parent:HTMLElement,action:()=>void)=>{const b=node('button','移除') as HTMLButtonElement;b.type='button';b.className='button secondary';b.onclick=()=>{action();render();changed();};parent.append(b);};
  const preview=(parent:HTMLElement,path:string)=>{
    const source=sources.find(s=>s.frozenPath===path),artifact=context.artifacts.find(a=>a.jobId===jobId&&a.name===path.split('/').pop());
    const url=source?.previewUrl||(artifact?fileUrl(artifact):null);
    if(url){const img=document.createElement('img');img.src=url;img.alt='外观图片';img.className='appearance-thumbnail';parent.append(img);}
  };
  const render=()=>{
    content.replaceChildren();
    field(content,'设计意图',design.appearance?.brief||'',v=>plan().brief=v,'textarea');
    content.append(node('p','参考图由 Agent 生成或读取，再构建可编辑 CAD。图片不用于推断隐藏结构或精确尺寸。'));
    for(const ref of design.appearance?.references||[]){
      const row=node('div');row.className='appearance-card';preview(row,ref.image);
      field(row,'参考用途',ref.purpose,v=>ref.purpose=v);
      field(row,'生成提示词（如适用）',ref.generation_prompt||'',v=>ref.generation_prompt=v,'textarea');
      remove(row,()=>plan().references=plan().references.filter((r:any)=>r!==ref));content.append(row);
    }
    const uploadRow=node('div');uploadRow.className='appearance-card';
    const license=field(uploadRow,'图片来源／使用许可','用户上传，授权信息未声明',()=>{});
    const file=document.createElement('input');file.type='file';file.accept='.png,.jpg,.jpeg';file.setAttribute('aria-label','选择外观图片');uploadRow.append(file);
    const target=document.createElement('select');target.setAttribute('aria-label','贴花所属零件');
    for(const p of parts){const option=node('option',p.displayName||p.id) as HTMLOptionElement;option.value=p.id;target.append(option);}uploadRow.append(target);
    const status=node('p');status.setAttribute('role','status');uploadRow.append(status);
    for(const [kind,title] of [['reference','添加参考图'],['decal','添加贴花']]){
      const button=node('button',title) as HTMLButtonElement;button.type='button';button.className='button secondary';
      button.onclick=async()=>{
        if(!file.files?.[0]){status.textContent='请先选择图片。';return;}
        const selected=parts.find((p:any)=>p.id===target.value);
        if(kind==='decal'&&!selected){status.textContent='请选择贴花所属零件。';return;}
        const uploadButtons=Array.from(uploadRow.querySelectorAll('button'));uploadButtons.forEach(b=>b.disabled=true);busy(true);status.textContent='正在导入图片…';
        try{
          const source=await uploadImage(context.project.id,file.files[0],license.value.trim()||'用户上传，授权信息未声明');
          if(!isCurrent())return;
          sources.push(source);
          if(kind==='reference')plan().references.push({image:source.frozenPath,image_sha256:source.sha256,purpose:'外观比例与造型参考',generation_prompt:''});
          else plan().decals.push(defaultDecal(selected,source.frozenPath,source.sha256,'decal_'+requestId().replaceAll('-','').slice(0,12)));
          render();changed();
        }catch(error){status.textContent=(error as Error).message;}
        finally{busy(false);uploadButtons.forEach(b=>b.disabled=false);}
      };uploadRow.append(button);
    }content.append(uploadRow);
    const materials=node('div');materials.className='mechanical-parameter-grid';
    for(const p of parts){
      const row=node('div');row.className='appearance-card';row.append(node('strong',p.displayName||p.id));
      const existing=(design.appearance?.materials||[]).find((r:any)=>r.part===p.id);
      const preset=document.createElement('select');preset.setAttribute('aria-label',(p.displayName||p.id)+' 材质');
      for(const [value,label] of [['original','原始材质'],['matte','哑光塑料'],['satin','柔和半哑光'],['metal','金属'],['polished','抛光金属'],['custom','自定义']]){
        const option=node('option',label) as HTMLOptionElement;option.value=value;preset.append(option);
      }preset.value=existing?'custom':'original';
      preset.onchange=()=>{
        const a=plan();a.materials=a.materials.filter((r:any)=>r.part!==p.id);
        const values:Record<string,number[]>={matte:[.85,0],satin:[.45,0],metal:[.35,1],polished:[.12,1],custom:[.55,0]};
        if(values[preset.value])a.materials.push({part:p.id,roughness:values[preset.value][0],metalness:values[preset.value][1]});
        render();changed();
      };row.append(preset);
      if(existing)for(const [key,title] of [['roughness','粗糙度'],['metalness','金属度']]){
        const input=field(row,title,existing[key],v=>existing[key]=Number(v),'number') as HTMLInputElement;input.min='0';input.max='1';input.step='any';
      }materials.append(row);
    }content.append(materials);
    for(const decal of design.appearance?.decals||[]){
      const row=node('div');row.className='appearance-card';row.append(node('strong',decal.part+' · '+decal.id));preview(row,decal.image);
      const grid=node('div');grid.className='mechanical-parameter-grid';
      for(const [key,title] of [['width_mm','宽度 mm'],['height_mm','高度 mm'],['depth_mm','投影深度 mm'],['rotation_deg','旋转 °'],['opacity','不透明度']]){
        const input=field(grid,title,decal[key]??(key==='opacity'?1:0),v=>decal[key]=Number(v),'number') as HTMLInputElement;
        if(key.endsWith('_mm'))input.min='.001';if(key==='opacity'){input.min='0';input.max='1';}
      }
      for(const [key,title,defaultValue] of [['center_mm','中心',[0,0,0]],['normal','朝外方向',[0,0,1]],['up','图片朝上',[0,1,0]]] as const){
        for(let i=0;i<3;i++)field(grid,title+' '+['X','Y','Z'][i],(decal[key]||defaultValue)[i],v=>{decal[key]??=[...defaultValue];decal[key][i]=Number(v);},'number');
      }row.append(grid);remove(row,()=>plan().decals=plan().decals.filter((d:any)=>d!==decal));content.append(row);
    }
    content.append(node('p','贴花默认投影到零件顶部；调整中心、朝外方向和投影深度后重建。下载 GLB 保留贴花，STEP / STL 保留工程实体。'));
  };render();return section;
}
