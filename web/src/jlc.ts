import {api,fileUrl,requestId,type Context,type Job,type Artifact} from './api';
import {locale} from './i18n';

const make=(tag:string,text='',cls='')=>{const e=document.createElement(tag);e.textContent=text;e.className=cls;return e;};
const label=(text:string,field:HTMLElement)=>{const e=make('label',text,'field-label');e.append(field);return e;};
const when=(value:string)=>new Date(value).toLocaleString(locale(),{hour12:false});

export class JlcPanel{
  private generation=0;
  private settings:HTMLDetailsElement|null=null;
  private selected=new Map<string,string>();
  private following=new Map<string,boolean>();
  private drafts=new Map<string,{order:string;date:string;type:string;unit:string}>();
  constructor(private host:HTMLElement,private refresh:()=>Promise<void>,private notify:(message:string,error?:boolean)=>void,private openOrders:()=>void){}

  async render(context:Context|null){
    const generation=++this.generation;
    if(!context){this.host.replaceChildren(make('p','请先选择工程。','empty-small'));return;}
    const pid=context.project.id;
    try{
      const [read,orders]=await Promise.all([api(`projects/${pid}/jlc`),api(`projects/${pid}/orders?limit=100`)]);
      if(generation!==this.generation)return;
      const profile=read.profile,following=this.following.get(pid)!==false;
      let chosen:Job|undefined=following?read.jobs[0]:read.jobs.find((j:Job)=>j.id===this.selected.get(pid))||read.jobs[0];
      let data=read;
      if(chosen&&chosen.id!==read.job?.id){data=await api(`projects/${pid}/jlc?jobId=${chosen.id}`);if(generation!==this.generation)return;}
      if(chosen)this.selected.set(pid,chosen.id);
      this.host.replaceChildren(make('h2','嘉立创官方接口'),make('p','获取工艺计价、核对已经存在的订单。不会创建第二张订单、付款或放行生产。','field-note'));
      this.host.append(make('p',profile.configured?`本机加密配置已保存 · ${profile.applicationName}`:'请先配置本机应用，密钥只保存在此电脑。','notice'),
        make('p',profile.authenticated?`最近一次接口验证：${when(profile.lastVerifiedAt)}`:'尚未完成真实接口验证。','quiet-note'));
      if(profile.issue)this.host.append(make('p',profile.issue,'connection-problem'));
      if(!this.settings)this.settings=this.settingsForm(profile,context);
      this.host.append(this.settings,this.syncForm(context,profile,orders.orders.filter((o:any)=>o.identity.provider==='jlc-pcb')));
      this.host.append(make('h3','计价与查单记录'),make('p','把明确的数量和工艺告诉你的 Agent，计价返回后会显示在这里。原文件与既有订单金额各自保留。','quiet-note'));
      const bar=make('div','','manufacturing-row'),select=document.createElement('select');select.setAttribute('aria-label','官方接口任务');
      for(const job of read.jobs){const option=document.createElement('option');option.value=job.id;option.textContent=`${job.kind==='manufacturing.jlc_quote'?'工艺计价':'核对订单'} · ${job.id.slice(-6)} · ${job.status==='completed'?'已完成':job.status==='running'?'执行中':job.status==='queued'?'排队中':job.status==='cancelled'?'已取消':'需处理'}`;select.append(option);}
      select.disabled=!chosen;if(chosen)select.value=chosen.id;
      select.onchange=()=>{this.selected.set(pid,select.value);this.following.set(pid,false);void this.render(context);};bar.append(select);
      const follow=make('button',following?'正在跟随新任务':'跟随最新任务','text-button') as HTMLButtonElement;follow.onclick=()=>{this.following.set(pid,true);void this.render(context);};bar.append(follow);this.host.append(bar);
      if(!chosen){this.host.append(make('p','还没有官方接口任务。','empty-small'));return;}
      this.host.append(make('p',data.job.phase,'field-note'));
      if(data.job.error)this.host.append(make('p',data.job.error,'connection-problem'));
      if(data.diagnostic){
        const details=make('details');details.append(make('summary','本次接口诊断'));
        details.append(make('p','此记录用于定位失败，不代表已核对订单；原请求和响应正文未保存。','quiet-note'));
        for(const attempt of data.diagnostic.attempts){
          details.append(make('p',`${attempt.path} · HTTP ${attempt.httpStatus??'未取得'} · 业务码 ${attempt.businessCode??'未取得'}`,'field-note'),
            make('p',`时间：${when(attempt.startedAt)} 至 ${when(attempt.finishedAt)}`,'field-note'),
            make('p',`平台追踪号：${attempt.traceId??'未取得或已排除'}`,'field-note'),
            make('p',`请求 SHA-256：${attempt.requestSha256}`,'field-note'),
            make('p',attempt.responseComplete?`完整响应 SHA-256：${attempt.responseSha256}`:'未取得完整响应，未生成响应摘要。','field-note'));
        }
        this.host.append(details);
      }
      if(data.requiresContextReview)this.host.append(make('p','此记录形成后需求或偏好已变化；它仍属于当时的输入。','connection-problem'));
      if(data.result){
        const result=data.result;
        if(chosen.kind==='manufacturing.jlc_quote'){
          this.host.append(make('p',`官方工艺计价返回金额：${result.amount} 元`,'notice'),
            make('p','运费尚未明确；原件未上传审核，这不是付款记录。','quiet-note'),make('p',result.scope,'quiet-note'));
          const details=make('details');details.append(make('summary','本次费用字段与服务项'));
          for(const [key,value] of Object.entries(result.fields||{}))details.append(make('p',`${key}：${value??'未明确'}`,'field-note'));
          for(const service of result.services||[])details.append(make('p',`${service.serviceName} · ${service.serviceMoney??'未明确'} 元`,'field-note'));
          this.host.append(details);
        }else{
          this.host.append(make('p',`已核对现有订单并追加第 ${result.orderVersion} 次观察。`,'notice'));
          const view=make('button','查看订单与证据历史','text-button') as HTMLButtonElement;view.onclick=this.openOrders;this.host.append(view);
        }
      }
      const docs=make('div','','document-downloads');for(const artifact of data.artifacts as Artifact[]){const link=make('a',artifact.kind==='jlc_api_diagnostic'?'下载本次失败诊断 ↗':'下载本次接口回执 ↗','text-button') as HTMLAnchorElement;link.href=fileUrl(artifact)+'?download=true';docs.append(link);}this.host.append(docs);
    }catch(error){if(generation===this.generation)this.host.replaceChildren(make('p',(error as Error).message,'connection-problem'));}
  }

