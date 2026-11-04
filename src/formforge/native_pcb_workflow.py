"""Explicit native-board-only routing drafts; never a schematic acceptance."""
import json
import os
from pathlib import Path
import shutil
import time
import zipfile

from .adapters.importer import within, digest, verify_snapshot
from .adapters.pcb import (pad_graph, draft_result, routing_proof, rework_drc_proof, body_gap_proof)
from .adapters.pcb_read_session import PcbReadSession
from .adapters.pcb_source_connectivity import proof as source_proof
from .adapters.pair_geometry import verify as pair_proof
from .adapters.board_stackup import proof as stackup_proof
from .processes import run_process
from .store import DomainError
from .jobs import Cancelled

SCOPE='native-source-preserving'


def validate_scope(parent, params):
    basis=parent.get('pcbDesign')or{}
    requested=params.get('acceptanceScope','schematic-contract')
    marked=basis.get('acceptanceScope')==SCOPE
    if requested!=SCOPE:
        if marked:raise DomainError('invalid_input','此为仅原生PCB来源，须明确选择native-source-preserving，不能冒充原理图契约。')
        return
    if not marked or parent['entrypoints'].get('schematic'):
        raise DomainError('invalid_input','原生源保持范围只允许明确标记、无原理图入口的PCB来源；原理图契约不允许降级。')
    if basis.get('sourceBoardSha256')!=params['expectedBoardHash']or not basis.get('nativeSourcePadGraph'):
        raise DomainError('version_conflict','原生来源板SHA或源焊盘契约缺失/不符。',409)


