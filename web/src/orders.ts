import {api,type Context} from './api';
import {locale} from './i18n';

const make=(tag:string,content='',cls='')=>{const e=document.createElement(tag);e.textContent=content;e.className=cls;return e;};
const when=(date:string)=>new Date(date).toLocaleString(locale(),{hour12:false});
const amount=(value:string|null|undefined)=>value==null?'待核价':value+' 元';
const stage:Record<string,string>={unknown:'状态未明确',submitted:'已提交','under-review':'等待平台审核',returned:'平台退回',approved:'订单审核通过',cancelled:'已取消','awaiting-payment':'平台显示待付款','unexpected-payment-or-production':'发现支付或生产状态，停止后续动作'};
const verification:Record<string,string>={unknown:'未明确',pending:'待确认',approved:'已确认',rejected:'未通过'};
const PAGE=40;

export class OrdersPanel{
  private generation=0;
  private selected=new Map<string,string>();
  private pages=new Map<string,{offset:number;limit:number}>();
  private listOffsets=new Map<string,number>();
  private expanded=new Map<string,boolean>();
  constructor(private host:HTMLElement,private notify:(message:string,error?:boolean)=>void){}

  async render(context:Context|null){
    const generation=++this.generation;
    if(!context){this.host.replaceChildren(make('p','请先选择工程。','empty-small'));return;}
    const pid=context.project.id,offset=this.listOffsets.get(pid)||0;
    try{
      const list=await api(`projects/${pid}/orders?offset=${offset}&limit=${PAGE}`);
      if(generation!==this.generation)return;
      const chosen=list.orders.find((o:any)=>o.id===this.selected.get(pid))||list.orders[0];
      let detail:any;
      if(chosen){
        const page=this.pages.get(chosen.id);
        const query=page?`offset=${page.offset}&limit=${page.limit}`:`latest=true&limit=${PAGE}`;
        detail=await api(`projects/${pid}/orders?orderId=${chosen.id}&${query}`);
        if(generation!==this.generation)return;
        this.selected.set(pid,chosen.id);
      }
      this.host.replaceChildren(make('h2','平台订单记录'),make('p','查看已关联订单的本地记录。刷新本地记录不会查询嘉立创。','field-note'));
      const toolbar=make('div','','manufacturing-row order-toolbar'),select=document.createElement('select');
      select.setAttribute('aria-label','平台订单');select.disabled=!chosen;
      for(const order of list.orders){const option=document.createElement('option');option.value=order.id;option.textContent=`${order.identity.provider==='jlc-smt'?'SMT':'PCB'} · ${order.identity.displayNumber} · ${stage[order.stage]||order.stage}`;select.append(option);}
      if(chosen)select.value=chosen.id;
      select.onchange=()=>{this.selected.set(pid,select.value);void this.render(context);};toolbar.append(select);
      toolbar.append(this.button('刷新本地记录',()=>void this.render(context)));this.host.append(toolbar);
      if(list.total>PAGE){
        const pager=make('div','','manufacturing-row');
        for(const [label,next] of [['上一页订单',Math.max(0,offset-PAGE)],['下一页订单',offset+PAGE]] as [string,number][]){
          pager.append(this.button(label,()=>{this.listOffsets.set(pid,next);void this.render(context);},next===offset||next>=list.total));
        }
        this.host.append(pager);
      }
      if(!detail){this.host.append(make('p','还没有关联订单。Agent 可通过 MCP 关联已有订单，原始制造文件和平台证据会一起保留。','empty-small'));return;}
      const order=detail.order,obs=order.latestObservation,smt=order.identity.provider==='jlc-smt';
      const heading=make('div','','card-heading order-heading');
      heading.append(make('strong',`嘉立创 ${smt?'SMT':'PCB'} · ${order.identity.displayNumber}`),make('span',stage[order.stage]||order.stage,'badge '+(order.actionHalted?'failed':'neutral')));this.host.append(heading);
      this.host.append(make('p',`观察于 ${when(obs.observedAt)} · 本地订单版本 ${order.version}`,'field-note'));
      if(order.actionHalted)this.host.append(make('p','历史观察出现已支付或生产状态，已保留该事实并停止后续动作。','connection-problem'));
      if(order.sourceRequiresReview)this.host.append(make('p','设计、采购或需求已变化；此订单仍绑定原始资料，请核对差异。','connection-problem'));
      if(smt)this.renderSmt(order);else this.renderPcb(order);
      this.host.append(make('p',`${obs.providerStatusLabel} · ${obs.paymentStatusLabel} · ${obs.productionStatusLabel}`,'order-provider-label'));
      const notes=this.details(order.id,'notes','本次观察说明');notes.append(make('p',obs.note));this.host.append(notes);
      this.renderFiles(order,pid);
      const official=this.link(`打开嘉立创 ${smt?'SMT':'PCB'} 订单列表 ↗`,smt?'https://member.jlc.com/center/smt/home/order/smt_list/':'https://www.jlc.com/newOrder/#/pcb/pcbOrderList');
      official.target='_blank';official.rel='noopener noreferrer';this.host.append(official);
      this.host.append(make('p',obs.evidenceLevel==='provider-api-https-response'?'来源：本机服务核对的官方 HTTPS 回执。未验证平台原始文件字节或回执签名。':'来源：Agent 记录的浏览器观察，未验证平台原始文件字节。','field-note'),make('p','订单审核和器件匹配不代表 DFM、方向或生产批准；此页面不执行下单、付款或放行。','field-note'));
      this.renderHistory(detail,pid,context);
    }catch(error){if(generation===this.generation){this.host.replaceChildren(make('p',(error as Error).message,'connection-problem'));this.notify((error as Error).message,true);}}
  }