  private settingsForm(profile:any,context:Context){
    const details=make('details') as HTMLDetailsElement;details.open=!profile.configured;details.append(make('summary','本机应用配置'));
    const form=document.createElement('form');form.className='manufacturing-form';form.autocomplete='off';
    const input=(name:string,type='text')=>{const field=document.createElement('input');field.type=type;field.required=true;field.name=name;field.autocomplete='off';field.spellcheck=false;return field;};
    const name=input('applicationName'),app=input('appId'),access=input('accessKey','password'),secret=input('secretKey','password');
    name.value=profile.applicationName||'';name.maxLength=80;app.inputMode='numeric';app.pattern='[1-9][0-9]{0,29}';app.maxLength=30;access.maxLength=200;secret.maxLength=512;
    form.append(label('应用名称',name),label('应用编号（AppID）',app),label('访问标识（AccessKey）',access),label('密钥（SecretKey）',secret),
      make('p','从已授权的嘉立创应用取得这些信息。保存后按当前 Windows 账号加密，网页不提供密钥回读。不要把密钥发送到聊天。','quiet-note'));
    const save=make('button','加密保存本机配置','primary-button') as HTMLButtonElement;save.type='submit';save.disabled=!profile.configurationAvailable;form.append(save);
    form.onsubmit=async event=>{event.preventDefault();save.disabled=true;
      try{
        await api('jlc-configuration','PUT',{expectedConfigVersion:profile.configVersion,applicationName:name.value,appId:app.value,accessKey:access.value,secretKey:secret.value});
        access.value='';secret.value='';form.reset();this.settings=null;
        this.notify('本机加密配置已保存。下一次真实请求会验证接口。');await this.refresh();
      }catch(error){this.notify((error as Error).message,true);save.disabled=!profile.configurationAvailable;}
    };
    const reset=make('button','清空输入并重读配置','text-button') as HTMLButtonElement;reset.type='button';reset.onclick=()=>{access.value='';secret.value='';this.settings=null;void this.render(context);};form.append(reset);
    details.append(form);return details;
  }

  private syncForm(context:Context,profile:any,orders:any[]){
    const pid=context.project.id,draft=this.drafts.get(pid)||{order:'',date:'',type:'',unit:''};this.drafts.set(pid,draft);
    const details=make('details') as HTMLDetailsElement;details.open=!!orders.length;details.append(make('summary','核对已存在的订单'));
    if(!orders.length){details.append(make('p','先把已经在平台提交的订单关联到此工程。此处不会创建新订单。','quiet-note'));return details;}
    const form=document.createElement('form');form.className='manufacturing-form';
    const choose=(title:string,values:[string,string][],value:string,changed:(v:string)=>void)=>{const select=document.createElement('select');select.required=true;for(const [id,text] of values){const option=document.createElement('option');option.value=id;option.textContent=text;select.append(option);}select.value=value;select.onchange=()=>changed(select.value);form.append(label(title,select));return select;};
    const selected=choose('已关联订单',[['','请选择'],...orders.map(o=>[o.id,o.identity.displayNumber] as [string,string])],draft.order,v=>draft.order=v);
    const date=document.createElement('input');date.type='date';date.required=true;date.value=draft.date;date.oninput=()=>draft.date=date.value;form.append(label('平台下单日期（中国时间）',date));
    const type=choose('订单类别',[['','请选择'],['examples','样板'],['batch','小批量']],draft.type,v=>draft.type=v);
    const unit=choose('接口尺寸单位',[['','请选择'],['cm','厘米'],['mm','毫米']],draft.unit,v=>draft.unit=v);
    form.append(make('p','按平台下单日期核对同编号订单。查询文档没有注明尺寸单位，需按实际回执明确选择；与原文件不符会停止关联。','quiet-note'));
    const submit=make('button','核对现有订单','primary-button') as HTMLButtonElement;submit.type='submit';submit.disabled=!profile.configured||context.jobs.some(j=>['queued','running'].includes(j.status));form.append(submit);
    form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;
      try{
        const order=orders.find(o=>o.id===selected.value);if(!order)throw new Error('请先选择本工程订单。');
        const job=await api(`projects/${pid}/jlc-order-syncs`,'POST',{orderId:order.id,expectedOrderVersion:order.version,
          orderType:type.value,orderDateBegin:date.value+' 00:00:00',orderDateEnd:date.value+' 23:59:59',responseLengthUnit:unit.value,requestId:requestId()});
        this.selected.set(pid,job.id);this.following.set(pid,true);this.notify('开始核对现有订单。');await this.refresh();
      }catch(error){this.notify((error as Error).message,true);submit.disabled=!profile.configured;}
    };
    details.append(form);return details;
  }
}