def save(path, data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')


def board_drc(service, revision, folder, deadline, cancel, phase, publish, preview_layers=()):
    folder.mkdir(parents=True);source=within(service.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest'])
    scratch=folder/'scratch';private=scratch/'config/10.0';private.mkdir(parents=True)
    share=Path(service.config.kicad_cli).parent.parent/'share/kicad'
    env={**os.environ,'KICAD_CONFIG_HOME':str(scratch/'config'),'TEMP':str(scratch),'TMP':str(scratch)}
    for filename in ('fp-lib-table','sym-lib-table'):shutil.copyfile(share/'template'/filename,private/filename)
    for var,name in [('KICAD10_SYMBOL_DIR','symbols'),('KICAD10_FOOTPRINT_DIR','footprints'),('KICAD10_3DMODEL_DIR','3dmodels')]:env[var]=str(share/name)
    board=within(source,revision['entrypoints']['board']);out=folder/'drc.json';log=folder/'native.log'
    command=[service.config.kicad_cli,'pcb','drc','--format','json','--severity-all','--all-track-errors','--exit-code-violations','--output',out,board]
    phase('原生PCB规则检查；ERC及原理图一致性明确未执行')
    run=run_process(command,source,log,env,cancel,deadline)
    publish(log,'pcb_native_log','text/plain')
    if run['exitCode']not in (0,5)or not out.is_file():raise DomainError('tool_failed','原生PCB检查未生成结构化报告。')
    raw=json.loads(out.read_text(encoding='utf-8-sig'))
    if not all(k in raw for k in ('violations','unconnected_items')):raise DomainError('tool_failed','无法读取原生PCB检查字段。')
    publish(out,'pcb_native_drc','application/json')
    for layer in preview_layers:
        side={'F.Cu':'front','B.Cu':'back'}.get(layer,layer.split('.')[0].lower())
        svg=folder/('pcb-'+side+'.svg');layers=layer+(','+('F.Silkscreen'if layer=='F.Cu'else'B.Silkscreen')if layer in {'F.Cu','B.Cu'}else'')+',Edge.Cuts'
        phase('导出原生PCB候选预览：'+layer)
        exported=run_process([service.config.kicad_cli,'pcb','export','svg','--layers',layers,'--mode-single','--fit-page-to-board',
                              '--exclude-drawing-sheet','--output',svg,board],source,folder/('preview-'+side+'.log'),env,cancel,deadline)
        if exported['exitCode']or not svg.is_file():raise DomainError('tool_failed','原生PCB候选预览未生成。')
        publish(svg,'pcb_preview','image/svg+xml')
    verify_snapshot(source,revision['manifest'])
    return raw,dict(errorCount=sum(v.get('severity')=='error'and not v.get('excluded',False)for v in raw['violations']),
                    warningCount=sum(v.get('severity')=='warning'for v in raw['violations']),unconnectedCount=len(raw['unconnected_items']),
                    ercChecked=False,schematicParityChecked=False,schematicParityCount=None,command=run)


def execute(service, job, output, phase, cancel, publish, register):
    parent=service.revision(job['projectId'],job['params']['revisionId']);validate_scope(parent,job['params'])
    output.mkdir(parents=True);deadline=time.monotonic()+job['params']['timeoutSeconds']
    reads=PcbReadSession(service.pcb,output/'pcb-read-metrics.json')
    source=reads.read(parent,output/'source-read',cancel)
    if pad_graph(source)['hash']!=parent['pcbDesign']['nativeSourcePadGraph']['hash']:
        raise DomainError('input_changed','原生焊盘来源契约与实际板不符。',409)
    baseline_raw,baseline=board_drc(service,parent,output/'baseline-drc',deadline,cancel,phase,publish)
    plan=service.pcb.route_plan(parent,job,output/'planning',cancel,phase,read_snapshot=reads.read)
    save(output/'route-plan.json',plan);publish(output/'route-plan.json','pcb_route_plan','application/json')
    common=dict(acceptanceScope=SCOPE,checksPassed=False,manufacturingApproved=False,ercChecked=False,schematicParityChecked=False,
                baselineNativeChecks=baseline,localPlacement=plan.get('localPlacement'),budgetExhausted=plan['budgetExhausted'])
    if not plan['operations']:
        result=dict(**common,revisionId=parent['id'],baseRevisionId=parent['id'],adopted=False,noop=True,changeCount=0,
                    nativeSourceChecksPassed=None,draftChecksPassed=False,nativeCheckStatus='unchanged-source-no-new-candidate-acceptance',
                    layoutFeedback=plan.get('layoutFeedback'),plannedConnectionCount=0,
                    scope='原生PCB局部搜索未形成可采纳变更，源版本保持；ERC/原理图一致性未执行。')
        save(output/'pcb-report.json',result);publish(output/'pcb-report.json','pcb_edit_report','application/json');cancel()
        if time.monotonic()>=deadline:raise DomainError('tool_failed','原生PCB任务达到时间预算。')
        if service.store.finish_readonly(job['id'],result,'原生PCB局部搜索保留源版本；没有新候选验收')is None:raise Cancelled()
        return
    edit_job={**job,'params':{**job['params'],'operations':plan['operations'],'_routePlan':plan}}
    before={};phase('保存明确的原生PCB候选，保留源版本')
    candidate,expected=service.pcb.candidate(parent,edit_job,output,None,None,cancel,baseline_out=before,read_snapshot=reads.read)
    register(candidate)
    layers=sorted({layer for n in job['params']['nets']for layer in n.get('layers',['F.Cu','B.Cu'])})
    raw,native=board_drc(service,candidate,output/'candidate-drc',deadline,cancel,phase,publish,layers)
    actual=service.pcb.read(candidate,output/'after-read',cancel)
    native_rules_passed=native['errorCount']==0 and native['unconnectedCount']==0
    inspection=dict(checks=[dict(kind='drc',status='passed'if native_rules_passed else'failed',schematicParityChecked=False)],checksPassed=False)
    result=draft_result(inspection,expected,actual,raw,None,service.pcb.copper_intent(candidate),previous=before,
                        native_source=True,baseline_drc=baseline_raw)
    proofs=dict(sourceConnectivity=source_proof(before,actual),routingProgress=routing_proof(plan,actual),reworkCopperEnds=rework_drc_proof(plan,raw,actual))
    if plan.get('layoutFeedback',{}).get('enforced'):proofs['bodyAvoidance']=body_gap_proof(plan,actual)
    basis=candidate['pcbDesign']
    if basis.get('pairedSections'):proofs['pairedGeometry']=pair_proof(basis['pairedSections'],actual)
    if basis.get('stackup'):proofs['stackup']=stackup_proof(basis['stackup'],actual['physicalStackup'])
    if basis.get('powerFeeds'):proofs['powerFeeds']=actual['powerFeeds']
    if actual['layoutFeedback'].get('interfaceLegends'):
        legends=actual['layoutFeedback']['interfaceLegends'];proofs['interfaceLegends']=dict(**legends,passed=legends['declaredPassed'])
    passed=result['draftChecksPassed']and all(p['passed']for p in proofs.values())
    result.update(common);result.update(proofs,nativeSourceChecksPassed=passed,draftChecksPassed=passed,revisionId=candidate['id'],baseRevisionId=parent['id'],
        connectivityPreserved=proofs['sourceConnectivity']['passed'],nativeChecksPassed=native_rules_passed,nativeChecks=native,
        changeCount=len(candidate['changes']),changes=candidate['changes'],layoutFeedback=actual['layoutFeedback'],
        scope='原生PCB源保持草稿：保存/DRC差异、真实焊盘与物理连接、出口/转角和明确铜重接；ERC/原理图一致性未执行。')
    save(output/'pcb-snapshot.json',actual);publish(output/'pcb-snapshot.json','pcb_snapshot','application/json')
    save(output/'pcb-report.json',result);publish(output/'pcb-report.json','pcb_edit_report','application/json')
    package=output/'pcb-project.zip'
    with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED)as archive:
        folder=within(service.config.data,candidate['relativePath']);verify_snapshot(folder,candidate['manifest'])
        for row in candidate['manifest']:cancel();archive.write(within(folder,row['path']),'source/'+row['path'])
        archive.writestr('formforge-manifest.json',json.dumps(candidate,ensure_ascii=False,indent=2))
    publish(package,'native_project','application/zip');cancel()
    if time.monotonic()>=deadline:raise DomainError('tool_failed','原生PCB任务达到时间预算，保留未采纳候选。')
    phase('原子采纳原生范围草稿或保留旧版；完整电气资格仍未验证')
    if service.store.finish_edit(job['id'],candidate['id'],result,pcb_draft=True)is None:raise Cancelled()
