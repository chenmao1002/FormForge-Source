"""Common product metrics for real PCB edit workflows, separate from revalidation."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

from .adapters.connectivity import fingerprint
from .adapters.importer import within,digest,reparse
from .product_regression import compare_reports,save,summarize_tasks


def workflow_metrics(report):
    tasks=[dict(task=r['id'],status='passed'if r.get('passed')else'failed')for r in report['tasks']]
    # A task that was never dispatched remains in the original denominator.
    tasks.extend(dict(task='not-dispatched-'+str(i),status='blocked')for i in range(report['declaredTasks']-len(tasks)))
    head=report.get('headBoard')or{}
    clocks=[r.get('backendElapsedSeconds')for r in report['tasks']]
    return dict(**summarize_tasks(tasks),workflowCompleted=report['workflowCompleted'],
        operatorApiCallCount=report.get('operatorApiCallCount'),operationTrace=report.get('operationTrace'),
        backendElapsedSeconds=sum(clocks)if clocks and all(t is not None for t in clocks)else None,
        agentFormForgeOperationCount=report.get('agentFormForgeOperationCount'),
        nativeDraftAcceptedCount=sum(r.get('nativeDraftAccepted')is True for r in report['tasks']),
        sourceRetainedCount=sum(r.get('outcome')=='source-retained'for r in report['tasks']),
        candidateSavedCount=sum(r.get('candidateSaved')is True for r in report['tasks']),
        engineeringAccepted=False,ercChecked=False,schematicParityChecked=False,
        viaCount=head.get('viaCount'),copperLengthMm=head.get('copperLengthMm'),routingQuality=head.get('routingQuality'),
        modelCalls=None,coldStartDesignCompletion=None)


def complete_native_metrics(report):
    tasks=[dict(task=r['id'],status='passed'if r.get('passed')else'failed')for r in report['tasks']]
    tasks.extend(dict(task='not-dispatched-'+str(i),status='blocked')for i in range(2-len(tasks)))
    native=report.get('nativeChecks')or{};head=report.get('headBoard')or{}
    clocks=[r.get('backendElapsedSeconds')for r in report['tasks']]
    return dict(**summarize_tasks(tasks),workflowCompleted=report.get('workflowCompleted',False),
        operatorApiCallCount=report.get('operatorApiCallCount'),operationTrace=report.get('operationTrace'),
        agentFormForgeOperationCount=report.get('agentFormForgeOperationCount'),
        backendElapsedSeconds=sum(clocks)if clocks and all(v is not None for v in clocks)else None,
        nativeToolCalls=report.get('nativeProcessCallCount'),nativeToolCallScope=report.get('nativeProcessCallCountScope'),
        ercChecked=native.get('ercExitCode')is not None,drcChecked=native.get('drcExitCode')is not None,
        schematicParityChecked=native.get('schematicParityChecked',False),
        ercErrors=native.get('ercErrors'),ercWarnings=native.get('ercWarnings'),ercExcluded=native.get('ercExcluded'),
        drcErrors=native.get('drcErrors'),drcWarnings=native.get('drcWarnings'),drcExcluded=native.get('drcExcluded'),
        unconnected=native.get('unconnected'),schematicParityFindings=native.get('schematicParityFindings'),
        fileConsistencyPassed=report.get('fileConsistencyPassed'),nativeErrorChecksPassed=report.get('nativeErrorChecksPassed'),
        strictNativeChecksPassed=report.get('strictNativeChecksPassed'),passedWithWarnings=report.get('passedWithWarnings',False),
        engineeringAccepted=False,manufacturingApproved=False,
        viaCount=head.get('viaCount'),copperLengthMm=head.get('copperLengthMm'),routingQuality=head.get('routingQuality'),
        modelCalls=None,coldStartDesignCompletion=None)


def complete_source_manifest(source):
    result=[]
    for path in sorted(source.rglob('*')):
        if reparse(path):raise ValueError('Complete native acceptance input cannot contain links')
        if path.is_file():result.append(dict(path=path.relative_to(source).as_posix(),sha256=digest(path),size=path.stat().st_size))
    return result


def run_complete_native_suite(root,path,suite,output,baseline):
    if suite.get('tasks')!=['eda.import','manufacturing.prepare']:raise ValueError('Complete native acceptance declares exactly import and manufacturing preparation')
    source=within(root,suite['sourceDirectory']);expected=suite['inputManifest'];observed=complete_source_manifest(source)
    if observed!=expected:raise ValueError('Frozen complete native input manifest changed')
    profile=within(root,suite['ruleProfile']['path'])
    if digest(profile)!=suite['ruleProfile']['sha256']:raise ValueError('Frozen software rule profile changed')
    output=Path(output).resolve();output.relative_to(root)
    if output.exists():raise ValueError('Use a fresh output directory')
    output.mkdir(parents=True);save(output/'suite.json',suite);shutil.copyfile(profile,output/'software-rule-profile.json')
    frozen=output/'source';shutil.copytree(source,frozen)
    if complete_source_manifest(frozen)!=expected:raise ValueError('Complete native source changed during freeze')
    names=['scripts/run-product-regression.py','scripts/run-esp32-native-acceptance.py','scripts/run-pcb-workflow-regression.py',
        'src/formforge/product_workflow_regression.py','src/formforge/product_regression.py','src/formforge/benchmark_trace.py',
        'src/formforge/adapters/manufacturing.py','src/formforge/adapters/manufacturing_native.py','src/formforge/adapters/manufacturing_preflight.py',
        'src/formforge/adapters/importer.py','src/formforge/adapters/connectivity.py','src/formforge/manufacturing_schemas.py',
        'src/formforge/service.py','src/formforge/store.py','src/formforge/app.py','src/formforge/mcp_gateway.py','src/formforge/config.py']
    hashes={}
    for name in names:
        target=output/'engine-host'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(within(root,name),target);hashes[name]=digest(target)
    inputs=fingerprint(suite)
    command=[sys.executable,str(root/'scripts/run-esp32-native-acceptance.py'),'--source',str(frozen),'--output',str(output/'workflow'),
        '--run-id',output.name,'--case-id',suite['id'],'--input-fingerprint',inputs]
    save(output/'command.json',command)
    with(output/'workflow.log').open('w',encoding='utf8')as log:run=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
    actual=json.loads((output/'workflow/report.json').read_text(encoding='utf8'));unchanged=complete_source_manifest(source)==expected and complete_source_manifest(frozen)==expected
    host_unchanged=all(digest(within(root,name))==value for name,value in hashes.items())
    row=dict(category='complex-four-layer',inputFingerprint=inputs,tasks=actual['tasks'],metrics=complete_native_metrics(actual),
        passed=actual['passed']and unchanged and host_unchanged,elapsedSeconds=actual['elapsedSeconds'])
    result=dict(schemaVersion=1,suiteId=suite['id'],runId=output.name,mode=suite['mode'],cases={suite['id']:row},
        suiteSha256=digest(path),sourceManifestFingerprint=fingerprint(expected),softwareRuleProfile=suite['ruleProfile'],
        engine=actual.get('engine'),hostFilesSha256=hashes,sourceUnchanged=unchanged,hostSourceUnchanged=host_unchanged,elapsedSeconds=actual['elapsedSeconds'],
        passed=run.returncode==0 and row['passed'],workflowReport=(output/'workflow/report.json').relative_to(root).as_posix(),
        aggregate=dict(products=1,passedProducts=int(row['passed']),productPassRate=float(row['passed'])),
        scope='Same-version complete native input and two real background tasks. Active native errors, retained warnings/exclusions, file consistency and supplier/engineering qualifications are separate. The three-product revalidation denominator remains unchanged.')
    if baseline:
        result['comparison']=compare_reports(result,json.loads(Path(baseline).read_text(encoding='utf8')))
        result['comparison']['scope']='Same frozen complete native source, selected software rules and two-task denominator; retained advisories and engineering approval remain separate.'
    save(output/'report.json',result);return result


def run_workflow_suite(root, suite_path, output, *, baseline=None, basis_report=None, engine_from_plan=None):
    root=Path(root).resolve();path=within(root,suite_path);suite=json.loads(path.read_text(encoding='utf8'))
    if suite.get('version')!=1:raise ValueError('Unsupported workflow suite')
    if suite.get('mode')=='complete-native-acceptance':
        if basis_report or engine_from_plan:raise ValueError('PCB edit seed/bundle options do not apply to complete native acceptance')
        return run_complete_native_suite(root,path,suite,output,baseline)
    if suite.get('mode')!='native-pcb-edit-workflow':raise ValueError('Unsupported workflow suite')
    output=Path(output).resolve();output.relative_to(root)
    if output.exists():raise ValueError('Use a fresh output directory')
    output.mkdir(parents=True);save(output/'suite.json',suite)
    host_files=['scripts/run-product-regression.py','scripts/run-pcb-workflow-regression.py',
                'src/formforge/product_workflow_regression.py','src/formforge/native_pcb_workflow.py',
                'src/formforge/service.py','src/formforge/store.py','src/formforge/schemas.py','src/formforge/benchmark_trace.py']
    host_files+=['src/formforge/adapters/pcb.py','src/formforge/adapters/local_contact_rework.py',
                 'src/formforge/adapters/local_copper_rework.py','src/formforge/adapters/local_rework.py']
    host_hashes={}
    for name in host_files:
        source=within(root,name);target=output/'engine-host'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        host_hashes[name]=digest(target)
    case_path=within(root,suite['case']);request_path=within(root,suite['request'])
    case=json.loads(case_path.read_text(encoding='utf8'));request=json.loads(request_path.read_text(encoding='utf8'))
    save(output/'case.json',case);save(output/'request.json',request)
    command=[sys.executable,str(root/'scripts/run-pcb-workflow-regression.py'),'--case',str(case_path),
             '--request',str(request_path),'--output',str(output/'workflow'),'--run-id',output.name]
    if basis_report:command.extend(['--basis-report',str(Path(basis_report).resolve())])
    if engine_from_plan:command.extend(['--engine-from-plan',str(Path(engine_from_plan).resolve())])
    with (output/'workflow.log').open('w',encoding='utf8')as log:
        run=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
    actual=json.loads((output/'workflow/report.json').read_text(encoding='utf8'))
    inputs=fingerprint(dict(case=case,request=request,preparedBoardSha256=actual.get('input',{}).get('preparedBoardSha256')))
    row=dict(category='native-pcb-edit',inputFingerprint=inputs,tasks=actual['tasks'],metrics=workflow_metrics(actual),
             passed=actual['passed'],elapsedSeconds=actual['elapsedSeconds'])
    result=dict(schemaVersion=1,suiteId=suite['id'],runId=output.name,mode=suite['mode'],cases={suite['id']:row},
        engine=actual.get('engine'),hostFilesSha256=host_hashes,elapsedSeconds=actual['elapsedSeconds'],passed=run.returncode==0 and actual['passed'],
        workflowReport=(output/'workflow/report.json').relative_to(root).as_posix(),
        aggregate=dict(products=1,passedProducts=int(actual['passed']),productPassRate=float(actual['passed'])),
        scope='Frozen PCB edit workflow; native draft acceptance, source retention, full engineering acceptance and cold-start design remain separate. Artifact-revalidation task denominator is unchanged.')
    if baseline:
        previous=json.loads(Path(baseline).read_text(encoding='utf8'));comparison=compare_reports(result,previous)
        prior_workflow=json.loads(within(root,previous['workflowReport']).read_text(encoding='utf8'))
        comparison['scope']='Same prepared PCB bytes, request and search budgets. Workflow completion is not schematic acceptance or an adopted gain.'
        comparison['timing']=dict(preparationBefore=prior_workflow.get('input',{}).get('normalizationAction'),
            preparationAfter=actual.get('input',{}).get('normalizationAction'),
            endToEndPreparationComparable=prior_workflow.get('input',{}).get('normalizationAction')==actual.get('input',{}).get('normalizationAction'),
            backendSecondsBefore=sum(r.get('backendElapsedSeconds')or 0 for r in prior_workflow['tasks']),
            backendSecondsAfter=row['metrics']['backendElapsedSeconds'])
        result['comparison']=comparison
    save(output/'report.json',result);return result
