import {api,type Context,type Revision} from './api';

export function planningPanel(context:Context,revision:Revision,getContext?:()=>Context):HTMLElement{
  const host=document.createElement('details');
  const title=document.createElement('summary');title.textContent='布局规划与阻挡诊断';host.append(title);
  const note=document.createElement('p');note.className='field-note';note.textContent='按当前版本分析接口位置和已记录的失败出口。可声明功能分区；分析结果用于选择下一步。';host.append(note);
  const label=document.createElement('label');label.textContent='功能分区（可选，每行填写“名称：器件位号”，位号用逗号分隔）';
  const input=document.createElement('textarea');input.rows=3;input.placeholder='控制：U1, R1, C1';label.append(input);host.append(label);
  const option=document.createElement('label');option.className='checkbox-label';
  const diagnostic=document.createElement('input');diagnostic.type='checkbox';
  option.append(diagnostic,document.createTextNode('功能分区分析时，附带接口与失败出口检查'));host.append(option);
  const button=document.createElement('button');button.type='button';button.className='button secondary';button.textContent='分析当前版本';host.append(button);
  const output=document.createElement('div');host.append(output);
  button.onclick=async()=>{
    button.disabled=true;output.replaceChildren();
    const line=(s:string)=>{const p=document.createElement('p');p.className='field-note';p.textContent=s;output.append(p);};
    try{
      const groups=input.value.trim()?input.value.trim().split(/\n/).map(s=>{
        const i=s.search(/[:：]/);if(i<1)throw new Error('每个功能分区请填写名称和器件位号。');
        return {name:s.slice(0,i).trim(),references:s.slice(i+1).split(/[,，、\s]+/).filter(Boolean),source:'网页用户声明'};
      }):[];
      const source=revision.manifest.find(f=>f.path===revision.entrypoints.board);
      const route=[...(getContext?.()||context).jobs].filter(j=>j.kind==='pcb.route'&&j.inputRevision===revision.id&&j.result?.layoutFeedback?.nextActions).sort((a,b)=>b.createdAt.localeCompare(a.createdAt))[0];
      const includeDiagnostics=!groups.length||diagnostic.checked;
      const report=await api(`projects/${context.project.id}/pcb-planning`,'POST',{
        revisionId:revision.id,expectedBoardHash:source?.sha256,groups,probeInterfaces:includeDiagnostics,...(route&&includeDiagnostics?{routeJobId:route.id}:{})});
      if(report.analysisMode==='functional-groups')line('仅分析功能分区；本次未执行接口、通道和出口几何检查。');
      line(`功能分区 ${report.groups.length} 组 · 跨区网络 ${report.crossGroupNets.length} 个 · 未分区器件 ${report.unassignedReferences.length} 个`);
      for(const g of report.groups)line(`${g.name}：${g.references.join('、')}${g.outOfRegion.length?'；区域外：'+g.outOfRegion.join('、'):''}`);
      for(const r of report.interfaces)line(`${r.reference}：${r.status==='passed'?'接口基准已对齐板边':'需要调整接口位置或方向'}；关联器件 ${r.affectedReferences.join('、')}；受影响网络 ${r.affectedNets.length} 个${r.blockers.length?'；试放阻挡：'+[...new Set(r.blockers.map((b:any)=>b.otherReference))].join('、'):''}`);
      if(report.unconfirmedInterfaces.length)line('插拔基准待确认：'+report.unconfirmedInterfaces.map((r:any)=>r.reference||'板框').join('、'));
      const actions:Record<string,string>={review_footprint_or_route_rule:'封装内部间距与线宽冲突，先审查器件或规则',route_with_existing_exit:'已有合法直出，可继续实际找路',compare_local_placement:'比较外围器件局部摆放',review_explicit_copper_rework:'先审查阻挡铜线的明确重布范围',review_uncovered_obstacle:'进一步核对板边、铜区、孔和本体约束'};
      for(const r of report.repairs)line(`${r.reference}.${r.pin}：${actions[r.nextAction]||r.nextAction}；可比较器件 ${r.movableCandidates.join('、')||'暂无'}；受影响网络 ${r.affectedNets.length} 个`);
      if(report.omittedInterfaceCount)line(`另有 ${report.omittedInterfaceCount} 个接口未试放，需继续单独核对。`);
      line('本报告绑定所选版本；直出或通道几何检查不代替实际布通、配对件插拔和制造验收。');
    }catch(e){line(e instanceof Error?e.message:String(e));}
    finally{button.disabled=false;}
  };
  return host;
}
