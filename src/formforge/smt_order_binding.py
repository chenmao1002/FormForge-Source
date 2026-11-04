"""Verify the frozen procurement revision and the exact submitted BOM/CPL pair."""
import csv
import json

from .adapters.importer import digest, within
from .adapters.procurement import verified_source
from .store import DomainError


def fail(message):
    raise DomainError('input_changed', message, 409)


def bind(registry, project, request):
    binding, sources, manufacturing, artifacts = registry._binding(
        project, request['manufacturingJobId'], request['expectedGerberSha256'])
    if manufacturing['kind'] != 'manufacturing.prepare':
        fail('SMT 订单需要原生制造任务与采购版本的完整绑定。')
    revision = registry.store.get('revisions', request['procurementRevisionId'])
    if revision['projectId'] != project or not revision.get('validation', {}).get('adopted'):
        fail('需要同工程已采纳的采购版本。')
    root = verified_source(registry.config, revision)
    plan = json.loads((root / 'plan.json').read_text('utf8'))
    procurement = registry.store.get('jobs', revision['createdByJobId'])
    if (procurement['projectId'] != project or procurement['kind'] != 'manufacturing.procurement'
            or procurement['status'] != 'completed' or (procurement.get('result') or {}).get('revisionId') != revision['id']):
        fail('采购版本与已完成任务不符。')
    package, _ = registry._artifact(project, manufacturing, 'manufacturing_project')
    artifacts.append(package)
    dependency = plan['manufacturingDependency']
    if (dependency['jobId'] != manufacturing['id'] or dependency['revisionId'] != binding['inputRevision']
            or dependency['packageSha256'] != package['sha256'] or dependency['artifact'] != package
            or procurement['inputRevision'] != revision.get('parentRevisionId')
            or procurement['params'].get('manufacturingJobId') != manufacturing['id']
            or procurement['params'].get('expectedPackageSha256') != package['sha256']):
        fail('采购版本来自不同制造任务或审核包。')

    def artifact(kind):
        rows = [a for a in registry.store.list('artifacts', project)
                if a['jobId'] == procurement['id'] and a['kind'] == kind]
        if len(rows) != 1 or rows[0].get('revisionId') != revision['id']:
            fail('采购产物缺失、重复或版本不符。')
        row = rows[0]
        path = within(registry.config.data, row['relativePath'])
        if not path.is_file() or path.stat().st_size != row['size'] or digest(path) != row['sha256']:
            fail('采购原始产物字节已改变。')
        artifacts.append(row)
        return row, path

    report, report_path = artifact('procurement_report')
    bom, bom_path = artifact('procurement_matching_bom')
    cpl, cpl_path = artifact('procurement_matching_cpl')
    notes, notes_path = artifact('procurement_matching_notes')
    body = json.loads(report_path.read_text('utf8'))
    note_body = json.loads(notes_path.read_text('utf8'))
    for result in [body, note_body]:
        if any(result.get(k) != v for k, v in dict(projectId=project, jobId=procurement['id'], revisionId=revision['id'],
                                                 manufacturingDependency=dependency).items()):
            fail('采购报告/匹配说明与工程、版本或制造依赖不符。')
    if not body.get('matchingFilesReady') or not procurement['result'].get('matchingFilesReady'):
        fail('采购匹配文件尚未就绪。')
    if bom['sha256'] != request['expectedBomSha256'] or cpl['sha256'] != request['expectedCplSha256']:
        fail('预期 BOM/CPL 摘要与原始产物不符。')
    files = {f['name']: f['sha256'] for f in note_body['files']}
    if files.get(bom['name']) != bom['sha256'] or files.get(cpl['name']) != cpl['sha256']:
        fail('匹配说明未固定同一组 BOM/CPL 字节。')
    with bom_path.open(encoding='utf-8-sig', newline='') as f:
        bom_rows = list(csv.DictReader(f))
    with cpl_path.open(encoding='utf-8-sig', newline='') as f:
        cpl_rows = list(csv.DictReader(f))
    expanded = {}
    for row in bom_rows:
        refs = row['Designator'].split(',')
        if row['Quantity'] != str(len(refs)):
            fail('BOM 每板数量与位号数不符。')
        for ref in refs:
            if not ref or ref in expanded:
                fail('BOM 位号重复或为空。')
            expanded[ref] = row
    by_ref = {r['reference']: r for r in body['rows']}
    positions = {r['Designator']: r for r in cpl_rows}
    manufacturing_refs = {r['reference'] for r in manufacturing['result']['selectedParts']}
    if (len(by_ref) != len(body['rows']) or len(positions) != len(cpl_rows) or not by_ref
            or set(expanded) != set(by_ref) or set(positions) != set(by_ref) or set(by_ref) != manufacturing_refs
            or body['selectedReferenceCount'] != len(by_ref) or note_body['selectedReferenceCount'] != len(by_ref)
            or not note_body.get('allSelectedReferencesRetained') or note_body.get('omittedSelectedReferences')):
        fail('BOM/CPL/制造范围未完整保留相同位号。')
    for ref, row in by_ref.items():
        if any(expanded[ref].get(k) != (row.get(v) or '') for k, v in [
            ('LCSC Part #', 'supplierPartNumber'), ('MPN', 'mpn'), ('Mounting', 'mounting')]):
            fail('BOM 器件身份或安装方式与采购报告不符。')
    sides = sorted({{'Top': 'top', 'Bottom': 'bottom'}.get(r['Layer'], 'unknown') for r in cpl_rows})
    if 'unknown' in sides or sides != request['parameters']['assembledSides']:
        fail('SMT 贴装面与原始坐标文件不符。')
    for quantity in [body.get('boardQuantity'), manufacturing['params'].get('boardQuantity')]:
        if quantity is not None and request['parameters']['assemblyQuantity'] > quantity:
            fail('贴片数量超过绑定资料的板数量。')
    for source in plan['sources']:
        row = registry._source(project, dict(sourceId=source['id'], expectedSha256=source['sha256'],
                                            expectedProvenanceSha256=source['provenanceSha256']))
        sources.append(row)
    binding['procurement'] = dict(revisionId=revision['id'], jobId=procurement['id'],
        jobFingerprint=registry_fingerprint(procurement), revisionFingerprint=registry_fingerprint(revision),
        reportArtifactId=report['id'], reportSha256=report['sha256'],
        bom=dict(artifactId=bom['id'], name=bom['name'], sha256=bom['sha256']),
        cpl=dict(artifactId=cpl['id'], name=cpl['name'], sha256=cpl['sha256']),
        notesArtifactId=notes['id'], notesSha256=notes['sha256'], bomLineCount=len(bom_rows),
        references=sorted(by_ref), referenceCount=len(by_ref), assembledSides=sides,
        throughHoleReferences=sorted(ref for ref, r in by_ref.items() if r['mounting'] == 'through-hole'),
        sourceRefs=[dict(sourceId=s['id'], expectedSha256=s['sha256'], expectedProvenanceSha256=s['provenanceSha256']) for s in sources])
    return binding, sources, [manufacturing, procurement], artifacts, revision


def registry_fingerprint(value):
    # Delayed import avoids a registry/binding module cycle.
    from .order_registry import fingerprint
    return fingerprint(value)


def validate_matching(binding, observation):
    refs = set(binding['procurement']['references'])
    matched = set(observation['matchedReferences'])
    checked = set(observation['checkedReferences'])
    if not (matched | checked) <= refs:
        fail('观察位号不属于订单绑定的 BOM/CPL。')
    if observation['bomMatchingStatus'] == 'complete' and matched != refs:
        fail('完整匹配必须明确覆盖全部位号，包括插件。')
