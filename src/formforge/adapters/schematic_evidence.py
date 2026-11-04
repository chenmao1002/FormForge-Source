"""Exact native ERC/XML evidence identity, shared independently of firmware roles."""
import json
from .connectivity import fingerprint
from .importer import digest, within, verify_snapshot
from ..store import DomainError


def fail(message, code='invalid_input'):
    raise DomainError(code, message, 409 if code in {'input_changed', 'version_conflict'} else 400)


def read_evidence(config, store, project_id, revision_id, selected=None, check_job_id=None):
    revision=store.get('revisions',revision_id)
    if revision['projectId']!=project_id or revision.get('module','eda')!='eda' or not revision['entrypoints'].get('schematic'):
        fail('原生检查依据必须属于同工程的原理图版本。')
    verify_snapshot(within(config.data,revision['relativePath']),revision['manifest'])
    manifest_hash=fingerprint(revision['manifest'])
    artifacts=store.list('artifacts',project_id)
    if selected:
        if selected['revisionId']!=revision_id or selected['inputManifestHash']!=manifest_hash:
            fail('原生检查依据的版本或输入清单摘要不符。','version_conflict')
        check_job_id=selected['checkJobId']
    reports=[a for a in artifacts if a['revisionId']==revision_id and a['kind']=='report' and a['name']=='erc.json']
    if check_job_id is None:
        if not reports:fail('此版本没有原生ERC依据；请先运行原生检查或PCB交接。','dependency_missing')
        # Discover by ERC, never by an unrelated later XML export. A failed or
        # damaged latest report must not silently fall back to an older green one.
        check_job_id=max(reports,key=lambda a:(a['createdAt'],a['id']))['jobId']
    job=store.get('jobs',check_job_id);result=job.get('result') or {}
    if job['projectId']!=project_id or job['status']!='completed' or result.get('revisionId',job.get('inputRevision'))!=revision_id:
        fail('检查任务必须已完成并明确对应同工程、同原理图版本。')
    checks=result.get('nativeChecks') if job['kind']=='pcb.handoff' else result.get('checks')
    erc=[c for c in checks if c.get('kind')=='erc'] if isinstance(checks,list) else []
    if len(erc)!=1 or erc[0].get('status')!='passed' or any(erc[0].get(k)!=0 for k in ['findings','errors','warnings']):
        fail('所选原生任务没有唯一且通过的ERC检查。')
    same=[a for a in artifacts if a['jobId']==check_job_id and a['revisionId']==revision_id]
    nets=[a for a in same if a['kind']=='netlist']
    reports=[a for a in same if a['kind']=='report' and a['name']=='erc.json']
    if len(nets)!=1 or len(reports)!=1:fail('所选检查须唯一配对一份原生XML网表和一份ERC报告。')
    net,report=nets[0],reports[0]
    evidence=dict(schemaVersion=1,checkJobId=check_job_id,revisionId=revision_id,inputManifestHash=manifest_hash,
                  netlistArtifactId=net['id'],expectedNetlistSha256=net['sha256'],ercArtifactId=report['id'],expectedErcSha256=report['sha256'])
    if selected is not None and selected!=evidence:fail('所选检查产物身份或期望摘要不符。','version_conflict')
    paths=[]
    for a in [net,report]:
        path=within(config.data,a['relativePath'])
        if not path.is_file() or path.stat().st_size!=a['size'] or digest(path)!=a['sha256']:
            fail('原生检查产物字节或SHA已变化。','input_changed')
        paths.append(path)
    try:
        raw=json.loads(paths[1].read_text(encoding='utf-8-sig'))
        if not isinstance(raw,dict) or not isinstance(raw.get('sheets'),list):raise ValueError('ERC sheets required')
        violations=[]
        for sheet in raw['sheets']:
            if not isinstance(sheet,dict) or not isinstance(sheet.get('violations'),list):raise ValueError('ERC violations required')
            violations.extend(sheet['violations'])
        if any(not isinstance(v,dict) or v.get('excluded',False) is not True for v in violations):raise ValueError('Active ERC findings')
    except (ValueError,TypeError,UnicodeError,OSError) as error:
        raise DomainError('invalid_input','原始ERC报告格式不完整或仍有活动检查项。') from error
    return dict(revision=revision,evidence=evidence,netlistArtifact=net,netlistPath=paths[0],ercArtifact=report,ercPath=paths[1],
                inputManifestHash=manifest_hash,check=dict(kind=job['kind'],toolVersion=result.get('nativeToolVersion',result.get('toolVersion')),erc=erc[0]))
