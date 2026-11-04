"""Frozen, project-local symbol libraries and bounded native symbol placement.

Source/version/license are caller declarations, never a vendor authentication claim.
File and selected symbol digests are verified independently of those declarations.
"""
import hashlib
import json
import math
import re
import uuid
from pathlib import Path

from ..store import DomainError, now
from .importer import digest, reparse, verify_snapshot, within
from .native_syntax import parse
from .schematic import child_value, fail, number, properties, replace_string, safe_string, sha
from .symbol_inheritance import resolve_symbol


def parse_library(raw):
    if len(raw) > 12 * 1024 * 1024:
        fail('符号库超过首版 12 MB 上限，请拆分为较小库。')
    try:
        tree = parse(raw.decode('utf-8-sig'))
        if tree.kind != 'kicad_symbol_lib' or not child_value(tree, 'version'):
            fail('需要 KiCad 原生 .kicad_sym 符号库。')
        symbols = tree.children('symbol')
        names = [s.values[1] for s in symbols]
        if not symbols or len(symbols) > 5000 or len(names) != len(set(names)):
            fail('符号库为空、符号重名或超过 5000 个符号上限。')
        if any(not isinstance(n, str) or not n or ':' in n or any(ord(c) < 32 for c in n) for n in names):
            fail('符号名称无效或包含保留分隔符。')
        return tree
    except (ValueError, IndexError, TypeError, UnicodeError) as exc:
        raise DomainError('invalid_input', f'符号库格式错误：{exc}') from exc


def definition_pins(definition, at=(0, 0), rotation=0, allow_power_flag=False):
    from .topology import symbol_pins
    if allow_power_flag and definition.values[1] != 'PWR_FLAG':
        fail('此专用操作仅支持冻结库的 PWR_FLAG。')
    if definition.children('extends') or (definition.children('power') and not allow_power_flag):
        fail('继承符号和电源符号暂不支持放置。')
    if not allow_power_flag and (child_value(definition, 'in_bom', 'yes') != 'yes' or child_value(definition, 'on_board', 'yes') != 'yes'):
        fail('仅支持进入 BOM 和电路板的普通器件。')
    fields = properties(definition)
    if not {'Reference', 'Value'} <= fields.keys() or (fields['Reference'].values[2].startswith('#') and not allow_power_flag):
        fail('符号缺少普通器件的 Reference / Value 字段。')
    numbers = []
    for part in definition.children('symbol'):
        match = re.search(r'_(\d+)_(\d+)$', part.values[1])
        if not match or int(match[1]) > 1:
            fail('多单元或未知符号结构暂不支持放置。')
        if int(match[2]) <= 1:
            numbers.extend(str(child_value(p, 'number')) for p in part.children('pin'))
    pin_text = ' '.join('(pin ' + safe_string(p) + ')' for p in numbers)
    node = parse(f'(symbol (lib_id {safe_string(definition.values[1])}) (unit 1) '
                 f'(at {number(at[0])} {number(at[1])} {rotation}) {pin_text})')
    page = {'tree': parse('(kicad_sch (lib_symbols ' + definition.raw + '))')}
    pins = symbol_pins(page, node, allow_power_flag)
    if allow_power_flag and (len(pins) != 1 or pins[0]['electricalType'] != 'power_out'):
        fail('PWR_FLAG 必须仅含一个 power_out 引脚。')
    if any(not math.isfinite(v) or abs(v) > 20000 for p in pins for v in p['at']):
        fail('符号引脚坐标无效或超出首版范围。')
    return pins


def describe_symbol(symbol, tree=None):
    result = dict(name=symbol.values[1], sha256=sha(symbol.raw),
                  description=properties(symbol).get('Description').values[2] if 'Description' in properties(symbol) else '')
    try:
        definition, resolution = resolve_symbol(tree, symbol)
        if resolution:
            result.update(symbolResolution=resolution,
                          description=properties(definition).get('Description').values[2] if 'Description' in properties(definition) else '')
        result.update(supported=True, pins=definition_pins(definition),
                      coordinates='relative-to-placement; mm; +X right; +Y down; rotation=0')
    except (DomainError, ValueError, TypeError, IndexError, AttributeError) as exc:
        result.update(supported=False, limitation=str(exc))
        if symbol.values[1] == 'PWR_FLAG':
            try:
                result.update(specialOperation='add_power_flag',pins=definition_pins(symbol,allow_power_flag=True))
            except (DomainError,ValueError,TypeError,IndexError):
                pass
    return result