  private button(label:string,action:()=>void,disabled=false){
    const button=make('button',label,'text-button') as HTMLButtonElement;button.type='button';button.disabled=disabled;button.onclick=action;return button;
  }

  private link(label:string,url:string){const link=make('a',label,'text-button') as HTMLAnchorElement;link.href=url;return link;}

  private details(order:string,key:string,title:string){
    const details=make('details','','order-details') as HTMLDetailsElement,identity=order+':'+key;
    details.open=this.expanded.get(identity)||false;details.append(make('summary',title));
    details.ontoggle=()=>{if(details.isConnected)this.expanded.set(identity,details.open);};return details;
  }

  private renderSmt(order:any){
    const obs=order.latestObservation,p=order.parameters,b=order.sourceBinding.procurement;
    this.host.append(make('p',`PCB ${order.pcbOrderNumber} · ${p.assemblyQuantity} 片 · ${p.assembledSides.map((s:string)=>s==='top'?'顶层':'底层').join('＋')} · ${p.productType==='standard'?'标准型':p.productType==='economy'?'经济型':'类型未明确'} · ${b.referenceCount} 个位号（含 ${b.throughHoleReferences.length} 个插件）`,'order-parameters'));
    const status=make('div','','order-status-grid');status.setAttribute('aria-label','SMT 状态');
    for(const [label,value] of [['订单审核',obs.reviewStatus==='approved'?'通过':stage[obs.reviewStatus]],['DFM',verification[obs.dfmStatus]],['方向校验',verification[obs.directionStatus]],['支付',obs.paymentStatus==='paid'?'已支付':obs.paymentStatus==='unpaid'?'未支付':'未明确']]){
      const item=make('div');item.append(make('span',label),make('strong',value));status.append(item);
    }
    this.host.append(status);
    const price=obs.prices||order.lastPriceObservation?.prices;
    if(price){
      const cost=make('div','','order-cost');cost.append(make('span',price.kind==='estimate'?'平台估算':'金额观察'),make('strong',amount(price.amount)),make('span',price.amountScope));
      this.host.append(cost,make('p',obs.prices?`运费：${amount(price.shippingAmount)}。${price.note}`:`本次未提供金额；以上为 ${when(order.lastPriceObservation.observedAt)} 的观察。运费：${amount(price.shippingAmount)}。${price.note}`,'field-note'));
    }
    const matching=obs.bomMatchingStatus==='unknown'?order.lastMatchingObservation:{status:obs.bomMatchingStatus,matchedReferences:obs.matchedReferences,checkedReferences:obs.checkedReferences,observedAt:obs.observedAt};
    this.host.append(make('p',matching?`${obs.bomMatchingStatus==='unknown'?'此前器件匹配':'器件匹配'}：${matching.status==='complete'?'完整':'部分'} · 匹配 ${matching.matchedReferences.length}/${b.referenceCount} · 勾选 ${matching.checkedReferences.length}/${b.referenceCount}（${when(matching.observedAt)}）`:'器件匹配：本次尚未观察。','field-note'));
    this.host.append(make('p',`DFM：${obs.manualDfmConfirmation==null?'本次未明确确认方式':obs.manualDfmConfirmation?'手动确认':'允许自动确认'} · 方向服务：${obs.directionService==='engineer-assistance'?'工程师协助（不代表已完成）':obs.directionService==='self-reviewed'?'自行校验':'未明确'}`,'field-note'));
    const notes=this.details(order.id,'parameters','贴装范围与备注');notes.append(make('p',p.note),make('p',`${b.referenceCount} 个位号 · ${b.bomLineCount} 项原始 BOM · 插件 ${b.throughHoleReferences.join('、')||'无'}`),make('p','关联 PCB 单号来自登记的网页观察；当前官方查单入口仅支持 PCB。'));this.host.append(notes);
  }

