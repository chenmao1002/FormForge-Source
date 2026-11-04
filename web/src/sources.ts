import {api,requestId,type Project} from './api';

const el=<T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as T;
const field=(id:string)=>el<HTMLInputElement>(id);
const make=(tag:string,text:string,cls='')=>{const node=document.createElement(tag);node.textContent=text;node.className=cls;return node;};
const hex=(data:ArrayBuffer)=>Array.from(new Uint8Array(data),b=>b.toString(16).padStart(2,'0')).join('');
const hash=async(data:ArrayBuffer)=>hex(await crypto.subtle.digest('SHA-256',data));
const base64=(data:Uint8Array)=>{let text='';for(let i=0;i<data.length;i+=8192)text+=String.fromCharCode(...data.subarray(i,i+8192));return btoa(text);};

export class SourcesPanel {
  private offset=0;
  private project='';
  private generation=0;
  private uploading=false;
  private stop=false;
  private pending:{project:string;requestId:string;fingerprint:string;uploadId?:string}|null=null;

  constructor(private current:()=>Project|undefined,private notify:(message:string,error?:boolean)=>void,private changed:()=>Promise<void>){
    el('source-add').onclick=()=>{const p=this.current();if(!p)return;el('source-project').textContent='添加到：'+p.name;el<HTMLDialogElement>('source-dialog').showModal();};
    el<HTMLFormElement>('source-form').onsubmit=event=>{event.preventDefault();void this.upload();};
    document.querySelectorAll<HTMLButtonElement>('.source-close').forEach(button=>button.onclick=()=>void this.close());
    el<HTMLDialogElement>('source-dialog').addEventListener('cancel',event=>{event.preventDefault();void this.close();});
    el('source-previous').onclick=()=>{this.offset=Math.max(0,this.offset-40);void this.render().catch(error=>this.notify(error.message,true));};
    el('source-next').onclick=()=>{this.offset+=40;void this.render().catch(error=>this.notify(error.message,true));};
  }

  async render(){
    const p=this.current(),generation=++this.generation;
    field('source-add').disabled=!p;
    if(this.project!==p?.id){this.project=p?.id||'';this.offset=0;}
    const host=el('source-list');
    if(!p){host.replaceChildren(make('p','创建或选择工程后添加资料。','empty-small'));return;}
    const result=await api<any>(`projects/${p.id}/sources?offset=${this.offset}&limit=40`);
    if(generation!==this.generation||this.current()?.id!==p.id)return;
    host.replaceChildren();
    el('source-count').textContent=result.total+' 份资料';
    for(const source of result.sources){
      const card=make('article','','source-record');
      const title=make('div','','card-heading');title.append(make('h3',source.name),make('span',source.origin==='project-authored'?'项目编写':source.origin==='supplier-declared'?'供应商资料 · 声明':'第三方资料 · 声明','small-tag'));
      const details=make('details','','source-details');details.append(make('summary','来源与摘要'),make('p',source.uri),make('p','许可：'+source.license),make('code',source.sha256,'library-digest'));
      const link=make('a','下载原始文件 ↗','text-button') as HTMLAnchorElement;link.href=source.downloadUrl;link.download=source.name;
      card.append(title,make('p',`版本 ${source.version} · ${source.size.toLocaleString()} 字节`,'field-note'),make('p',source.note),details,link);host.append(card);
    }
    if(!result.total)host.append(make('p','还没有登记资料。可以添加器件手册、接口尺寸、符号库或固件源文件，Agent 也可通过 MCP 提交。','empty-small'));
    el('source-page').textContent=result.total?`${this.offset+1}–${this.offset+result.sources.length} / ${result.total}`:'';
    field('source-previous').disabled=this.offset===0;field('source-next').disabled=result.nextOffset===null;
  }

  private async close(){
    if(this.uploading){
      this.stop=true;
      const pending=this.pending;
      if(pending?.uploadId){
        try{await api(`projects/${pending.project}/source-transfers`,'POST',{action:'abort',uploadId:pending.uploadId});}
        catch(error){this.notify((error as Error).message,true);return;}
      }
      el('source-progress').textContent='正在结束上传…';
    }else el<HTMLDialogElement>('source-dialog').close();
  }

  private async upload(){
    const p=this.current(),file=field('source-file').files?.[0];if(!p||!file||this.uploading)return;
    if(!file.size||file.size>64*1024*1024){this.notify('请选择不超过64MiB的非空文件。',true);return;}
    this.uploading=true;this.stop=false;field('source-submit').disabled=true;
    el('source-form').querySelectorAll<HTMLInputElement>('input,select,textarea').forEach(field=>field.disabled=true);
    try{
      const metadata={name:file.name,uri:field('source-uri').value,version:field('source-version').value,license:field('source-license').value,note:field('source-note').value,origin:field('source-origin').value};
      el('source-progress').textContent='正在核对文件摘要…';
      const buffer=await file.arrayBuffer(),sha256=await hash(buffer),fingerprint=JSON.stringify([p.id,metadata,sha256]);
      if(this.stop){el<HTMLDialogElement>('source-dialog').close();return;}
      if(this.pending?.fingerprint!==fingerprint)this.pending={project:p.id,requestId:requestId(),fingerprint};
      const pending=this.pending!;
      let state=await api<any>(`projects/${p.id}/source-transfers`,'POST',{action:'begin',...metadata,size:file.size,sha256,requestId:pending.requestId});
      pending.uploadId=state.id;
      if(state.status==='aborted'){this.pending=null;throw new Error('上次上传已取消，请重新登记。');}
      for(let offset=state.receivedBytes;state.status==='uploading'&&offset<file.size;){
        if(this.stop){await api(`projects/${p.id}/source-transfers`,'POST',{action:'abort',uploadId:state.id});this.pending=null;el<HTMLDialogElement>('source-dialog').close();return;}
        const bytes=buffer.slice(offset,Math.min(offset+state.maxChunkBytes,file.size));
        state=await api<any>(`projects/${p.id}/source-transfers`,'POST',{action:'append',uploadId:state.id,offset,contentBase64:base64(new Uint8Array(bytes)),sha256:await hash(bytes)});
        offset=state.receivedBytes;el('source-progress').textContent=`已接收 ${offset.toLocaleString()} / ${file.size.toLocaleString()} 字节`;
      }
      if(this.stop){await api(`projects/${p.id}/source-transfers`,'POST',{action:'abort',uploadId:state.id});this.pending=null;el<HTMLDialogElement>('source-dialog').close();return;}
      if(state.status!=='committed')state=await api<any>(`projects/${p.id}/source-transfers`,'POST',{action:'commit',uploadId:state.id});
      this.pending=null;el<HTMLDialogElement>('source-dialog').close();el<HTMLFormElement>('source-form').reset();el('source-progress').textContent='';this.notify('资料已登记，原始文件与来源已保存。');await this.changed();
    }catch(error){if(this.stop){this.pending=null;el<HTMLDialogElement>('source-dialog').close();}else{el('source-progress').textContent=(error as Error).message+' 可再次点击登记以重试。';this.notify((error as Error).message,true);}}
    finally{this.uploading=false;field('source-submit').disabled=false;el('source-form').querySelectorAll<HTMLInputElement>('input,select,textarea').forEach(field=>field.disabled=false);}
  }
}
