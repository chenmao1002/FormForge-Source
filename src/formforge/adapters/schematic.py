"""Bounded native schematic edits. Unknown fields and untouched bytes survive."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from ..store import DomainError, now, uid
from .importer import digest, verify_snapshot, within
from .native_syntax import Node, parse

# KiCad's installed project templates use 6 mil for the default net-class wire.
DEFAULT_WIRE_WIDTH_MILS = 6
DEFAULT_WIRE_WIDTH_MM = 0.1524


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def fail(message):
    raise DomainError('invalid_input', message)


def child_value(node, kind, default=None):
    rows = node.children(kind)
    return rows[0].values[1] if len(rows) == 1 and len(rows[0].values) > 1 else default


def properties(node):
    return {p.values[1]: p for p in node.children('property') if len(p.values) >= 3}


def location(node):
    rows = node.children('at')
    return [float(v) for v in rows[0].values[1:]] if len(rows) == 1 else None


def wire_points(node):
    return [[float(p.values[1]), float(p.values[2])] for p in node.one('pts').children('xy')]


def object_id(node):
    return str(child_value(node, 'uuid', ''))


def load_pages(source: Path, entry: str):
    pages, counts, visits = {}, {}, 0

    def visit(relative, ancestors, instance_path=None):
        nonlocal visits
        visits += 1
        if visits > 2000 or len(ancestors) > 32:
            fail('原理图层级超过首版读取上限。')
        path = within(source, relative)
        relative = path.relative_to(source).as_posix()
        if relative in ancestors:
            fail('原理图存在循环图页引用。')
        counts[relative] = counts.get(relative, 0) + 1
        if relative not in pages:
            if path.stat().st_size > 12 * 1024 * 1024:
                fail('单张原理图超过首版 12 MB 解析上限。')
            raw = path.read_bytes()
            text = raw.decode('utf-8-sig')
            try:
                tree = parse(text)
                if tree.kind != 'kicad_sch':
                    fail('输入不是 KiCad 原理图。')
                objects = [n for n in tree.values if isinstance(n, Node) and object_id(n)]
                ids = [object_id(n) for n in objects]
                if len(ids) != len(set(ids)):
                    fail('同一图页存在重复 UUID，不能定位修改对象。')
            except (ValueError, IndexError, TypeError) as exc:
                raise DomainError('invalid_input', f'无法解析原理图 {relative}：{exc}') from exc
            pages[relative] = dict(path=path, raw=raw, text=text, tree=tree, objects=objects, instancePaths=[])
        instance_path = instance_path or '/' + object_id(pages[relative]['tree'])
        pages[relative]['instancePaths'].append(instance_path)
        for sheet in pages[relative]['tree'].children('sheet'):
            props = properties(sheet)
            if 'Sheetfile' not in props:
                fail('层级图页缺少 Sheetfile。')
            nested = within(source, str(Path(relative).parent / props['Sheetfile'].values[2]))
            visit(nested.relative_to(source).as_posix(), [*ancestors, relative], instance_path + '/' + object_id(sheet))

    visit(entry, [])
    for relative, page in pages.items():
        page['instanceCount'] = counts[relative]
    return pages


def describe_object(node, sheet):
    result = dict(id=object_id(node), sheet=sheet, type=node.kind, sha256=sha(node.raw), at=location(node))
    if node.kind == 'symbol':
        fields = properties(node)
        result.update(reference=fields.get('Reference').values[2] if 'Reference' in fields else '',
                      libraryId=child_value(node, 'lib_id'), unit=child_value(node, 'unit'),
                      properties=[dict(name=name, value=p.values[2], at=location(p)) for name, p in fields.items()],
                      pins=[dict(number=p.values[1], id=object_id(p)) for p in node.children('pin')],
                      operations=['set_property', 'move_field'])
    elif node.kind == 'wire':
        result.update(points=wire_points(node), operations=['reroute_wire', 'remove_wire'])
    elif node.kind == 'junction':
        result['operations'] = ['remove_junction']
    elif node.kind == 'no_connect':
        result['operations'] = ['remove_no_connect']
    elif node.kind == 'text':
        result.update(text=node.values[1], operations=['set_text','move_text'])
    elif node.kind in {'label', 'global_label', 'hierarchical_label'}:
        result.update(text=node.values[1], operations=['remove_label'] if node.kind in {'label','global_label'} else [])
    else:
        result['operations'] = []
    return result


# Bump when the native grammar or display/pin interpretation changes.
SCHEMATIC_READ_POLICY = 'native-syntax-schematic-display-readability-v7'


def _describe_nodes(page, revision, sheet):
    from .topology import symbol_pins
    descriptions = []
    for node in page['objects']:
        info = describe_object(node, sheet)
        if node.kind == 'symbol':
            for record in revision.get('symbolLibraries', []):
                if str(info['libraryId']).startswith(record['id'] + ':'):
                    info['libraryProvenance'] = dict(libraryId=record['id'], librarySha256=record['sha256'],
                        sourceVersion=record['sourceVersion'], sourceUri=record['sourceUri'], provenancePath=record['provenancePath'])
            try:
                info['pinPositions'] = symbol_pins(page, node, allow_power_flag=True, allow_complex=True)
                info['instanceReferences'] = [dict(path=p.values[1], reference=child_value(p,'reference'), unit=child_value(p,'unit'))
                    for instances in node.children('instances') for project in instances.children('project') for p in project.children('path')
                    if p.values[1] in page['instancePaths']]
                if page['instanceCount'] == 1:
                    # Preserve existing editing limits even when display geometry
                    # can describe mirrored/multi-unit symbols.
                    try:symbol_pins(page,node)
                    except DomainError:pass
                    else:info['operations'].extend(['add_symbol','move_symbol','rotate_symbol','remove_symbol'])
            except (DomainError, ValueError, IndexError, TypeError, AttributeError) as exc:
                info['templateLimitation'] = str(exc)
        descriptions.append(info)
    return descriptions


def _display_snapshot(source, entry, revision):
    pages = load_pages(source, entry)
    expected = {within(source, item['path']): item['sha256'] for item in revision['manifest']}
    result = dict(sheets=[], pages={})
    for relative, page in pages.items():
        # Bind what was actually parsed, including every hierarchical page. The
        # before/after disk checks alone cannot detect a changed-and-restored read.
        raw_hash = hashlib.sha256(page['raw']).hexdigest()
        if expected.get(page['path']) != raw_hash:
            raise DomainError('input_changed', '原理图解析内容与版本清单不一致。', 409)
        result['sheets'].append(dict(path=relative, sha256=raw_hash, instanceCount=page['instanceCount'],
                                     objectCount=len(page['objects'])))
        titles = page['tree'].children('title_block')
        document = dict(title=child_value(titles[0], 'title'), date=child_value(titles[0], 'date')) if titles else {}
        from .schematic_policy import feedback
        objects = _describe_nodes(page, revision, relative)
        readability=feedback(objects)
        from .schematic_functional_feedback import analyze
        readability['functionalGroups']=analyze(source,entry,relative,page,objects,readability,revision.get('functionalGroups'))
        result['pages'][relative] = dict(document=document, objects=objects, readability=readability)
    return result


def read_schematic(config, revision, sheet=None, offset=0, limit=100, *, cache=None):
    source = within(config.data, revision['relativePath'])
    verify_snapshot(source, revision['manifest'])
    entry = revision['entrypoints'].get('schematic')
    if not entry:
        fail('该版本没有原理图入口。')
    from .schematic_read_cache import cache_key
    key = cache_key(source, revision, SCHEMATIC_READ_POLICY) if cache is not None else None
    snapshot = cache.get(key) if cache is not None else None
    missed = snapshot is None
    if missed:
        snapshot = _display_snapshot(source, entry, revision)
    selected = sheet or entry
    if selected not in snapshot['pages']:
        fail('图页不属于该版本的原理图层级。')
    page = snapshot['pages'][selected]
    objects = page['objects']
    result = dict(revisionId=revision['id'], sheet=selected, units='mm', document=page['document'],
                  readability=page['readability'],
                  sheets=snapshot['sheets'], objects=objects[offset:offset + limit],
                  total=len(objects), offset=offset, nextOffset=offset + limit if offset + limit < len(objects) else None,
                  connectivityPolicy='preserve-or-explicit-replace-groups',
                  note='新增器件可复用同页模板或已冻结外部库的普通单单元符号；电气操作要求显式连接目标且不支持共享图页。')
    verify_snapshot(source, revision['manifest'])
    if cache is not None and missed:
        cache.put(key, snapshot)
    return result


def number(value):
    return format(float(value), '.6f').rstrip('0').rstrip('.') or '0'


def safe_string(value):
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        fail('文字包含不支持的控制字符。')
    return json.dumps(value, ensure_ascii=False)


def replace_string(raw, index, value):
    tokens = list(re.finditer(r'"(?:\\.|[^"\\])*"|[^\s()]+', raw))
    token = tokens[index]
    return raw[:token.start()] + safe_string(value) + raw[token.end():]


def replace_child(parent, child, replacement):
    return parent.raw[:child.start - parent.start] + replacement + parent.raw[child.end - parent.start:]


def apply_operations(source: Path, entry: str, operations, check_cancel, libraries=None):
    from .topology import TOPOLOGY_OPERATIONS, plan_operation
    pages = load_pages(source, entry)
    if any(op['kind'] in {'tidy_fields','connect_local_labels','move_group'} for op in operations) and len(operations)!=1:
        fail('自动字段整理须单独成批，避免与同页修改使用不同几何状态。')
    patches, changes, touched, embedded, removed = {}, [], set(), set(), set()
    new_files={}
    references = {str(properties(n)['Reference'].values[2]).casefold() for p in pages.values()
                  for n in p['tree'].children('symbol') if 'Reference' in properties(n)}
    parsed_libraries = {}
    for op in operations:
        check_cancel()
        sheet = op['sheet']
        if sheet not in pages:
            fail('修改图页不在原理图层级内。')
        page = pages[sheet]
        if op['kind'] in {'tidy_fields','connect_local_labels','move_group'}:
            if hashlib.sha256(page['raw']).hexdigest()!=op['expectedSheetHash']:
                raise DomainError('version_conflict','图页已变化，请重新读取后整理字段。',409)
            if page['instanceCount']!=1:fail('共享实例图页不能自动整理字段。')
            if op['kind']=='move_group':
                from .schematic_transform import move_group as plan
            elif op['kind']=='tidy_fields':
                from .schematic_fields import plan
            else:
                from .schematic_connections import plan
            edits,change=plan(page,op,check_cancel)
            patches.setdefault(sheet,[]).extend(edits);changes.append(change)
            continue
        if op['kind']=='add_sheet':
            from .schematic_hierarchy import plan_sheet
            edits,change=plan_sheet(source,entry,pages,op,new_files)
            patches.setdefault(sheet,[]).extend(edits);changes.append(change)
            continue
        if op['kind'] in {'add_library_symbol','add_power_flag'}:
            from .symbol_library import plan_library_symbol
            edits, change = plan_library_symbol(source, libraries or [], page, entry, op, references, embedded, parsed_libraries)
            patches.setdefault(sheet, []).extend(edits)
            changes.append(change)
            continue
        if op['kind'] in TOPOLOGY_OPERATIONS or op['kind'] in {'add_text','set_document_date'}:
            if op['kind'].startswith('remove_'):
                key = (sheet, op['objectId'], '*')
                if key in touched:
                    fail('同一批次不能重复修改或删除同一对象。')
                touched.add(key)
            edits, change = plan_operation(pages, op, references, removed)
            patches.setdefault(sheet, []).extend(edits)
            changes.append(change)
            if op['kind'].startswith('remove_'):
                removed.add((sheet,op['objectId']))
            continue
        matches = [n for n in page['objects'] if object_id(n) == op['objectId']]
        if len(matches) != 1:
            fail('对象 UUID 不存在或不唯一，请重新读取原理图。')
        node = matches[0]
        if sha(node.raw) != op['expectedHash']:
            raise DomainError('version_conflict', '对象内容已变化，请读取当前版本后重新修改。', 409)
        # Different properties can share a symbol; touching the same field twice
        # is rejected instead of depending on caller operation order.
        key = (sheet, object_id(node), op.get('property', '*'))
        if key in touched:
            fail('同一批次不能重复修改同一对象字段。')
        touched.add(key)
        kind = op['kind']
        change = dict(kind=kind, sheet=sheet, objectId=object_id(node), instanceCount=page['instanceCount'])
        if kind in {'set_property', 'move_field'}:
            if node.kind != 'symbol':
                fail('属性操作只能作用于器件。')
            fields = properties(node)
            field = fields.get(op['property'])
            if field is None:
                fail('此器件没有指定属性，首版只修改已有字段。')
            change.update(reference=fields.get('Reference').values[2] if 'Reference' in fields else '', property=op['property'])
            if kind == 'set_property':
                if change['reference'].startswith('#') or str(child_value(node, 'lib_id', '')).startswith('power:'):
                    fail('电源符号属性可能改变网络，当前不支持此操作。')
                reference = change['reference']
                same_ref = [n for p in pages.values() for n in p['tree'].children('symbol')
                            if 'Reference' in properties(n) and properties(n)['Reference'].values[2] == reference]
                if len(same_ref) != 1:
                    fail('多单元或重复参考标号的属性修改需要联动处理，当前不支持。')
                replacement = replace_string(field.raw, 2, op['value'])
                target = field
                change.update(before=field.values[2], after=op['value'])
            else:
                target = field.one('at')
                before = [float(v) for v in target.values[1:3]]
                after = [round(float(v), 6) for v in op['at']]
                angle = op.get('rotation')
                if angle is None:
                    angle = target.values[3]
                else:
                    if angle not in (0, 90):fail('字段角度仅支持0或90度。')
                    change.update(beforeRotation=float(target.values[3]),afterRotation=angle)
                replacement = '(at ' + ' '.join(number(v) for v in after) + ' ' + str(angle) + ')'
                change.update(before=before, after=after)
        elif kind == 'set_text':
            if node.kind != 'text':
                fail('set_text 只修改说明文字，不修改网络标签。')
            target = node
            replacement = replace_string(node.raw, 1, op['text'])
            change.update(before=node.values[1], after=op['text'])
        elif kind == 'move_text':
            if node.kind!='text':fail('move_text 只移动说明文字，不移动网络标签。')
            at=location(node);target=node.one('at')
            replacement=f'(at {number(op["at"][0])} {number(op["at"][1])} {number(at[2])})'
            change.update(before=at[:2],after=list(op['at']))
        elif kind == 'reroute_wire':
            if node.kind != 'wire':
                fail('导线路径操作只能作用于 wire。')
            before = wire_points(node)
            after = [[round(float(v), 6) for v in point] for point in op['points']]
            if len(before) != 2 or after[0] != before[0] or after[-1] != before[-1]:
                fail('导线重绘必须保持原来的两个端点及顺序。')
            for a, b in zip(after, after[1:]):
                if a == b or (a[0] != b[0] and a[1] != b[1]):
                    fail('导线必须由非零长度的水平或垂直线段组成。')
            segments, ids = [], []
            for index, (a, b) in enumerate(zip(after, after[1:])):
                segment = replace_child(node, node.one('pts'), '(pts ' + ' '.join('(xy ' + ' '.join(number(v) for v in p) + ')' for p in (a, b)) + ')')
                segment_node = parse(segment)
                strokes = segment_node.children('stroke')
                if strokes:
                    width = strokes[0].one('width')
                    if float(width.values[1]) == 0:
                        segment = replace_child(segment_node, width, f'(width {DEFAULT_WIRE_WIDTH_MM})')
                segment_id = object_id(node) if index == 0 else str(uuid.uuid4())
                if index:
                    segment_node = parse(segment)
                    segment = replace_child(segment_node, segment_node.one('uuid'), '(uuid ' + safe_string(segment_id) + ')')
                segments.append(segment)
                ids.append(segment_id)
            target, replacement = node, '\n\t'.join(segments)
            change.update(before=before, after=after, segmentIds=ids)
        else:
            fail('未知原理图操作。')
        if change['before'] == change['after'] and change.get('beforeRotation') == change.get('afterRotation'):
            fail('操作没有改变内容，请提交实际修改。')
        patches.setdefault(sheet, []).append((target.start, target.end, replacement))
        changes.append(change)
    # All preconditions are validated before any file changes are written.
    prepared = {}
    for sheet, edits in patches.items():
        page = pages[sheet]
        text = page['text']
        previous = len(text)
        for start, end, replacement in sorted(edits, reverse=True):
            if end > previous:
                fail('修改范围重叠，不能原子应用。')
            text = text[:start] + replacement + text[end:]
            previous = start
        parse(text)
        encoded = text.encode('utf-8')
        if page['raw'].startswith(b'\xef\xbb\xbf'):
            encoded = b'\xef\xbb\xbf' + encoded
        prepared[sheet] = encoded
    for sheet, encoded in prepared.items():
        check_cancel()
        pages[sheet]['path'].write_bytes(encoded)
    for relative,record in new_files.items():
        check_cancel()
        within(source,relative).write_bytes(record['data'])
    return changes


def create_candidate(config, parent, job, check_cancel):
    source = within(config.data, parent['relativePath'])
    verify_snapshot(source, parent['manifest'])
    entry = parent['entrypoints'].get('schematic')
    if not entry:
        fail('该版本没有原理图。')
    revision_id = uid('r')
    destination = config.data / 'projects' / parent['projectId'] / 'revisions' / revision_id / 'source'
    shutil.copytree(source, destination)
    verify_snapshot(destination, parent['manifest'])
    libraries = list(parent.get('symbolLibraries', []))
    footprints = list(parent.get('footprintLibraries', []))
    operations = job['params']['operations']
    paths = {p['path'] for p in parent['manifest']}
    functional_groups=parent.get('functionalGroups')
    imports = [op for op in operations if op['kind'] == 'import_symbol_library']
    footprint_imports = [op for op in operations if op['kind'] == 'import_footprint']
    if footprint_imports:
        if len(operations) != 1:fail('封装导入必须单独创建版本，采纳后再明确器件Footprint。')
        from .footprint_library import import_footprint
        record,added_paths=import_footprint(config,destination,entry,footprint_imports[0],footprints,job['id'])
        footprints.append(record);paths.update(added_paths)
        changes=[dict(kind='import_footprint',before=None,after=record)]
    elif imports:
        if len(operations) != 1:
            fail('库导入必须单独创建版本；采纳后再从库中放置器件。')
        from .symbol_library import import_library
        record = import_library(destination, entry, imports[0], libraries, job['id'])
        libraries.append(record)
        paths.update([record['path'], record['provenancePath'], (Path(entry).parent / 'sym-lib-table').as_posix()])
        changes = [dict(kind='import_symbol_library', libraryId=record['id'], before=None, after=record)]
    elif any(op['kind']=='auto_layout' for op in operations):
        if len(operations)!=1:fail('自动排版须单独成批创建候选。')
        from .schematic_layout import layout
        report,added=layout(config,destination,entry,operations[0],check_cancel)
        from .schematic_functional_feedback import declarations
        functional_groups=declarations(destination,entry,operations[0],functional_groups)
        changes=[report];paths.update(added)
    else:
        changes = apply_operations(destination, entry, operations, check_cancel, libraries)
        paths.update(c['after']['path'] for c in changes if c['kind']=='add_sheet')
    verify_snapshot(source, parent['manifest'])
    manifest = [dict(path=p, sha256=digest(within(destination, p)), size=within(destination, p).stat().st_size) for p in sorted(paths)]
    return dict(id=revision_id, projectId=parent['projectId'], backend='kicad10', origin='edited',
                parentRevisionId=parent['id'], createdByJobId=job['id'], sourceDirectory=parent.get('sourceDirectory'),
                relativePath=destination.relative_to(config.data).as_posix(), entrypoints=parent['entrypoints'],
                manifest=manifest, totalBytes=sum(p['size'] for p in manifest), createdAt=now(),
                message=job['params']['message'], changes=changes, validation={'status': 'pending'},
                connectivityIntent=job['params'].get('connectivity'), symbolLibraries=libraries, footprintLibraries=footprints,
                designSpecification=parent.get('designSpecification'),functionalGroups=functional_groups,
                downstreamRequiresReview=['pcb', 'mechanical', 'firmware', 'manufacturing'])


def native_connectivity(path):
    root = ET.parse(path).getroot()
    references = sorted(c.attrib['ref'] for c in root.findall('components/comp'))
    partitions = sorted(tuple(sorted((n.attrib['ref'], n.attrib['pin']) for n in net.findall('node')))
                        for net in root.findall('nets/net'))
    return dict(references=references, partitions=partitions)


def compare_revisions(before, after):
    a = {p['path']: p for p in before['manifest']}
    b = {p['path']: p for p in after['manifest']}
    files = [dict(path=p, beforeSha256=a.get(p, {}).get('sha256'), afterSha256=b.get(p, {}).get('sha256'))
             for p in sorted(a.keys() | b.keys()) if a.get(p) != b.get(p)]
    return dict(beforeRevisionId=before['id'], afterRevisionId=after['id'], files=files,
                changes=after.get('changes', []) if after.get('parentRevisionId') == before['id'] else [],
                validation=after.get('validation'), note='文件摘要比较；详细操作摘要只在直接父子版本间提供。')
