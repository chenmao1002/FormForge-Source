"""Bounded, source-preserving legacy Eeschema v4 to native document migration.

Symbol definitions must first be upgraded by the installed KiCad library CLI.
This translates document objects, never reconstructs a circuit from its netlist.
Unknown records fail explicitly; native netlist partitions are the acceptance.
"""
import base64
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET

from .native_syntax import parse
from .schematic import safe_string


NAMESPACE = uuid.UUID('1e0cb05f-aa78-4985-8eaf-c78b5ba97da3')


def tokens(line):
    result = []
    for token in re.findall(r'"(?:\\.|[^"\\])*"|[^\s]+', line):
        result.append(token[1:-1].replace('\\"', '"').replace('\\\\', '\\') if token.startswith('"') else token)
    return result


def mm(value):
    return format(round(float(value)*.0254, 6), '.6f').rstrip('0').rstrip('.') or '0'


def identity(value):
    return str(uuid.uuid5(NAMESPACE, value))


def legacy_uuid(timestamp):
    if not re.fullmatch(r'[0-9a-fA-F]{1,8}', timestamp):
        raise ValueError('Unsupported legacy symbol timestamp: '+timestamp)
    return str(uuid.UUID(timestamp.zfill(32)))


def orientation(matrix, mirror_axis='x'):
    a, b, c, d = matrix
    if a*a+c*c != 1 or b*b+d*d != 1 or a*b+c*d != 0:
        raise ValueError('Unsupported legacy symbol transformation: '+str(matrix))
    mirrored = a*d-b*c == 1
    # Native ERC pin-position probes confirm mirroring is applied after the
    # native rotation. A mirrored instance therefore reverses the rotation
    # sign relative to an unmirrored library-Y-up instance.
    angle = round(math.degrees(math.atan2(c if mirrored else -c, a))) % 360
    if mirrored and mirror_axis == 'y':angle = (angle+180) % 360
    return angle, mirror_axis if mirrored else None


def read_legacy(path):
    lines = Path(path).read_text(encoding='utf-8-sig').splitlines()
    if not lines or lines[0] != 'EESchema Schematic File Version 4':
        raise ValueError('Only explicit legacy Eeschema schematic version 4 is supported.')
    result = dict(components=[], objects=[], title={}, counts=Counter(), unknown=[])
    index = 1
    while index < len(lines):
        line = lines[index].strip();parts = tokens(line);index += 1
        if not parts:
            continue
        tag = parts[0]
        if tag == '$Comp':
            block = []
            while index < len(lines) and lines[index].strip() != '$EndComp':
                block.append(tokens(lines[index].strip()));index += 1
            if index == len(lines):raise ValueError('Unterminated legacy component.')
            index += 1;component = dict(fields=[], annotations=[])
            for row in block:
                if row[0] == 'L':component.update(libId=row[1], reference=row[2])
                elif row[0] == 'U':component.update(unit=int(row[1]), convert=int(row[2]), timestamp=row[3])
                elif row[0] == 'P':component['at'] = list(map(int, row[1:3]))
                elif row[0] == 'F':component['fields'].append(row)
                elif row[0] == 'AR':component['annotations'].append(row)
                elif len(row) == 4 and all(re.fullmatch(r'-?\d+', value) for value in row):component['matrix'] = list(map(int, row))
                elif len(row) == 3 and all(re.fullmatch(r'-?\d+', value) for value in row):pass
                else:result['unknown'].append(dict(tag='component:'+row[0], line=index))
            if component.get('convert') != 1:raise ValueError('Legacy alternate body style is not supported.')
            result['components'].append(component);result['counts']['symbol'] += 1
        elif tag == 'Text':
            if index == len(lines):raise ValueError('Missing legacy text body.')
            result['objects'].append(dict(kind=parts[1], header=parts, text=lines[index]));index += 1
            result['counts']['Text '+parts[1]] += 1
        elif tag in ('Wire', 'Entry'):
            if index == len(lines):raise ValueError('Missing legacy line coordinates.')
            result['objects'].append(dict(kind=' '.join(parts), points=list(map(int, tokens(lines[index])))));index += 1
            result['counts'][' '.join(parts)] += 1
        elif tag in ('Connection', 'NoConn'):
            result['objects'].append(dict(kind=tag, at=list(map(int, parts[2:4]))));result['counts'][tag] += 1
        elif tag == '$Bitmap':
            bitmap = dict(kind='bitmap');data = [];reading = False
            while index < len(lines) and lines[index].strip() != '$EndBitmap':
                row = tokens(lines[index].strip());index += 1
                if row[0] == 'Pos':bitmap['at'] = list(map(int, row[1:3]))
                elif row[0] == 'Scale':bitmap['scale'] = float(row[1])
                elif row[0] == 'Data':reading = True
                elif row[0] == 'EndData':reading = False
                elif reading:data.extend(row)
                else:result['unknown'].append(dict(tag='bitmap:'+row[0], line=index))
            if index == len(lines):raise ValueError('Unterminated legacy bitmap.')
            bitmap['data'] = base64.b64encode(bytes.fromhex(''.join(data))).decode('ascii')
            result['objects'].append(bitmap);result['counts']['bitmap'] += 1;index += 1
        elif tag == '$Descr':result['paper'] = parts[1]
        elif tag in ('Title', 'Date', 'Rev', 'Comp') or tag.startswith('Comment'):
            result['title'][tag] = parts[1] if len(parts) > 1 else ''
        elif tag.startswith('LIBS:') or tag in ('EELAYER', 'encoding', 'Sheet', '$EndDescr', '$EndSCHEMATC'):
            pass
        else:result['unknown'].append(dict(tag=tag, line=index))
    if result['unknown']:
        raise ValueError('Unsupported legacy records: '+json.dumps(result['unknown'], ensure_ascii=False))
    return result


