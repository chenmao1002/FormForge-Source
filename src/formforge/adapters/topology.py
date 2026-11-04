"""Plans native electrical edits without changing the input while validating."""
import math
import re
import uuid

from ..store import DomainError
from .importer import digest
from .native_syntax import Node
from .schematic import child_value, fail, location, number, object_id, properties, safe_string, sha, DEFAULT_WIRE_WIDTH_MM

TOPOLOGY_OPERATIONS = {'add_symbol', 'add_library_symbol', 'add_wire', 'remove_wire', 'add_junction', 'remove_junction',
                       'move_symbol', 'rotate_symbol', 'remove_symbol', 'add_label', 'remove_label', 'add_no_connect', 'remove_no_connect'}
TOPOLOGY_OPERATIONS.add('add_power_flag')
TOPOLOGY_OPERATIONS.update({'add_sheet','add_global_label'})


def descendants(node):
    yield node
    for child in node.values:
        if isinstance(child, Node):
            yield from descendants(child)


def symbol_pins(page, node, allow_power_flag=False, *, allow_complex=False):
    """Embedded pin geometry; complex geometry is opt-in for rigid block edits/readout."""
    library = page['tree'].one('lib_symbols')
    definitions = [s for s in library.children('symbol') if s.values[1] == child_value(node, 'lib_id')]
    if len(definitions) != 1:
        fail('器件缺少唯一的内嵌符号定义。')
    definition = definitions[0]
    if definition.children('extends') or (definition.children('power') and not allow_power_flag) or (node.children('mirror') and not allow_complex):
        fail('本阶段新增器件不支持继承符号、电源符号或镜像器件。')
    if not allow_complex and (str(child_value(node, 'unit')) != '1' or node.children('convert') or node.children('body_style')):
        fail('本阶段新增器件仅支持默认外形的单单元符号。')
    unit=int(child_value(node,'unit',1));body=int(child_value(node,'body_style',child_value(node,'convert',1)))
    mirror=child_value(node,'mirror')
    if unit<1 or body<1 or mirror not in {None,'x','y'}:fail('符号单元、外形或镜像设置不支持。')
    at = location(node)
    if len(at) != 3 or at[2] not in {0, 90, 180, 270}:
        fail('本阶段新增器件仅支持正交旋转。')
    pins = []
    for part in definition.children('symbol'):
        match = re.search(r'_(\d+)_(\d+)$', part.values[1])
        if not match or (not allow_complex and int(match[1]) > 1):
            fail('多单元或未知符号结构尚不支持新增。')
        if int(match[1]) not in (0,unit) or int(match[2]) not in (0,body):
            continue
        for pin in part.children('pin'):
            x, y, *_ = location(pin)
            angle = math.radians(at[2])
            # Library +Y points up; schematic +Y points down. Rotate before inversion.
            x, y = x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle)
            orientation=(float(location(pin)[2])+at[2])%360
            # Native mirror axes are in the rotated document frame.
            if mirror=='x':y=-y;orientation=-orientation
            if mirror=='y':x=-x;orientation=180-orientation
            pins.append(dict(number=str(child_value(pin, 'number')), name=child_value(pin, 'name'),
                             electricalType=pin.values[1], orientation=orientation%360,
                             at=[round(at[0] + x, 6), round(at[1] - y, 6)]))
    numbers = [p['number'] for p in pins]
    if not numbers or len(set(numbers)) != len(numbers):
        fail('重复编号或没有引脚的符号不支持新增。')
    if set(numbers) != {str(p.values[1]) for p in node.children('pin')}:
        fail('实例与内嵌符号的引脚定义不一致。')
    return pins


