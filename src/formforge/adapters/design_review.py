"""Version-bound, Agent-declared engineering evidence, never automatic PDF reasoning.

Arithmetic is deterministic and bounded; facts/units/coverage remain a reviewer's
responsibility. Physical measurements and manufacturing approval are separate.
"""
import ast
import math
import shutil
import zipfile
import json
from pathlib import Path

from .importer import digest, within, verify_snapshot
from .schematic import read_schematic
from .native_syntax import parse
from .connectivity import read_graph
from ..store import DomainError
from ..source_registry import validate_registered_file


def fail(message):
    raise DomainError('invalid_input', message)


def calculate(expression, facts):
    try:
        tree = ast.parse(expression, mode='eval')
    except (ValueError, SyntaxError) as exc:
        raise DomainError('invalid_input', '计算式无效。') from exc
    if len(list(ast.walk(tree))) > 150:
        fail('计算式过长。')
    used = set()

    def visit(node):
        if isinstance(node, ast.Expression):
            value = visit(node.body)
        elif isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = node.value
        elif isinstance(node, ast.Name) and node.id in facts:
            used.add(node.id)
            value = facts[node.id]['value']
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add): value = a + b
            elif isinstance(node.op, ast.Sub): value = a - b
            elif isinstance(node.op, ast.Mult): value = a * b
            elif b != 0: value = a / b
            else: fail('计算式除以零。')
        else:
            fail('计算式仅支持事实名称、常数和加减乘除。')
        if not math.isfinite(value) or abs(value) > 1e15:
            fail('计算结果超出范围。')
        return float(value)

    return visit(tree), used


def connection_check(item, graph):
    """Check declared native pin membership; different_nets means pairwise distinct."""
    mode = item.get('mode', 'same_net')
    requested = [(p['reference'], p['pin']) for p in item['pins']]
    if mode not in {'same_net', 'different_nets', 'exact_net'} or not requested or len(set(requested)) != len(requested):
        fail('连接断言类型或引脚集合无效。')
    if mode != 'exact_net' and len(requested) < 2:
        fail('同网/异网断言至少需要两个引脚。')
    membership = {(p['reference'], p['pin']): g['id'] for g in graph['groups'] for p in g['pins']}
    ids = [membership.get(p) for p in requested]
    actual_groups = [g for g in graph['groups'] if g['id'] in set(ids)]
    actual = {(p['reference'], p['pin']) for g in actual_groups for p in g['pins']}
    known = None not in ids
    passed = known and (len(set(ids)) == len(ids) if mode == 'different_nets' else len(set(ids)) == 1)
    if mode == 'exact_net':
        passed = passed and actual == set(requested)
    as_pins = lambda pairs: [dict(reference=r, pin=p) for r, p in sorted(pairs)]
    return dict(kind='connection', title=item['title'], mode=mode, pins=item['pins'], passed=passed,
                actualGroups=actual_groups, unknownPins=as_pins(p for p in requested if p not in membership),
                missingPins=as_pins(set(requested)-actual) if mode == 'exact_net' else [],
                extraPins=as_pins(actual-set(requested)) if mode == 'exact_net' else [])


