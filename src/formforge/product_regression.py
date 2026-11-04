"""Pinned product artifact revalidation, with comparable and scoped metrics."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime, timezone

from .adapters.connectivity import fingerprint
from .adapters.importer import within, reparse
from .benchmark_trace import summarize_trace


CATEGORIES={'simple-board','complex-four-layer','pcba-enclosure'}
TASKS={'integrity','erc','drc','pcb-snapshot','pcba-binding','step-read','stl-topology','explosion-review'}
MODE='artifact-revalidation'


def save(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def input_fingerprint(case):
    return fingerprint(dict(category=case['category'],inputs=sorted(case['inputs'],key=lambda r:r['path']),pcb=case['pcb'],
                            mechanical=case.get('mechanical'),tasks=case['tasks'],expected=case.get('expected',{})))


def load_suite(root, path):
    suite=json.loads(within(root,path).read_text(encoding='utf8'))
    if suite.get('version')not in {1,2} or suite.get('mode')!=MODE:raise ValueError('Unsupported product regression suite')
    cases=[json.loads(within(root,p).read_text(encoding='utf8')) for p in suite['cases']]
    if not cases or len({c['id'] for c in cases})!=len(cases) or not {c['category']for c in cases}<=CATEGORIES:
        raise ValueError('Product cases must be nonempty, unique and use supported categories')
    if suite['version']==1 and ({c['category']for c in cases}!=CATEGORIES or len(cases)!=3):
        raise ValueError('The suite requires one fixed case for each of the three product categories')
    for case in cases:
        if case.get('version')!=1 or not case['inputs'] or len(set(case['tasks']))!=len(case['tasks']) or not set(case['tasks'])<=TASKS:
            raise ValueError('Unsupported or duplicate product tasks')
        if case['tasks'][0]!='integrity'or not {'integrity','erc','drc','pcb-snapshot'}<=set(case['tasks']):raise ValueError('Every product requires frozen integrity before native PCB revalidation')
        paths=[r['path']for r in case['inputs']]
        if len(set(paths))!=len(paths):raise ValueError('Duplicate frozen input path')
        for row in case['inputs']:
            within(root,row['path'])
            if len(row['sha256'])!=64:raise ValueError('Missing frozen input digest')
    return suite,cases


def summarize_tasks(tasks):
    count=len(tasks);passed=sum(r['status']=='passed'for r in tasks)
    return dict(declaredTasks=count,finishedTasks=sum(r['status']in {'passed','failed'}for r in tasks),
                passedTasks=passed,failedTasks=sum(r['status']=='failed'for r in tasks),
                blockedTasks=sum(r['status']=='blocked'for r in tasks),
                taskPassRate=passed/count if count else None)


def native_findings(raw, task):
    if task=='erc':
        if 'sheets'not in raw:raise ValueError('Unrecognized native ERC report')
        return [r for sheet in raw['sheets']for r in sheet['violations']]
    if not all(k in raw for k in ('violations','unconnected_items','schematic_parity')):
        raise ValueError('Unrecognized native DRC/parity report')
    return raw['violations']


def compare_reports(current, baseline):
    """A different task denominator or input must never be sold as an improvement."""
    if current['mode']!=baseline['mode'] or set(current['cases'])!=set(baseline['cases']):
        raise ValueError('Baseline product set/mode differs')
    rows={}
    for name,new in current['cases'].items():
        old=baseline['cases'][name]
        if new['inputFingerprint']!=old['inputFingerprint']:raise ValueError('Baseline input, rules or task set differs: '+name)
        a,b=new['metrics'],old['metrics']
        rows[name]=dict(taskPassRateBefore=b['taskPassRate'],taskPassRateAfter=a['taskPassRate'],
                       wallSecondsBefore=old['elapsedSeconds'],wallSecondsAfter=new['elapsedSeconds'],
                       agentCallsBefore=b.get('agentFormForgeOperationCount'),agentCallsAfter=a.get('agentFormForgeOperationCount'),
                       viasBefore=b.get('viaCount'),viasAfter=a.get('viaCount'),
                       routingQualityBefore=b.get('routingQuality'),routingQualityAfter=a.get('routingQuality'))
    return dict(comparable=True,cases=rows,scope='Same frozen products, task denominator and rules; revalidation is not design-from-requirements success.')


class ProductRegression:
    def __init__(self, root, output, config=None):
        from .config import Config
        from .adapters.pcb import PcbAdapter
        self.root=Path(root).resolve();self.output=Path(output).resolve();self.output.relative_to(self.root)
        if self.output.exists():raise ValueError('Use a fresh output directory to retain failed evidence')
        self.output.mkdir(parents=True);self.config=config or Config(self.root);self.pcb=PcbAdapter(self.config)
        self.engine=self.output/'engine';shutil.copytree(self.pcb.engine_root,self.engine/'pcb')
        for name,path in [('product_regression.py',Path(__file__)),('product_step_probe.py',Path(__file__).with_name('adapters')/'product_step_probe.py'),
                          ('mechanical_explosion.py',Path(__file__).with_name('adapters')/'mechanical_explosion.py'),
                          ('stl_topology.py',Path(__file__).with_name('stl_topology.py')),
                          ('benchmark_trace.py',Path(__file__).with_name('benchmark_trace.py'))]:
            shutil.copyfile(path,self.engine/name)
        self.engine_files={p.relative_to(self.engine).as_posix():digest(p)for p in self.engine.rglob('*')if p.is_file()}
        self.tool_paths={'kicadCli':self.config.kicad_cli,'kicadPython':self.config.kicad_python,'mechanicalPython':self.config.mechanical_python}
        self.tools={k:dict(path=p,sha256=digest(Path(p)))if p else None for k,p in self.tool_paths.items()}
        self.native_calls=0

    def freeze(self, case, folder):
        frozen=folder/'inputs';entries=[]
        for row in case['inputs']:
            source=within(self.root,row['path'])
            if any(reparse(p)for p in [source,*source.parents] if p.is_relative_to(self.root)):raise ValueError('Linked benchmark input')
            target=within(frozen,row['path']);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
            if digest(target)!=row['sha256']:raise ValueError('Frozen input changed: '+row['path'])
            entries.append(dict(**row,size=target.stat().st_size))
        save(folder/'input-manifest.json',entries)
        return dict(passed=True,fileCount=len(entries),bytes=sum(r['size']for r in entries))

    def run_process(self, command, folder, timeout=120, env=None):
        self.native_calls+=1
        result=subprocess.run([str(x)for x in command],capture_output=True,encoding='utf8',errors='replace',
                              timeout=timeout,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (folder/'native.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf8')
        save(folder/'command.json',dict(arguments=list(map(str,command)),exitCode=result.returncode))
        return result

    def native_rules(self, case, folder, task, frozen):
        config=self.config;pcb=case['pcb'];source=within(frozen,pcb['sourceDirectory']);out=folder/'native.json'
        scratch=folder/'scratch';private=scratch/'config/10.0';private.mkdir(parents=True)
        env={**os.environ,'KICAD_CONFIG_HOME':str(scratch/'config'),'TEMP':str(scratch),'TMP':str(scratch)}
        share=Path(config.kicad_cli).parent.parent/'share/kicad'
        for name in ('fp-lib-table','sym-lib-table'):shutil.copyfile(share/'template'/name,private/name)
        for var,name in [('KICAD10_SYMBOL_DIR','symbols'),('KICAD10_FOOTPRINT_DIR','footprints'),('KICAD10_3DMODEL_DIR','3dmodels')]:env[var]=str(share/name)
        command=[config.kicad_cli,'sch'if task=='erc'else'pcb',task,'--format','json','--severity-all','--output',out]
        if task=='drc':command.extend(['--all-track-errors','--schematic-parity'])
        command.append(within(source,pcb['schematic'if task=='erc'else'board']))
        result=self.run_process(command,folder,env=env)
        if result.returncode not in (0,5) or not out.is_file():raise ValueError('Native '+task+' failed to produce its structured report')
        raw=json.loads(out.read_text(encoding='utf-8-sig'));expected=case.get('expected',{})
        rows=native_findings(raw,task);errors=sum(r.get('severity')=='error'for r in rows);warnings=sum(r.get('severity')=='warning'for r in rows)
        missing=len(raw.get('unconnected_items',[]));parity=len(raw.get('schematic_parity',[]))
        return dict(passed=errors<=expected.get(task+'Errors',0) and (task+'Warnings' not in expected or warnings<=expected[task+'Warnings']) and (task!='drc'or(missing<=expected.get('unconnected',0)and parity<=expected.get('schematicParity',0))),
                    errors=errors,warnings=warnings,unconnected=missing if task=='drc'else None,schematicParity=parity if task=='drc'else None,
                    nativeReport='native.json',scope='Frozen source native rule check; warnings and other engineering qualifications remain visible.')

    def task(self, case, task, folder):
        frozen=folder.parent/'inputs';pcb=case['pcb'];mech=case.get('mechanical',{})
        if task=='integrity':return self.freeze(case,folder.parent)
        if task in {'erc','drc'}:return self.native_rules(case,folder,task,frozen)
        if task=='pcb-snapshot':
            self.native_calls+=1
            result=self.pcb.worker(dict(action='read',board=str(within(frozen,pcb['sourceDirectory']+'/'+pcb['board']))),folder/'read',lambda:None,60)
            tracks=result['tracks'];quality=result['layoutFeedback']['routingQuality'];expected=case.get('expected',{})
            texts={r['text'] for r in result.get('boardTexts',[])}
            missing_texts=sorted(set(expected.get('requiredBoardTexts',[]))-texts)
            forbidden_texts=sorted(set(expected.get('forbiddenBoardTexts',[]))&texts)
            exits=result['layoutFeedback']['padExits']
            quality_ok=all(isinstance(quality.get(k),(int,float)) and quality[k]<=limit for k,limit in expected.get('routingQualityLimits',{}).items())
            exits_ok=all(isinstance(exits.get(k),(int,float)) and exits[k]<=limit for k,limit in expected.get('padExitLimits',{}).items())
            return dict(passed=len(result['footprints'])==expected.get('footprintCount',len(result['footprints'])) and not missing_texts and not forbidden_texts and quality_ok and exits_ok,
                        missingBoardTexts=missing_texts,forbiddenBoardTexts=forbidden_texts,padExits=exits,
                        footprintCount=len(result['footprints']),copperLayers=result['copperLayers'],
                        viaCount=sum(t['type']=='via'for t in tracks),
                        copperLengthMm=round(sum(math.dist(t['start'],t['end'])for t in tracks if t['type']=='segment'),6),
                        routingQuality={k:quality.get(k)for k in ('bendCount','backtrackCount','shortClusterCount','exposedCornerCount','rightAngleCount','acuteCornerCount')})
        if task=='pcba-binding':
            report=json.loads(within(frozen,mech['mechanicalReport']).read_text(encoding='utf8'))
            record=json.loads(within(frozen,mech['revisionRecord']).read_text(encoding='utf8'));design=record['mechanicalDesign']
            pcba=next(s for s in report['sourceProvenance']['sources']if s['id']=='pcba');dependency=design['pcbDependency']
            board_sha=digest(within(frozen,pcb['sourceDirectory']+'/'+pcb['board']))
            generic=dependency.get('bindingKind')=='feature-model-pcba-v1'
            pcba_sha=digest(within(frozen,mech['pcbaStep']))
            checks=dict(designBound=report['designSha256']==mech['expectedDesignSha256']==design['designSha256'],
                        pcbRevisionBound=pcb['revisionId']==dependency['revisionId'] and (generic or pcba['version']==pcb['revisionId']),
                        pcbBytesBound=board_sha==dependency['boardSha256'],pcbaBytesBound=pcba_sha==pcba['sha256'])
            if generic:
                binding=dependency['pcbaBinding']
                checks.update(completePcba=dependency['coverage']['complete'],nativeStepBound=pcba_sha==dependency['geometryStepSha256']==binding['stepSha256'],
                              singleOutput=sum(p['id']==binding['part'] for p in report['parts'])==1)
            return dict(passed=all(checks.values()),checks=checks,scope='Frozen board, complete PCBA and enclosure provenance; not a new collision or assembly approval.')
        if task=='explosion-review':
            from .adapters import mechanical_explosion
            if digest(Path(mechanical_explosion.__file__))!=self.engine_files['mechanical_explosion.py']:raise ValueError('Explosion review engine changed')
            design=json.loads(within(frozen,mech['designJson']).read_text(encoding='utf8'))
            result=mechanical_explosion.review_joint_directions(design);expected=case.get('expected',{})
            return dict(passed=not result['findings'] and not result['uncovered'] and result['comparedCount']==expected['explosionJointCount'],**result)
        if task=='step-read':
            parts=[dict(id=r['id'],path=str(within(frozen,r['step'])),singleSolid=True)for r in mech['parts']]
            parts.extend(dict(id=k,path=str(within(frozen,mech[k])),singleSolid=False)for k in ('assemblyStep','pcbaStep'))
            save(folder/'request.json',dict(parts=parts));script=self.engine/'product_step_probe.py'
            run=self.run_process([self.config.mechanical_python,'-I','-B',script,folder/'request.json',folder/'native.json'],folder,timeout=180)
            if run.returncode or not(folder/'native.json').is_file():raise ValueError('Independent native STEP read failed')
            result=json.loads((folder/'native.json').read_text(encoding='utf8'));return {**result,'passed':result['checksPassed']}
        if task=='stl-topology':
            from .stl_topology import inspect, report
            results=report([inspect(within(frozen,r['stl']))for r in mech['parts']]);save(folder/'stl-topology.json',results)
            return dict(passed=all(r['passed']for r in results['files']),fileCount=len(results['files']),report='stl-topology.json',
                        scope='Actual frozen binary STL topology; supplier approval, wall thickness and self-intersection remain separate.')
        raise ValueError('Unsupported task: '+task)

    def run(self, suite, cases, *, traces=None, baseline=None):
        started=time.perf_counter();run_id=self.output.name
        result=dict(schemaVersion=1,suiteId=suite['id'],runId=run_id,mode=MODE,startedAt=datetime.now(timezone.utc).isoformat(),
                    timingScope='From the first product input freeze through the last product task; caller API durations are separately recorded, engine initialization is excluded.',
                    engine=dict(files=self.engine_files,fingerprint=fingerprint(self.engine_files),pcbBundleHash=self.pcb.engine_hash,tools=self.tools),
                    cases={},scope='Declared real frozen products, independently revalidated. No new design-from-requirements, model reasoning, hardware or supplier approval is measured.')
        for case in cases:
            begin=time.perf_counter();folder=self.output/case['id'];folder.mkdir();save(folder/'case.json',case)
            row=dict(category=case['category'],inputFingerprint=input_fingerprint(case),tasks=[],metrics={});result['cases'][case['id']]=row
            native_before=self.native_calls;intact=True
            for task in case['tasks']:
                task_folder=folder/task;task_folder.mkdir();clock=time.perf_counter()
                if not intact:entry=dict(task=task,status='blocked',reason='Frozen input integrity failed',elapsedSeconds=0.)
                else:
                    try:
                        value=self.task(case,task,task_folder);entry=dict(task=task,status='passed'if value['passed']else'failed',result=value)
                    except Exception as error:entry=dict(task=task,status='failed',errorType=type(error).__name__,reason=str(error))
                    entry['elapsedSeconds']=round(time.perf_counter()-clock,6)
                    if task=='integrity':intact=entry['status']=='passed'
                row['tasks'].append(entry);save(self.output/'report.json',result)
            metrics=summarize_tasks(row['tasks']);metrics.update(nativeToolCalls=self.native_calls-native_before,runnerTaskDispatches=len([t for t in row['tasks']if t['status']!='blocked']),
                agentFormForgeOperationCount=None,agentTraceStatus='not-recorded',modelCalls=None,coldStartDesignCompletion=None)
            for task in row['tasks']:
                if task['task']=='pcb-snapshot'and 'result'in task:
                    metrics.update({k:task['result'][k]for k in ('viaCount','copperLengthMm','routingQuality')})
            if traces and case['id']in traces:
                events=[json.loads(line)for line in Path(traces[case['id']]).read_text(encoding='utf8').splitlines()if line.strip()];trace=summarize_trace(events)
                if trace.get('runId')!=run_id or trace.get('caseId')!=case['id']or trace.get('inputFingerprint')!=row['inputFingerprint']:
                    raise ValueError('Agent trace does not match this run, product and frozen input')
                for event in events:
                    if event.get('revisionId')and event['revisionId']!=case['pcb']['revisionId']:
                        raise ValueError('Agent trace read another revision')
                    if event.get('boardSha256')and event['boardSha256']!=digest(within(folder/'inputs',case['pcb']['sourceDirectory']+'/'+case['pcb']['board'])):
                        raise ValueError('Agent trace read other board bytes')
                metrics.update(agentFormForgeOperationCount=trace['externalAgentCalls'],agentTraceStatus='recorded',operationTrace=trace)
                shutil.copyfile(traces[case['id']],folder/'agent-operations.jsonl')
            row.update(metrics=metrics,elapsedSeconds=round(time.perf_counter()-begin,6),passed=metrics['passedTasks']==metrics['declaredTasks'])
        result.update(finishedAt=datetime.now(timezone.utc).isoformat(),elapsedSeconds=round(time.perf_counter()-started,6),
                      passed=all(r['passed']for r in result['cases'].values()),
                      aggregate=dict(products=len(cases),passedProducts=sum(r['passed']for r in result['cases'].values()),
                                     productPassRate=sum(r['passed']for r in result['cases'].values())/len(cases)))
        if baseline:result['comparison']=compare_reports(result,json.loads(Path(baseline).read_text(encoding='utf8')))
        save(self.output/'report.json',result);return result