  private renderPcb(order:any){
    const obs=order.latestObservation,p=order.parameters,b=order.sourceBinding;
    this.host.append(make('p',`${p.copperLayers} 层 · ${p.widthMm} × ${p.heightMm} mm · ${p.boardQuantity} 片裸板 · ${p.thicknessMm} mm`,'order-parameters'));
    const confirmation=(key:string)=>{const value=obs[key];if(value!=null)return value?'手动':'非手动';const previous=order.lastManualConfirmation?.[key];return previous?`本次未提供；此前${previous.value?'手动':'非手动'}（${when(previous.observedAt)}）`:'未明确';};
    this.host.append(make('p',`订单确认：${confirmation('manualOrderConfirmation')} · 生产稿确认：${confirmation('manualProductionProof')}`,'field-note'));
    const price=obs.prices||order.lastPriceObservation?.prices;
    if(price)this.host.append(make('p',`${price.kind==='estimate'?'平台页面估算':'记录的正式报价'} · PCB ${amount(price.boardAmount)} · 运费 ${amount(price.shippingAmount)} · 总额 ${amount(price.totalAmount)}`),make('p',obs.prices?price.note:`本次未提供金额；此前观察于 ${when(order.lastPriceObservation.observedAt)}。${price.note}`,'field-note'));
    const notes=this.details(order.id,'parameters','PCB 参数与备注');notes.append(make('p',p.note),make('p',b.parameterVerification==='external-cam-layer-count-and-outline'?'层数和外形已与本地 CAM 报告核对；板厚和数量为本次订单的明确约定。':'已核对制造任务明确的数量与板厚约定，外形和层数需另审。'));this.host.append(notes);
  }

  private renderFiles(order:any,pid:string){
    const b=order.sourceBinding,docs=make('div','','document-downloads');
    const details=this.details(order.id,'files','原始资料、版本与摘要');
    const files:[string,any][]=[['原始 Gerber',b.gerber]];
    if(order.identity.provider==='jlc-smt')files.push(['原始 BOM',b.procurement.bom],['原始坐标 CPL',b.procurement.cpl]);
    for(const [label,file] of files){
      const url=file.artifactId?`/api/artifacts/${encodeURIComponent(file.artifactId)}/file?download=true`:`/api/projects/${pid}/sources/${file.source.sourceId}/file?download=true`;
      docs.append(this.link(`下载${label} ↗`,url));details.append(make('code',`${label} SHA-256 ${file.sha256}`,'library-digest'));
    }
    details.append(make('p',`制造任务 ${b.manufacturingJobId} · ${b.inputRevision||'独立制造文件，无 CAD 版本'}`));
    if(b.procurement)details.append(make('p',`采购版本 ${b.procurement.revisionId} · 任务 ${b.procurement.jobId}`));
    this.host.append(docs,details);
  }

  private renderHistory(detail:any,pid:string,context:Context){
    const order=detail.order,smt=order.identity.provider==='jlc-smt',following=!this.pages.has(order.id);
    const history=this.details(order.id,'history',`观察历史与证据 · ${detail.total} 次`),toolbar=make('div','','manufacturing-row order-history-toolbar');
    toolbar.append(make('span',`第 ${detail.offset+1}–${detail.offset+detail.observations.length} 条，共 ${detail.total} 条`),this.button(following?'正在跟随最新':'跟随最新观察',()=>{this.pages.delete(order.id);void this.render(context);},following));history.append(toolbar);
    for(const row of [...detail.observations].reverse()){
      const obs=row.observation,item=make('section','','order-observation');item.append(make('h3',`记录 ${row.orderVersion} · ${when(obs.observedAt)}`),make('p',obs.evidenceLevel==='provider-api-https-response'?'官方 HTTPS 接口 · 本机服务核对':'浏览器观察 · Agent 记录','field-note'),make('p',`${obs.providerStatusLabel} · ${obs.paymentStatusLabel} · ${obs.productionStatusLabel}`),make('p',obs.note));
      if(smt)item.append(make('p',`订单审核：${obs.reviewStatus==='approved'?'通过':stage[obs.reviewStatus]} · DFM：${verification[obs.dfmStatus]} · 方向：${verification[obs.directionStatus]}`));
      const docs=make('div','','document-downloads');for(const source of row.evidence)docs.append(this.link(`${source.name} ↗`,`/api/projects/${pid}/sources/${source.id}/file?download=true`));
      const digest=this.details(order.id,'digest:'+row.id,'记录摘要');digest.append(make('code',row.sha256,'library-digest'));item.append(docs,digest);history.append(item);
    }
    if(detail.total>PAGE){
      const pager=make('div','','manufacturing-row');
      pager.append(this.button('较早记录',()=>{this.pages.set(order.id,{offset:Math.max(0,detail.offset-PAGE),limit:Math.min(PAGE,detail.offset)});void this.render(context);},detail.offset===0));
      const next=detail.offset+detail.limit;
      pager.append(this.button('较新记录',()=>{if(next>=detail.total-PAGE)this.pages.delete(order.id);else this.pages.set(order.id,{offset:next,limit:PAGE});void this.render(context);},next>=detail.total));history.append(pager);
    }
    this.host.append(history);
  }
}