def review(config, revision, params, graph, output, check_cancel, publish):
    source_root = within(config.data, revision['relativePath'])
    verify_snapshot(source_root, revision['manifest'])
    source_ids = [s['id'] for s in params['sources']]
    fact_ids = [f['id'] for f in params['facts']]
    if len(source_ids) != len(set(source_ids)) or len(fact_ids) != len(set(fact_ids)):
        fail('来源和事实名称不能重复。')
    facts = {f['id']: f for f in params['facts']}
    if any(f['sourceId'] not in source_ids for f in facts.values()):
        fail('事实引用了未提供的来源。')
    frozen = output / 'evidence'
    frozen.mkdir()
    sources = []
    for item in params['sources']:
        check_cancel()
        path = Path(item['path']).resolve()
        registered=bool(item.get('registeredSource')) and validate_registered_file(config,revision['projectId'],item)
        if (not registered and not any(path.is_relative_to((config.root / folder).resolve()) for folder in ('examples', 'validation'))) or path.is_symlink():
            fail('审查依据须先保存在 FormForge examples/ 或 validation/ 内。')
        if path.suffix.lower() not in {'.pdf', '.json', '.md', '.txt'} or not path.is_file() or path.stat().st_size > 12 * 1024 * 1024:
            fail('依据仅支持不超过 12 MB 的 PDF/JSON/Markdown/文本。')
        if digest(path) != item['sha256']:
            fail('审查依据摘要已变化。')
        target = frozen / (item['id'] + path.suffix.lower())
        shutil.copyfile(path, target)
        if digest(target) != item['sha256']:
            fail('复制审查依据时内容发生变化。')
        sources.append({**item, 'path': target.name})
    objects = []
    presentation_pages = {}
    first = read_schematic(config, revision, limit=300)
    for sheet in first['sheets']:
        offset = 0
        while True:
            page = read_schematic(config, revision, sheet['path'], offset, 300)
            if offset==0 and params.get('pinReviews'):
                presentation_pages[sheet['path']]=dict(sha256=sheet['sha256'],readability=page['readability'])
            objects.extend(page['objects'])
            if page['nextOffset'] is None: break
            offset = page['nextOffset']
    components = {}
    for o in objects:
        if o['type'] == 'symbol':
            if o['reference'] in components: fail('审查暂不支持共享图页或重复位号。')
            components[o['reference']] = {p['name']: p['value'] for p in o['properties']}
    checks = []
    if params.get('layoutGroups'):
        from .schematic_groups import check_groups
        checks.extend(check_groups(params['layoutGroups'],objects))
    for item in params['properties']:
        actual = components.get(item['reference'], {}).get(item['name'])
        checks.append(dict(kind='property', title=item['reference'] + ' ' + item['name'],
                           expected=item['expected'], actual=actual, passed=actual == item['expected']))
    for item in params['connections']:
        checks.append(connection_check(item, graph))
    for item in params['calculations']:
        value, a = calculate(item['expression'], facts)
        limit, b = calculate(item['limit'], facts)
        used = sorted(a | b)
        qualifications = sorted({facts[k]['basis'] for k in used if facts[k]['basis'] in {'typical', 'engineering_allowance'}})
        close = math.isclose(value, limit, rel_tol=1e-12, abs_tol=1e-12)
        checks.append(dict(kind='calculation', **item, value=value, limitValue=limit,
                           factIds=used, qualifications=qualifications,
                           passed=(value <= limit or close) if item['operator'] == 'le' else (value >= limit or close)))
    passed = all(c['passed'] for c in checks)
    result = dict(revisionId=revision['id'], projectVersion=params['expectedProjectVersion'], graphHash=graph['hash'], inputManifest=revision['manifest'],
                  title=params['title'], scope=params['scope'], checksPassed=passed,
                  pcbHandoffReady=passed, manufacturingApproved=False, hardwareVerified=False,
                  declaration='Agent 提供事实、单位、覆盖范围和配置条件；服务核对摘要、实际属性/连接并重算，不自动证明数据表解读正确。',
                  sources=sources, facts=params['facts'], checks=checks, conditions=params['conditions'],
                  hardwareVerification=params['hardwareVerification'])
    path = output / 'design-review.json'
    if params.get('pinReviews'):
        from .pin_review import evaluate
        result['pinReview']=evaluate(params['pinReviews'],revision['id'],presentation_pages)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    publish(path, 'design_review', 'application/json')
    package = output / 'design-review-evidence.zip'
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(path, path.name)
        for p in frozen.iterdir(): archive.write(p, 'evidence/' + p.name)
    publish(package, 'design_review_evidence', 'application/zip')
    return result


def local_footprint_libraries(source,manifest):
    table=source/'fp-lib-table'
    if not table.exists():return None
    tracked={row['path'] for row in manifest}
    if 'fp-lib-table' not in tracked:fail('封装表不在版本输入清单中。')
    tree=parse(table.read_text(encoding='utf8'));libraries={}
    if tree.kind!='fp_lib_table':fail('项目封装表格式不正确。')
    for row in tree.children('lib'):
        name=row.one('name').values[1];kind=row.one('type').values[1];uri=row.one('uri').values[1]
        if name in libraries or kind!='KiCad' or not uri.startswith('${KIPRJMOD}/'):fail('项目封装仅支持不重复的KiCad工程内相对库。')
        folder=within(source,uri[len('${KIPRJMOD}/'):])
        if not folder.is_dir():fail('项目冻结封装库不存在。')
        libraries[name]=folder
    return libraries