def external_library(source_path):
    requested = Path(source_path).expanduser()
    if not requested.is_absolute() or not requested.is_file() or requested.suffix.lower() != '.kicad_sym':
        fail('需要本机已有 .kicad_sym 文件的绝对路径。')
    if any(reparse(p) for p in [requested, *requested.parents]):
        fail('符号库来源不能经过符号链接或目录联接。')
    if requested.stat().st_size > 12 * 1024 * 1024:
        fail('符号库超过首版 12 MB 上限。')
    raw = requested.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    tree = parse_library(raw)
    if digest(requested) != actual:
        raise DomainError('input_changed', '探查期间外部库发生变化，请重试。', 409)
    return requested, raw, actual, tree


def probe_library(source_path):
    requested, raw, actual, tree = external_library(source_path)
    return dict(sourcePath=str(requested.resolve()), sha256=actual, size=len(raw),
                formatVersion=child_value(tree, 'version'), generator=child_value(tree, 'generator'),
                symbolCount=len(tree.children('symbol')),
                note='只读探查，没有导入或修改工程。发布版本、来源地址和许可仍需明确声明；导入时再次核对摘要。')


def import_library(source, entry, op, existing, job_id):
    requested, raw, actual, tree = external_library(op['sourcePath'])
    if actual != op['expectedSha256']:
        raise DomainError('input_changed', '外部符号库摘要与预期不一致，请核对文件版本。', 409)
    library_id = 'FF_' + actual[:16]
    if any(r['id'] == library_id for r in existing):
        fail('此库内容已冻结到当前版本，请复用现有 libraryId；更新库须使用新摘要。')
    folder = Path(entry).parent / 'formforge-libraries' / library_id
    destination = within(source, folder.as_posix())
    if destination.exists():
        fail('库目标目录已存在，拒绝覆盖。')
    table_path = within(source, (Path(entry).parent / 'sym-lib-table').as_posix())
    table = parse(table_path.read_text(encoding='utf-8-sig') if table_path.exists() else '(sym_lib_table (version 7))')
    if table.kind != 'sym_lib_table':
        fail('现有 sym-lib-table 格式无效。')
    if any(str(child_value(lib, 'name', '')).casefold() == library_id.casefold() for lib in table.children('lib')):
        fail('库名称与现有工程库冲突，拒绝覆盖。')
    relative_file = (folder / 'library.kicad_sym').as_posix()
    record = dict(id=library_id, sha256=actual, path=relative_file,
                  provenancePath=(folder / 'provenance.json').as_posix(),
                  sourcePath=str(requested.resolve()), sourceUri=op['sourceUri'], sourceVersion=op['sourceVersion'],
                  description=op['description'], license=op.get('license', '未声明'),
                  provenanceTrust='caller-declared; content-sha256-verified',
                  formatVersion=child_value(tree, 'version'), generator=child_value(tree, 'generator'),
                  symbolCount=len(tree.children('symbol')), importedAt=now(), importedByJobId=job_id)
    if op.get('registeredSource'):record['registeredSource']=op['registeredSource']
    uri = '${KIPRJMOD}/' + (Path('formforge-libraries') / library_id / 'library.kicad_sym').as_posix()
    registration = f'\n (lib (name {safe_string(library_id)})(type "KiCad")(uri {safe_string(uri)})(options "")(descr "FormForge frozen library"))\n'
    updated_table = table.source[:table.end - 1] + registration + table.source[table.end - 1:]
    parse(updated_table)
    destination.mkdir(parents=True)
    (destination / 'library.kicad_sym').write_bytes(raw)
    (destination / 'provenance.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')
    table_path.write_text(updated_table, encoding='utf8')
    if digest(requested) != actual:
        raise DomainError('input_changed', '导入期间外部库发生变化，候选不予采纳。', 409)
    return record


