import {translateText} from './i18n';

export function sourceMatches(text:string,query:string):number[]{
  if(!query)return [];
  const escaped=query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  return Array.from(text.matchAll(new RegExp(escaped,'gi')),match=>match.index!);
}

export function memoryUsage(used:unknown,capacity:number){
  if(typeof used!=='number'||!Number.isFinite(used)||used<0||!Number.isFinite(capacity)||capacity<=0)return null;
  return {used,capacity,percent:used/capacity*100,bar:Math.min(100,used/capacity*100)};
}

export class FirmwareSourceView {
  private text='';
  private name='';
  private matches:number[]=[];
  private current=-1;
  readonly host=document.createElement('div');
  private pre=document.createElement('pre');
  private code=document.createElement('code');
  private numbers=document.createElement('div');
  private query=document.createElement('input');
  private status=document.createElement('span');
  private copy=document.createElement('button');
  private download=document.createElement('button');
  constructor(){
    this.host.className='firmware-source-view';
    const tools=document.createElement('div');tools.className='firmware-code-tools';
    this.query.type='search';this.query.placeholder='搜索源码…';this.query.setAttribute('aria-label','搜索源码');
    this.status.className='firmware-find-status';this.status.setAttribute('role','status');
    const button=(title:string,action:()=>void)=>{const b=document.createElement('button');b.type='button';b.textContent=title;b.onclick=action;return b;};
    const previous=button('上一处',()=>this.move(-1)),next=button('下一处',()=>this.move(1));
    this.copy=button('复制源码',()=>{void navigator.clipboard.writeText(this.text).then(()=>this.status.textContent='已复制源码').catch(()=>this.status.textContent='复制失败，请选择源码后复制。');});
    this.download=button('导出源码文本',()=>{const url=URL.createObjectURL(new Blob([this.text],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=this.name.replaceAll('/','_');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    const wrap=button('自动换行',()=>{const enabled=wrap.getAttribute('aria-pressed')!=='true';wrap.setAttribute('aria-pressed',String(enabled));this.pre.classList.toggle('wrap',enabled);this.numbers.hidden=enabled;});wrap.setAttribute('aria-pressed','false');
    tools.append(this.query,previous,next,this.status,wrap,this.copy,this.download);
    this.pre.className='firmware-code';this.pre.tabIndex=0;this.pre.setAttribute('aria-label','应用源码内容');
    this.code.translate=false;this.numbers.className='firmware-line-numbers';this.numbers.setAttribute('aria-hidden','true');
    this.pre.append(this.numbers,this.code);this.host.append(tools,this.pre);
    this.query.oninput=()=>{this.matches=sourceMatches(this.text,this.query.value);this.current=this.matches.length?0:-1;this.paint();};
    this.query.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();this.move(e.shiftKey?-1:1);}if(e.key==='Escape'){this.query.value='';this.query.dispatchEvent(new Event('input'));}};
    this.setLoading();
  }
  setLoading(){this.text='';this.query.value='';this.query.disabled=true;this.copy.disabled=true;this.download.disabled=true;this.code.textContent=translateText('正在读取…');this.numbers.textContent='';this.status.textContent='';this.host.dataset.ready='false';}
  setError(message:string){this.code.textContent=message;}
  setSource(name:string,text:string){this.name=name;this.text=text;this.matches=[];this.current=-1;this.query.disabled=false;this.copy.disabled=false;this.download.disabled=false;this.host.dataset.ready='true';this.pre.scrollTop=0;this.pre.scrollLeft=0;this.paint();}
  private move(direction:number){if(!this.matches.length)return;this.current=(this.current+direction+this.matches.length)%this.matches.length;this.paint();}
  private paint(){
    const fragment=document.createDocumentFragment();
    if(this.query.value){
      let offset=0;for(const [index,start] of this.matches.entries()){
        fragment.append(document.createTextNode(this.text.slice(offset,start)));
        const mark=document.createElement('mark');mark.textContent=this.text.slice(start,start+this.query.value.length);if(index===this.current)mark.className='current';fragment.append(mark);offset=start+this.query.value.length;
      }fragment.append(document.createTextNode(this.text.slice(offset)));
      this.status.textContent=this.matches.length?`${this.current+1} / ${this.matches.length}`:'没有匹配的源码';
    }else{
      // Render only text nodes, never interpret source code as HTML.
      const tokens=/(\/\*[\s\S]*?\*\/|\/\/[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|^\s*#[^\n]*|\b(?:void|int|char|unsigned|static|const|volatile|return|if|else|for|while|struct|typedef|enum|sizeof|uint\d+_t)\b|\b(?:0x[\da-fA-F]+|\d+)\b)/gm;
      let offset=0;for(const match of this.text.matchAll(tokens)){
        fragment.append(document.createTextNode(this.text.slice(offset,match.index)));
        const span=document.createElement('span'),token=match[0];span.textContent=token;
        span.className=token.startsWith('//')||token.startsWith('/*')?'source-comment':token.trimStart().startsWith('#')?'source-directive':/^['"]/.test(token)?'source-string':/^\d/.test(token)?'source-number':'source-keyword';fragment.append(span);offset=match.index!+token.length;
      }fragment.append(document.createTextNode(this.text.slice(offset)));this.status.textContent='';
    }
    this.code.replaceChildren(fragment);this.numbers.textContent=Array.from({length:this.text.split('\n').length},(_,i)=>String(i+1)).join('\n');
    const current=this.code.querySelector<HTMLElement>('mark.current');if(current)this.pre.scrollTop=current.offsetTop-this.pre.clientHeight/2;
  }
}