def _effects(size, hidden=False, justify='', bold=False, italic=False):
    return f'(effects (font (size {size} {size})'+(' (bold yes)' if bold else '')+(' (italic yes)' if italic else '')+')'+(
        ' (justify '+justify+')' if justify else '')+(' (hide yes)' if hidden else '')+')'


def _symbol(component, definition, root_id, project, mirror_axis):
    angle, mirror = orientation(component['matrix'], mirror_axis)
    fields = [];reference = component['reference'];names = set()
    for field in component['fields']:
        number = int(field[1]);name = ('Reference', 'Value', 'Footprint', 'Datasheet')[number] if number < 4 else field[10] if len(field) > 10 else 'Field'+str(number)
        if name in names:name += ' ('+str(number)+')'
        names.add(name)
        if number == 0:reference = field[2]
        horizontal = field[8];vertical = field[9][0]
        justify = ' '.join([{'L':'left', 'R':'right'}.get(horizontal, ''), {'T':'top', 'B':'bottom'}.get(vertical, '')]).strip()
        hidden = bool(int(field[7], 16) & 1)
        style = field[9][1:]
        fields.append(f'(property {safe_string(name)} {safe_string(field[2])} (at {mm(field[4])} {mm(field[5])} {0 if field[3]=="H" else 90}) '
                      +_effects(mm(field[6]), hidden, justify, 'B' in style, 'I' in style)+')')
    pins = []
    for part in definition.children('symbol'):
        match = re.search(r'_(\d+)_(\d+)$', part.values[1])
        if not match:raise ValueError('Unknown upgraded library subunit: '+part.values[1])
        if int(match[1]) not in (0, component['unit']) or int(match[2]) not in (0, 1):continue
        for pin in part.children('pin'):
            number = str(pin.one('number').values[1])
            pins.append(f'(pin {safe_string(number)} (uuid {safe_string(identity(component["timestamp"]+":"+str(len(pins))))}))')
    at = component['at'];hidden = reference.startswith('#');stamp = legacy_uuid(component['timestamp'])
    return f'''(symbol (lib_id {safe_string(component['libId'])}) (at {mm(at[0])} {mm(at[1])} {angle})
      (unit {component['unit']}) {'(mirror '+mirror+')' if mirror else ''}
      (in_bom {'no' if hidden else 'yes'}) (on_board {'no' if hidden else 'yes'}) (dnp no) (uuid "{stamp}")
      {' '.join(fields)} {' '.join(pins)}
      (instances (project {safe_string(project)} (path "/{root_id}" (reference {safe_string(reference)}) (unit {component['unit']})))))'''