def clone_symbol(page, node, op):
    fields = properties(node)
    if not {'Reference', 'Value', 'Footprint'} <= fields.keys() or fields['Reference'].values[2].startswith('#'):
        fail('模板需要正常器件的标号、值和封装。')
    if child_value(node, 'in_bom') != 'yes' or child_value(node, 'on_board') != 'yes':
        fail('模板必须包含在 BOM 和电路板中。')
    allowed = {'lib_id', 'lib_name', 'at', 'unit', 'in_bom', 'on_board', 'dnp', 'uuid', 'property', 'pin', 'instances', 'fields_autoplaced'}
    if any(isinstance(n, Node) and n.kind not in allowed for n in node.values):
        fail('模板包含尚未支持的实例设置，不能可靠复制。')
    paths = [n for n in descendants(node) if n.kind == 'path']
    if len(paths) != 1 or len(node.one('instances').children('project')) != 1:
        fail('模板含多个工程实例，暂不支持新增。')
    pins = symbol_pins(page, node)
    origin = location(node)
    dx, dy = op['at'][0] - origin[0], op['at'][1] - origin[1]
    edits, new_id = [], str(uuid.uuid4())
    keep = {'Reference': op['reference'], 'Value': op['value'], 'Footprint': op['footprint'],
            'Datasheet': op.get('datasheet', '~'), 'Description': ''}
    for n in node.values:
        if not isinstance(n, Node):
            continue
        if n.kind == 'property':
            if n.values[1] not in keep:
                edits.append((n.start, n.end, ''))
                continue
            from .schematic import replace_string
            text = replace_string(n.raw, 2, keep[n.values[1]])
            # Reparse after the string replacement because offsets may have changed.
            from .native_syntax import parse
            parsed = parse(text)
            field_at = parsed.one('at')
            values = location(parsed)
            pos = f'(at {number(values[0] + dx)} {number(values[1] + dy)} {values[2]})'
            text = text[:field_at.start] + pos + text[field_at.end:]
            edits.append((n.start, n.end, text))
        elif n.kind == 'at':
            edits.append((n.start, n.end, f'(at {number(op["at"][0])} {number(op["at"][1])} {origin[2]})'))
        elif n.kind == 'fields_autoplaced':
            edits.append((n.start, n.end, ''))
        else:
            for nested in descendants(n):
                if nested.kind == 'uuid':
                    value = new_id if n.kind == 'uuid' else str(uuid.uuid4())
                    edits.append((nested.start, nested.end, '(uuid ' + safe_string(value) + ')'))
                elif nested.kind == 'reference':
                    edits.append((nested.start, nested.end, '(reference ' + safe_string(op['reference']) + ')'))
    text = node.raw
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start - node.start] + replacement + text[end - node.start:]
    return text, new_id, [dict(p, at=[round(p['at'][0] + dx, 6), round(p['at'][1] + dy, 6)]) for p in pins]


