import {api,requestId} from './api';
import {devicePanel} from './firmware-device';

const make=(tag:string,text='',cls='')=>{const e=document.createElement(tag);e.textContent=text;e.className=cls;return e;};
const hash=async(data:Uint8Array)=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',data as BufferSource)),v=>v.toString(16).padStart(2,'0')).join('');
const b64=(data:Uint8Array)=>{let s='';for(let i=0;i<data.length;i+=8192)s+=String.fromCharCode(...data.subarray(i,i+8192));return btoa(s);};
const address=(n:number)=>'0x'+n.toString(16).toUpperCase();

export class FirmwareFlashPanel {
  private generation=0;
  deactivate(){this.generation++;}
  async render(host:HTMLElement,project:string){
    const generation=++this.generation;
    const current=()=>generation===this.generation&&host.isConnected;
    host.replaceChildren(make('h3','固件包、计划与设备'),make('p','导入与计划不连接设备；设备操作需要单独明确授权。','field-note'));
    const status=make('p','','field-note');status.setAttribute('role','status');host.append(status);
    try {
      const [profile,history]=await Promise.all([api('firmware-flash-profile'),api(`projects/${project}/flash-bundles`)]);
      if(!current())return;
      const env=profile.environment;
      host.append(make('p',env?`pyOCD ${env.packageVersions.pyocd} · esptool ${env.packageVersions.esptool}`:'烧录工具环境未安装','field-note'));
      host.append(make('p','OpenOCD 仅保留扩展入口，尚未接入。','quiet-note'));
      const form=document.createElement('form');form.className='flash-import-form';
      const target=document.createElement('select');target.setAttribute('aria-label','目标芯片');
      for(const name of Object.keys(profile.targets)){const option=document.createElement('option');option.value=name;option.textContent=name;target.append(option);}
      const size=document.createElement('select');size.setAttribute('aria-label','声明 Flash 容量');
      const armSize=document.createElement('option');armSize.value='32768';armSize.textContent='32 KiB';size.append(armSize);
      for(const n of [1,2,4,8,16,32]){const option=document.createElement('option');option.value=String(n*1024*1024);option.textContent=n+' MiB';size.append(option);}size.value='32768';size.disabled=true;
      target.onchange=()=>{size.disabled=profile.targets[target.value]?.backend==='pyocd';armSize.disabled=!size.disabled;if(size.disabled){const bytes=profile.targets[target.value].size;armSize.value=String(bytes);armSize.textContent=String(bytes/1024)+' KiB';}size.value=size.disabled?armSize.value:String(4*1024*1024);};
      const files=document.createElement('input');files.type='file';files.multiple=true;files.accept='.bin,.hex,.elf,.zip';files.required=true;files.setAttribute('aria-label','外部固件文件');
      const manifest=document.createElement('input');manifest.value='flasher_args.json';manifest.setAttribute('aria-label','ZIP 清单路径');
      const note=document.createElement('input');note.required=true;note.placeholder='固件来源或用途';note.setAttribute('aria-label','固件来源或用途');note.maxLength=1000;
      const rows=make('div');
      let entries:{file:File;offset:HTMLInputElement;role:HTMLSelectElement}[]=[];
      files.onchange=()=>{
        rows.replaceChildren();entries=[];
        for(const file of Array.from(files.files||[])){
          const row=make('div','','flash-file-row'),label=make('span',file.name);label.translate=false;
          const offset=document.createElement('input');offset.placeholder='BIN 地址，如 0x08000000';offset.setAttribute('aria-label',file.name+' 烧录地址');offset.disabled=!file.name.toLowerCase().endsWith('.bin');
          const role=document.createElement('select');role.setAttribute('aria-label',file.name+' 镜像角色');
          for(const [id,title] of [['firmware','固件'],['bootloader','引导程序'],['application','应用'],['partition-table','分区表'],['data','数据'],['merged','合并镜像']]){const o=document.createElement('option');o.value=id;o.textContent=title;role.append(o);}
          row.append(label,offset,role);rows.append(row);entries.push({file,offset,role});
        }
      };
      const submit=make('button','导入并检查','button secondary') as HTMLButtonElement;submit.type='submit';
      form.append(make('label','目标芯片'),target,make('label','声明 Flash 容量'),size,make('label','选择固件文件'),files,rows,make('label','ZIP 清单路径'),manifest,note,submit);host.append(form);
      const list=make('div');host.append(make('h3','烧录包与计划历史'),list);
      const devices=make('div');host.append(devices);
      const showHistory=(data:any)=>{
        devices.replaceChildren();devicePanel(devices,project,data,current,profile.targets);
        list.replaceChildren();
        for(const bundle of data.bundles){
          const card=make('article','','source-record');const title=make('h4',bundle.name);title.translate=false;
          card.append(make('p',bundle.sourceKind==='internal-build'?'内部构建产物 · 已绑定版本与摘要':'外部固件 · 未验证构建来源','quiet-note'));
          card.append(title,make('p',`${bundle.request.target} · v${bundle.version} · ${bundle.images.length} files`),make('code',bundle.bundleSha256,'library-digest'));
          const table=document.createElement('table');
          for(const row of bundle.ranges){const tr=document.createElement('tr');tr.append(make('td',address(row.address)),make('td',row.size+' bytes'));table.append(tr);}card.append(table);
          const policy=document.createElement('input');policy.type='checkbox';
          const policyLabel=make('label','允许计划包含镜像外的扇区字节');policyLabel.prepend(policy);
          const reason=document.createElement('input');reason.placeholder='扩大擦除范围的原因';reason.setAttribute('aria-label','扩大擦除范围的原因');reason.maxLength=1000;
          const plan=make('button','生成离线计划','button secondary') as HTMLButtonElement;plan.type='button';
          plan.onclick=async()=>{
            plan.disabled=true;
            try{
              await api(`projects/${project}/flash-plans`,'POST',{bundleId:bundle.id,expectedBundleSha256:bundle.bundleSha256,uncoveredErasePolicy:policy.checked?'erase-declared-sectors':'reject',eraseReason:policy.checked?reason.value:null,requestId:requestId()});
              const updated=await api(`projects/${project}/flash-bundles`);if(current())showHistory(updated);
            }catch(error){if(current())status.textContent=(error as Error).message;}finally{plan.disabled=false;}
          };
          card.append(policyLabel,reason,plan);
          for(const result of data.plans.filter((p:any)=>p.bundleId===bundle.id)){
            const details=document.createElement('details');details.append(make('summary',result.checksPassed?'离线计划检查通过 · 未操作设备':'离线计划需处理'));
            const summary=make('div');
            summary.append(make('p','写入范围'));for(const r of result.writeRanges)summary.append(make('p',address(r.address)+' · '+r.size+' bytes'));
            summary.append(make('p','擦除范围'));for(const r of result.eraseRanges)summary.append(make('p',address(r.address)+' · '+r.size+' bytes'));
            const extra=make('p');extra.append(make('span','镜像外擦除字节'),document.createTextNode(': '+result.uncoveredEraseRanges.reduce((n:number,r:any)=>n+r.size,0)));summary.append(extra);
            for(const item of [...result.issues,...result.warnings])summary.append(make('p',item.message,'field-note'));
            const download=make('button','下载计划 JSON','text-button') as HTMLButtonElement;download.type='button';download.onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=result.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
            details.append(summary,download);card.append(details);
          }
          list.append(card);
        }
        if(!data.bundles.length)list.append(make('p','尚未导入外部固件。','quiet-note'));
      };
      showHistory(history);
      const builds=await api(`projects/${project}/flash-build-artifacts`);if(!current())return;
      const internal=make('section','','source-record');internal.append(make('h3','内部构建产物'));
      const choices=document.createElement('select');choices.setAttribute('aria-label','内部构建产物');
      for(const a of builds.artifacts){const o=document.createElement('option');o.value=a.id;o.textContent=`${a.name} · ${a.revisionId} · ${a.jobId}`;choices.append(o);}
      const review=document.createElement('input');review.placeholder='依赖已变化时的复核理由';review.setAttribute('aria-label','依赖复核理由');review.maxLength=1000;
      const bind=make('button','绑定构建产物','button secondary') as HTMLButtonElement;bind.disabled=!builds.artifacts.length;
      bind.onclick=async()=>{const a=builds.artifacts.find((a:any)=>a.id===choices.value);if(!a)return;bind.disabled=true;try{
        await api(`projects/${project}/flash-internal-bundles`,'POST',{name:a.name,revisionId:a.revisionId,buildJobId:a.jobId,artifactId:a.id,expectedSha256:a.sha256,dependencyReviewReason:review.value||null,requestId:requestId()});
        const updated=await api(`projects/${project}/flash-bundles`);if(current())showHistory(updated);
      }catch(error){if(current())status.textContent=(error as Error).message;}finally{bind.disabled=false;}};
      internal.append(choices,review,bind);host.insertBefore(internal,form);
      form.onsubmit=async(event)=>{
        event.preventDefault();
        const selected=entries.map(e=>({file:e.file,role:e.role.value,offset:e.offset.value}));
        const selectedTarget=target.value,selectedSize=Number(size.value),selectedManifest=manifest.value,selectedNote=note.value;
        const zipped=selected.length===1&&selected[0].file.name.toLowerCase().endsWith('.zip');
        if(selected.some(e=>e.file.name.toLowerCase().endsWith('.zip'))&&!zipped){status.textContent='ZIP 须单独导入。';return;}
        const inputs=Array.from(form.querySelectorAll<HTMLInputElement|HTMLSelectElement|HTMLButtonElement>('input,select,button'));inputs.forEach(e=>e.disabled=true);
        let activeUpload:string|null=null;
        try {
          const imported=[];
          for(const entry of selected){
            if(!current())return;
            const extension=entry.file.name.split('.').pop()!.toLowerCase();
            if(entry.file.size>64*1024*1024||!entry.file.size)throw new Error('固件为空或超过 64 MiB。');
            if(extension==='bin'&&!/^(0x[0-9a-f]+|[0-9]+)$/i.test(entry.offset))throw new Error('BIN 必须明确烧录地址。');
            const data=new Uint8Array(await entry.file.arrayBuffer());status.textContent='正在登记固件文件…';
            const upload=await api(`projects/${project}/source-transfers`,'POST',{action:'begin',name:entry.file.name,uri:'urn:formforge:external-firmware',version:'external-import',license:'User supplied; not independently verified',note:selectedNote,origin:'third-party-declared',size:data.length,sha256:await hash(data),requestId:requestId()});
            activeUpload=upload.id;
            for(let offset=0;offset<data.length;offset+=upload.maxChunkBytes){if(!current())return;const part=data.slice(offset,offset+upload.maxChunkBytes);await api(`projects/${project}/source-transfers`,'POST',{action:'append',uploadId:upload.id,offset,contentBase64:b64(part),sha256:await hash(part)});}
            if(!current())return;
            const done=await api(`projects/${project}/source-transfers`,'POST',{action:'commit',uploadId:upload.id});
            activeUpload=null;
            imported.push({source:done.source.bindings.externalManufacturing,format:extension,role:entry.role,address:extension==='bin'?Number(entry.offset):null});
          }
          if(!current())return;
          await api(`projects/${project}/flash-bundles`,'POST',{name:selected.map(e=>e.file.name).join(' + ').slice(0,120),target:selectedTarget,flashSize:profile.targets[selectedTarget]?.backend==='pyocd'?null:selectedSize,...(zipped?{archive:imported[0].source,manifest:selectedManifest}:{images:imported}),requestId:requestId()});
          const updated=await api(`projects/${project}/flash-bundles`);if(current()){status.textContent='外部固件已登记，未操作设备。';showHistory(updated);}
        }catch(error){if(current())status.textContent=(error as Error).message;}
        finally{if(activeUpload)try{await api(`projects/${project}/source-transfers`,'POST',{action:'abort',uploadId:activeUpload});}catch{/* The registry retains an incomplete upload if cleanup cannot reach the service. */}inputs.forEach(e=>e.disabled=false);size.disabled=profile.targets[target.value]?.backend==='pyocd';entries.forEach(e=>e.offset.disabled=!e.file.name.toLowerCase().endsWith('.bin'));}
      };
    }catch(error){if(current())status.textContent=(error as Error).message;}
  }
}