def frozen_library(source, records, library_id, parsed_libraries=None):
    matches = [r for r in records if r['id'] == library_id]
    if len(matches) != 1:
        fail('此版本没有指定的冻结库，请先导入并采纳库版本。')
    record = matches[0]
    raw = within(source, record['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['sha256']:
        raise DomainError('input_changed', '冻结库摘要已变化。', 409)
    provenance = json.loads(within(source, record['provenancePath']).read_text(encoding='utf8'))
    if provenance != record:
        raise DomainError('input_changed', '冻结库来源记录与版本记录不一致。', 409)
    # A batch may place hundreds of instances from the same large library.
    # Reuse only its parsed syntax; content and provenance are checked above
    # on every access, including cache hits. The caller owns the batch lifetime.
    if parsed_libraries is None:
        return record, parse_library(raw)
    if record['sha256'] not in parsed_libraries:
        parsed_libraries[record['sha256']] = parse_library(raw)
    return record, parsed_libraries[record['sha256']]


def recover_libraries(source, entry):
    """Round-trip only the explicit frozen-library namespace, not arbitrary JSON."""
    folder = within(source, (Path(entry).parent / 'formforge-libraries').as_posix())
    records = []
    if not folder.is_dir():
        return records
    try:
        for path in sorted(folder.glob('FF_*/provenance.json')):
            record = json.loads(path.read_text(encoding='utf8'))
            relative = path.relative_to(source).as_posix()
            expected_library = (path.parent / 'library.kicad_sym').relative_to(source).as_posix()
            if (record['id'] != path.parent.name or not re.fullmatch(r'FF_[0-9a-f]{16}', record['id'])
                    or record['path'] != expected_library or record['provenancePath'] != relative
                    or record['id'] != 'FF_' + record['sha256'][:16]):
                fail('导入工程中的库来源记录路径或标识不匹配。')
            for key in ['sourcePath', 'sourceUri', 'sourceVersion', 'description', 'license', 'importedAt', 'formatVersion', 'sha256']:
                if not isinstance(record[key], str) or not record[key]:
                    fail('导入工程中的库来源记录缺少必要字段。')
            _, tree = frozen_library(source, [record], record['id'])
            if record['symbolCount'] != len(tree.children('symbol')):
                fail('库来源记录的符号数量不符。')
            table = parse(within(source, (Path(entry).parent / 'sym-lib-table').as_posix()).read_text(encoding='utf-8-sig'))
            matches = [lib for lib in table.children('lib') if child_value(lib, 'name') == record['id']]
            uri = '${KIPRJMOD}/formforge-libraries/' + record['id'] + '/library.kicad_sym'
            if len(matches) != 1 or child_value(matches[0], 'uri') != uri or child_value(matches[0], 'type') != 'KiCad':
                fail('库来源记录与工程库映射不一致。')
            records.append(record)
        return records
    except (KeyError, ValueError, TypeError, OSError) as exc:
        raise DomainError('invalid_input', f'无法恢复工程内冻结库来源：{exc}') from exc


def read_libraries(config, revision, library_id=None, symbol=None, offset=0, limit=50):
    source = within(config.data, revision['relativePath'])
    verify_snapshot(source, revision['manifest'])
    records = revision.get('symbolLibraries', [])
    result = dict(revisionId=revision['id'], libraries=records)
    if library_id:
        record, tree = frozen_library(source, records, library_id)
        rows = tree.children('symbol')
        if symbol is not None:
            rows = [s for s in rows if s.values[1] == symbol]
            if not rows:
                fail('库中不存在指定符号。')
        result.update(library=record, symbols=[describe_symbol(s, tree) for s in rows[offset:offset + limit]],
                      total=len(rows), offset=offset, nextOffset=offset + limit if offset + limit < len(rows) else None)
    elif symbol:
        fail('查询符号时需要 library_id。')
    return result


def plan_library_symbol(source, records, page, entry, op, references, embedded, parsed_libraries=None):
    power_flag = op['kind'] == 'add_power_flag'
    op = dict(op)
    if power_flag:
        op.update(symbol='PWR_FLAG',value='PWR_FLAG',footprint='',rotation=0)
    if page['instanceCount'] != 1:
        fail('共享图页暂不支持新增器件。')
    if digest(page['path']) != op['expectedSheetHash']:
        raise DomainError('version_conflict', '图页摘要已变化，请重新读取。', 409)
    if op['reference'].casefold() in references:
        fail('器件标号已存在。')
    record, tree = frozen_library(source, records, op['libraryId'], parsed_libraries)
    if record['sha256'] != op['librarySha256']:
        raise DomainError('version_conflict', '库摘要不符合请求。', 409)
    matches = [s for s in tree.children('symbol') if s.values[1] == op['symbol']]
    if len(matches) != 1:
        fail('库中不存在指定符号。')
    definition = matches[0]
    if sha(definition.raw) != op['symbolSha256']:
        raise DomainError('version_conflict', '符号摘要不符合请求。', 409)
    definition, resolution = resolve_symbol(tree, definition)
    pins = definition_pins(definition, op['at'], op.get('rotation', 0),power_flag)
    lib_id = record['id'] + ':' + op['symbol']
    definition_text = replace_string(definition.raw, 1, lib_id)
    library = page['tree'].one('lib_symbols')
    definitions = [s for s in library.children('symbol') if s.values[1] == lib_id]
    patches = []
    if definitions and (len(definitions) != 1 or definitions[0].raw != definition_text):
        fail('已有内嵌定义与冻结库不一致，拒绝覆盖。')
    key = (op['sheet'], lib_id)
    if not definitions and key not in embedded:
        patches.append((library.end - 1, library.end - 1, '\n' + definition_text + '\n'))
        embedded.add(key)
    new_id = str(uuid.uuid4())
    x, y = op['at']
    fields = dict(Reference=op['reference'], Value=op['value'], Footprint=op['footprint'],
                  Datasheet=op.get('datasheet', '~'), Description=properties(definition).get('Description').values[2] if 'Description' in properties(definition) else '')
    field_text = []
    field_angle = op.get('rotation', 0) % 180
    for i, (name, value) in enumerate(fields.items()):
        # Upright readable labels; do not inherit distributor or purchase fields.
        px, py = (x + 5.08, y + (-1.27 if i == 0 else 1.27)) if i < 2 else (x, y)
        hidden = '' if i < 2 and not power_flag else ' (hide yes)'
        field_text.append(f'(property {safe_string(name)} {safe_string(value)} (at {number(px)} {number(py)} {field_angle}) '
                          f'(effects (font (size 1.27 1.27)){hidden}))')
    pin_text = '\n'.join(f'(pin {safe_string(p["number"])} (uuid "{uuid.uuid4()}"))' for p in pins)
    text = f'''(symbol (lib_id {safe_string(lib_id)}) (at {number(x)} {number(y)} {op.get('rotation', 0)})
      (unit 1) (in_bom {'no' if power_flag else 'yes'}) (on_board {'no' if power_flag else 'yes'}) (dnp no) (uuid "{new_id}")
      {' '.join(field_text)} {pin_text}
      (instances (project {safe_string(Path(entry).stem)} (path {safe_string(page['instancePaths'][0])}
        (reference {safe_string(op['reference'])}) (unit 1)))))'''
    parse(text)
    patches.append((page['tree'].end - 1, page['tree'].end - 1, '\n' + text + '\n'))
    references.add(op['reference'].casefold())
    change = dict(kind=op['kind'], sheet=op['sheet'], objectId=new_id, reference=op['reference'],
        libraryId=record['id'], librarySha256=record['sha256'], symbol=op['symbol'], symbolSha256=op['symbolSha256'],
        sourceVersion=record['sourceVersion'], sourceUri=record['sourceUri'], provenancePath=record['provenancePath'],
        pins=[p['number'] for p in pins], pinPositions=pins, before=None,
        after=dict(value=op['value'], footprint=op['footprint'], at=list(op['at']), rotation=op.get('rotation', 0)),
        procurementMetadata='not-copied')
    if resolution:
        change['symbolResolution'] = dict(resolution, embeddedDefinitionSha256=sha(definition_text))
    return patches, change