def plan_operation(pages, op, references, removed=None):
    page = pages[op['sheet']]
    removed = removed or set()
    def remaining(kind):
        return [n for n in page['tree'].children(kind) if (op['sheet'],object_id(n)) not in removed]
    if page['instanceCount'] != 1:
        fail('电气修改暂不支持重复实例化的图页。')
    node = None
    if 'objectId' in op:
        matches = [n for n in page['objects'] if object_id(n) == op['objectId']]
        if len(matches) != 1:
            fail('目标 UUID 不存在或不唯一。')
        node = matches[0]
        if sha(node.raw) != op['expectedHash']:
            raise DomainError('version_conflict', '对象内容已变化，请重新读取。', 409)
    elif digest(page['path']) != op['expectedSheetHash']:
        raise DomainError('version_conflict', '图页摘要已变化，请重新读取。', 409)
    kind = op['kind']
    change = dict(kind=kind, sheet=op['sheet'], instanceCount=1)
    if kind == 'set_document_date':
        from datetime import date
        try:
            date.fromisoformat(op['date'])
        except ValueError:
            fail('图纸日期必须是有效的 YYYY-MM-DD。')
        titles=page['tree'].children('title_block')
        if len(titles)!=1 or len(titles[0].children('date'))!=1:
            fail('当前操作要求已有唯一的标题栏日期。')
        target=titles[0].one('date')
        if target.values[1]==op['date']:
            fail('图纸日期没有变化。')
        change.update(before=target.values[1],after=op['date'])
        return [(target.start,target.end,'(date '+safe_string(op['date'])+')')],change
    if kind in {'remove_wire', 'remove_junction', 'remove_label', 'remove_no_connect'}:
        if node.kind != kind.removeprefix('remove_') and not(kind=='remove_label' and node.kind=='global_label'):
            fail('删除操作类型与目标对象不一致。')
        change.update(objectId=object_id(node), before=node.raw, after=None)
        return [(node.start, node.end, '')], change
    if kind in {'move_symbol', 'rotate_symbol', 'remove_symbol'}:
        if node.kind != 'symbol':
            fail('器件移动/删除只能作用于普通器件。')
        pins = symbol_pins(page, node)
        reference = properties(node)['Reference'].values[2]
        matches = [n for p in pages.values() for n in p['tree'].children('symbol')
                   if 'Reference' in properties(n) and properties(n)['Reference'].values[2].casefold() == reference.casefold()]
        if len(matches) != 1 or reference.startswith('#'):
            fail('多单元、重复标号或电源器件的移动/删除暂不支持。')
        change.update(objectId=object_id(node), reference=reference, pins=[p['number'] for p in pins])
        if kind == 'remove_symbol':
            references.remove(reference.casefold())
            change.update(before=dict(at=location(node), libraryId=child_value(node, 'lib_id')), after=None)
            return [(node.start, node.end, '')], change
        origin = location(node)
        if kind == 'rotate_symbol':
            from .schematic_transform import rotate_symbol
            edits, rotated = rotate_symbol(page,node,op['rotation'])
            change.update(before=origin,after=origin[:2]+[op['rotation']],pinPositions=rotated)
            return edits, change
        dx, dy = op['at'][0] - origin[0], op['at'][1] - origin[1]
        if dx == dy == 0:
            fail('器件位置没有改变。')
        edits = []
        for target in [node, *node.children('property')]:
            pos = target.one('at')
            values = location(target)
            replacement = f'(at {number(values[0] + dx)} {number(values[1] + dy)} {number(values[2])})'
            edits.append((pos.start, pos.end, replacement))
        for target in node.children('fields_autoplaced'):
            edits.append((target.start, target.end, ''))
        change.update(before=origin[:2], after=list(op['at']),
                      pinPositions=[dict(p,at=[round(p['at'][0]+dx,6),round(p['at'][1]+dy,6)]) for p in pins])
        return edits, change
    if kind == 'add_symbol':
        if node.kind != 'symbol':
            fail('新增器件需要已有器件作为符号模板。')
        ref = op['reference']
        if ref.casefold() in references:
            fail('器件标号已存在。')
        references.add(ref.casefold())
        text, new_id, pins = clone_symbol(page, node, op)
        change.update(objectId=new_id, templateObjectId=object_id(node), reference=ref,
                      before=None, after=dict(value=op['value'], footprint=op['footprint'], at=op['at']),
                      pins=[p['number'] for p in pins], pinPositions=pins, procurementMetadata='not-copied')
    elif kind == 'add_wire':
        points = [[round(float(v), 6) for v in p] for p in op['points']]
        segments, ids = [], []
        for a, b in zip(points, points[1:]):
            if a == b or (a[0] != b[0] and a[1] != b[1]):
                fail('新增导线必须由非零水平或垂直线段组成。')
            id = str(uuid.uuid4()); ids.append(id)
            pts = ' '.join('(xy ' + ' '.join(number(v) for v in p) + ')' for p in [a, b])
            segments.append(f'(wire (pts {pts}) (stroke (width {DEFAULT_WIRE_WIDTH_MM}) (type default)) (uuid "{id}"))')
        text = '\n\t'.join(segments)
        change.update(objectId=ids[0], segmentIds=ids, before=None, after=points)
    elif kind == 'add_junction':
        at = [round(float(v), 6) for v in op['at']]
        if any(location(n) == at for n in remaining('junction')):
            fail('此位置已有连接点。')
        id = str(uuid.uuid4())
        text = '(junction (at ' + ' '.join(number(v) for v in at) + f') (diameter 0) (color 0 0 0 0) (uuid "{id}"))'
        change.update(objectId=id, before=None, after=at)
    elif kind in {'add_label', 'add_global_label', 'add_no_connect', 'add_text'}:
        at = [round(float(v), 6) for v in op['at']]
        id = str(uuid.uuid4())
        pos = ' '.join(number(v) for v in at)
        if kind == 'add_no_connect':
            if any(location(n) == at for n in remaining('no_connect')):
                fail('此位置已有未连接标记。')
            text = f'(no_connect (at {pos}) (uuid "{id}"))'
        elif kind in {'add_label','add_global_label'}:
            native_kind='global_label' if kind=='add_global_label' else 'label'
            # KiCad normalizes text angles to 0/90; justification defines anchor side.
            rotation = op.get('rotation', 0)
            text_angle = rotation % 180
            justification = 'right' if rotation >= 180 else 'left'
            if any(location(n)[:2] == at and n.values[1] == op['text'] for n in remaining(native_kind)):
                fail('此位置已有相同网络标签。')
            if kind=='add_global_label':
                text=f'(global_label {safe_string(op["text"])} (shape bidirectional) (at {pos} {text_angle}) (effects (font (size 1.27 1.27)) (justify {justification})) (uuid "{id}") (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {pos} {text_angle}) (effects (font (size 1.27 1.27)) (hide yes))))'
            else:text = f'(label {safe_string(op["text"])} (at {pos} {text_angle}) (effects (font (size 1.27 1.27)) (justify {justification} bottom)) (uuid "{id}"))'
        else:
            text = f'(text {safe_string(op["text"])} (at {pos} 0) (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "{id}"))'
        change.update(objectId=id, before=None, after=dict(at=at,text=op.get('text')))
    else:
        fail('未知电气操作。')
    position = page['tree'].end - 1
    return [(position, position, '\n\t' + text + '\n')], change