def convert(source, upgraded_libraries, output, mirror_axis='x'):
    """Write a new single-sheet native document; never overwrite legacy input."""
    source = Path(source);output = Path(output)
    if output.exists():raise ValueError('Use a fresh native schematic output path.')
    document = read_legacy(source);root_id = identity(hashlib.sha256(source.read_bytes()).hexdigest())
    definitions = {};embedded = [];library_counts = {}
    for name, library in upgraded_libraries.items():
        tree = parse(Path(library).read_text(encoding='utf8'));symbols = tree.children('symbol');library_counts[name] = len(symbols)
        for symbol in symbols:
            if symbol.children('extends'):raise ValueError('Derived legacy library aliases require an explicit resolved definition.')
            key = name+':'+symbol.values[1];definitions[key] = symbol
            embedded.append(re.sub(r'^\(symbol\s+"(?:\\.|[^"\\])*"', lambda match:'(symbol '+safe_string(key), symbol.raw, count=1))
    body = []
    for component in document['components']:
        if component['libId'] not in definitions:raise ValueError('Missing upgraded original symbol: '+component['libId'])
        body.append(_symbol(component, definitions[component['libId']], root_id, output.stem, mirror_axis))
    for index, obj in enumerate(document['objects']):
        key = identity(root_id+':object:'+str(index));kind = obj['kind']
        if kind in ('Connection', 'NoConn'):
            x, y = map(mm, obj['at']);body.append(f'({"junction" if kind=="Connection" else "no_connect"} (at {x} {y}) '+('(diameter 0) (color 0 0 0 0) ' if kind=='Connection' else '')+f'(uuid "{key}"))')
        elif kind in ('Wire Wire Line', 'Wire Bus Line', 'Wire Notes Line'):
            a, b, c, d = map(mm, obj['points']);tag = {'Wire Wire Line':'wire', 'Wire Bus Line':'bus', 'Wire Notes Line':'polyline'}[kind]
            body.append(f'({tag} (pts (xy {a} {b}) (xy {c} {d})) (stroke (width 0) (type default)) (uuid "{key}"))')
        elif kind == 'Entry Wire Line':
            a, b, c, d = obj['points'];body.append(f'(bus_entry (at {mm(a)} {mm(b)}) (size {mm(c-a)} {mm(d-b)}) (stroke (width 0) (type default)) (uuid "{key}"))')
        elif kind in ('Label', 'GLabel', 'HLabel', 'Notes'):
            h = obj['header'];tag = {'Label':'label', 'GLabel':'global_label', 'HLabel':'hierarchical_label', 'Notes':'text'}[kind]
            text = obj['text'].replace('\\n', '\n') if kind == 'Notes' else obj['text']
            shape = ''
            if kind in ('GLabel', 'HLabel'):
                choices = {'Input':'input', 'Output':'output', 'BiDi':'bidirectional', '3State':'tri_state', 'Passive':'passive'}
                if h[6] not in choices:raise ValueError('Unknown legacy label direction: '+h[6])
                shape = '(shape '+choices[h[6]]+')'
            body.append(f'({tag} {safe_string(text)} {shape} (at {mm(h[2])} {mm(h[3])} {int(h[4])*90}) '
                        +_effects(mm(h[5]), justify='left', italic='Italic' in h)+f' (uuid "{key}"))')
        elif kind == 'bitmap':
            data = ' '.join(safe_string(obj['data'][i:i+76]) for i in range(0, len(obj['data']), 76))
            body.append(f'(image (at {mm(obj["at"][0])} {mm(obj["at"][1])}) (scale {obj["scale"]}) (uuid "{key}") (data {data}))')
        else:raise ValueError('Unsupported legacy object: '+kind)
    title = document['title'];title_fields = []
    for old, new in (('Title', 'title'), ('Date', 'date'), ('Rev', 'rev'), ('Comp', 'company')):
        title_fields.append('('+new+' '+safe_string(title.get(old, ''))+')')
    for i in range(1, 5):title_fields.append(f'(comment {i} '+safe_string(title.get('Comment'+str(i), ''))+')')
    text = f'''(kicad_sch (version 20250114) (generator "formforge") (uuid "{root_id}")
      (paper {safe_string(document['paper'])}) (title_block {' '.join(title_fields)})
      (lib_symbols {' '.join(embedded)}) {' '.join(body)}
      (sheet_instances (path "/" (page "1"))) (embedded_fonts no))'''
    parse(text)
    output.parent.mkdir(parents=True, exist_ok=True);output.write_text(text, encoding='utf8')
    return dict(schemaVersion=1, sourceSha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                outputSha256=hashlib.sha256(output.read_bytes()).hexdigest(), counts=dict(document['counts']),
                libraries=library_counts, rootUuid=root_id, mirrorAxis=mirror_axis, unknownRecords=[],
                scope='Original document geometry plus native-upgraded original symbol definitions; native netlist/ERC remain required.')


def pin_partitions(path):
    """Exact electrical reference/pin sets; repeated same-number pins deduplicate."""
    source = Path(path).read_text(encoding='utf-8-sig')
    if source.lstrip().startswith('<'):
        root = ET.fromstring(source);references = sorted(c.attrib['ref'] for c in root.findall('components/comp'))
        groups = [set((node.attrib['ref'], node.attrib['pin']) for node in net.findall('node')) for net in root.findall('nets/net')]
    else:
        root = parse(source);references = sorted(str(c.one('ref').values[1]) for c in root.one('components').children('comp'))
        groups = [set((str(node.one('ref').values[1]), str(node.one('pin').values[1])) for node in net.children('node')) for net in root.one('nets').children('net')]
    return dict(references=references, partitions=sorted(tuple(sorted(group)) for group in groups if group))