def handoff(config, revision, graph, review_job, output, check_cancel, publish):
    reviewed = review_job.get('result') or {}
    if review_job['status'] != 'completed' or review_job['kind'] != 'schematic.review' or not reviewed.get('pcbHandoffReady'):
        fail('PCB 交接需要已通过的原理图工程审查。')
    if review_job['projectId'] != revision['projectId'] or reviewed['revisionId'] != revision['id'] or reviewed['graphHash'] != graph['hash'] or reviewed['inputManifest'] != revision['manifest']:
        fail('工程审查与待交接版本不一致。')
    source = within(config.data, revision['relativePath'])
    verify_snapshot(source, revision['manifest'])
    if Path(revision['entrypoints']['schematic']).parent != Path('.'):
        fail('首版 PCB 交接仅支持根目录原理图；嵌套工程待接入。')
    local_libraries=local_footprint_libraries(source,revision['manifest'])
    bundle = output / 'handoff'
    shutil.copytree(source, bundle / 'source')
    verify_snapshot(bundle / 'source', revision['manifest'])
    # Library files are frozen here, not merely left pointing at global libraries.
    share = Path(config.kicad_cli).resolve().parent.parent / 'share/kicad/footprints'
    manifest, mapping = [], []
    first = read_schematic(config, revision, limit=300)
    for sheet in first['sheets']:
        offset = 0
        while True:
            page = read_schematic(config, revision, sheet['path'], offset, 300)
            for component in page['objects']:
                if component['type'] != 'symbol' or component['reference'].startswith('#'): continue
                check_cancel()
                props = {p['name']: p['value'] for p in component['properties']}
                footprint = props.get('Footprint', '')
                if footprint.count(':') != 1: fail('封装名称必须包含库名。')
                library, name = footprint.split(':')
                if any(c in library + name for c in '/\\') or library in {'.', '..'} or name in {'.', '..'}: fail('封装名称无效。')
                if local_libraries is None:path = within(share, library + '.pretty/' + name + '.kicad_mod')
                else:
                    if library not in local_libraries:fail('所用封装库未在当前工程封装表声明。')
                    path=within(local_libraries[library],name+'.kicad_mod')
                    if path.relative_to(source).as_posix() not in {r['path'] for r in revision['manifest']}:fail('所用封装不在冻结输入清单中。')
                tree = parse(path.read_text(encoding='utf8'))
                pads = sorted({n.values[1] for n in tree.children('pad') if n.values[1]})
                pins = sorted({p['number'] for p in component['pins']})
                if pads != pins: fail(component['reference'] + ' 符号引脚与封装焊盘不一致。')
                target = bundle / 'footprints' / (library + '.pretty') / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                mapping.append(dict(reference=component['reference'], value=props.get('Value'), footprint=footprint,
                                    pins=pins, pads=pads, sha256=digest(target), sourcePath=str(path)))
            if page['nextOffset'] is None: break
            offset = page['nextOffset']
    libraries = sorted({m['footprint'].split(':')[0] for m in mapping})
    table = '(fp_lib_table\n (version 7)\n' + ''.join(f' (lib (name "{name}") (type "KiCad") (uri "${{KIPRJMOD}}/../footprints/{name}.pretty") (options "") (descr "FormForge frozen handoff"))\n' for name in libraries) + ')\n'
    (bundle / 'source/fp-lib-table').write_text(table, encoding='utf8')
    netlist = output / 'native/netlist.xml'
    if read_graph(netlist)['hash'] != graph['hash']: fail('原生网表与连接分组不一致。')
    shutil.copyfile(netlist, bundle / 'netlist.xml')
    from .pcb_netlist import named_graph
    named_graph(graph, output / 'native/netlist.net')
    shutil.copyfile(output / 'native/netlist.net', bundle / 'netlist.net')
    native = json.loads((output / 'native/inspection.json').read_text(encoding='utf8'))
    if native['inputRevision'] != revision['id'] or not native['checksPassed']:
        fail('原生检查报告与交接版本不一致或未通过。')
    for name in ('erc.json', 'inspection.json'):
        shutil.copyfile(output / 'native' / name, bundle / name)
    (bundle / 'design-review.json').write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding='utf8')
    (bundle / 'connectivity.json').write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding='utf8')
    evidence = config.data / 'projects' / review_job['projectId'] / 'jobs' / review_job['id'] / 'evidence'
    for row in reviewed['sources']:
        path = within(evidence, row['path'])
        if digest(path) != row['sha256']: fail('冻结的审查依据摘要不一致。')
        target = bundle / 'evidence' / path.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, target)
        if digest(target) != row['sha256']: fail('交接依据复制时内容变化。')
    verify_snapshot(source,revision['manifest'])
    for path in sorted(bundle.rglob('*')):
        if path.is_file(): manifest.append(dict(path=path.relative_to(bundle).as_posix(), sha256=digest(path), size=path.stat().st_size))
    report = dict(revisionId=revision['id'], projectVersion=review_job['contextSnapshot']['version'], reviewJobId=review_job['id'], graphHash=graph['hash'],
                  componentCount=len(mapping), mapping=mapping, manifest=manifest, checksPassed=True,
                  nativeToolVersion=native['toolVersion'], nativeChecks=native['checks'],
                  boardCreated=False, routingCompleted=False, manufacturingApproved=False,
                  footprintModelsFrozen=False,
                  footprintSource=dict(uri='https://gitlab.com/kicad/libraries/kicad-footprints',
                    version='KiCad 10 installed library files; upstream commit unspecified',
                    license='Not independently verified; unchanged project-local KiCad source snapshot, not redistribution authorization'),
                  scope='原理图至 PCB 的连接交接，冻结封装与焊盘映射；尚无板框、布局、布线或制造验收。',
                  conditions=reviewed['conditions'], hardwareVerification=reviewed['hardwareVerification'])
    if local_libraries is not None:
        report['footprintSource']=dict(uri='formforge://revisions/'+revision['id']+'/fp-lib-table',version='Frozen project revision '+revision['id'],
            tableSha256=digest(source/'fp-lib-table'),license='Project-local source bytes; upstream origin/license not independently inferred from library name')
    report_path = output / 'pcb-handoff.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    publish(report_path, 'pcb_handoff_report', 'application/json')
    package = output / 'pcb-handoff.zip'
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(report_path, 'pcb-handoff.json')
        for row in manifest: archive.write(bundle / row['path'], row['path'])
    publish(package, 'pcb_handoff', 'application/zip')
    return report
