"""Native pin partitions and exact, bounded electrical change contracts.

Network names are presentation only: automatic KiCad names can change with UUIDs.
The hash binds every component pin and group, including isolated pins.
"""
import hashlib
import json
import xml.etree.ElementTree as ET

from ..store import DomainError


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def group(pins, name=None):
    pins = sorted(set(pins))
    return dict(id=fingerprint(pins), pins=[dict(reference=r, pin=p) for r, p in pins], name=name)


def keys(group):
    return tuple(sorted((p['reference'], p['pin']) for p in group['pins']))


def assemble(components, groups):
    components = sorted(components, key=lambda c: c['reference'])
    groups = sorted(groups, key=lambda g: g['id'])
    return dict(components=components, groups=groups,
                hash=fingerprint(dict(components=components, groups=[keys(g) for g in groups])))


def read_graph(path):
    root = ET.parse(path).getroot()
    libraries = {(p.attrib['lib'], p.attrib['part']): sorted({n.attrib['num'] for n in p.findall('pins/pin')})
                 for p in root.findall('libparts/libpart')}
    components, all_pins, seen, groups = [], set(), set(), []
    refs = set()
    for comp in root.findall('components/comp'):
        ref = comp.attrib['ref']
        if ref in refs:
            raise DomainError('invalid_input', '原生网表存在重复器件标号。')
        refs.add(ref)
        lib = comp.find('libsource')
        if lib is None or (lib.get('lib'), lib.get('part')) not in libraries:
            raise DomainError('invalid_input', '原生网表缺少完整器件引脚定义，不能核对电气目标。')
        pins = libraries[(lib.get('lib'), lib.get('part'))]
        components.append(dict(reference=ref, pins=pins))
        all_pins.update((ref, pin) for pin in pins)
    for net in root.findall('nets/net'):
        pins = {(n.attrib['ref'], n.attrib['pin']) for n in net.findall('node')}
        if not pins:
            continue
        if pins - all_pins or pins & seen:
            raise DomainError('invalid_input', '原生网表存在未知或重复分组引脚。')
        seen.update(pins)
        groups.append(group(pins, net.get('name')))
    # KiCad may omit intentionally unconnected pins from nets; preserve them as singletons.
    groups.extend(group([p], '(isolated)') for p in all_pins - seen)
    return assemble(components, groups)


def expected_graph(before, intent, additions, removals=None):
    if not intent:
        return before
    if intent['baselineHash'] != before['hash']:
        raise DomainError('version_conflict', '原生连接基线摘要已变化，请重新读取连接图。', 409)
    removed = intent['removeGroups']
    groups_by_id = {g['id']: g for g in before['groups']}
    if len(set(removed)) != len(removed) or set(removed) - groups_by_id.keys():
        raise DomainError('invalid_input', '要替换的连接分组重复或不属于基线。')
    removals = removals or []
    old_refs = {c['reference'] for c in before['components']}
    if len(set(removals)) != len(removals) or set(removals) - old_refs:
        raise DomainError('invalid_input', '删除器件标号重复或不属于基线。')
    components = [c for c in before['components'] if c['reference'] not in removals]
    refs = {c['reference'] for c in components}
    for item in additions:
        if item['reference'] in refs:
            raise DomainError('invalid_input', '新增器件标号重复。')
        refs.add(item['reference'])
        components.append(dict(reference=item['reference'], pins=sorted(item['pins'])))
    universe = {(c['reference'], p) for c in components for p in c['pins']}
    groups = [g for g in before['groups'] if g['id'] not in removed]
    for pins in intent['addGroups']:
        pairs = [(p['reference'], p['pin']) for p in pins]
        if not pairs or len(set(pairs)) != len(pairs):
            raise DomainError('invalid_input', '目标连接分组不能为空或含重复引脚。')
        groups.append(group(pairs))
    combined = [p for g in groups for p in keys(g)]
    if len(set(combined)) != len(combined):
        raise DomainError('invalid_input', '同一引脚不能出现在多个目标分组；必须完整替换原分组。')
    if set(combined) != universe:
        missing, unknown = sorted(universe - set(combined)), sorted(set(combined) - universe)
        raise DomainError('invalid_input', f'目标必须覆盖全部引脚，遗漏 {missing[:8]}，未知 {unknown[:8]}。')
    return assemble(components, groups)


def compare_graphs(before, actual, expected, intent):
    expected_groups = {g['id']: g for g in expected['groups']}
    actual_groups = {g['id']: g for g in actual['groups']}
    missing = [g for k, g in expected_groups.items() if k not in actual_groups]
    unexpected = [g for k, g in actual_groups.items() if k not in expected_groups]
    matched = actual['hash'] == expected['hash']
    return dict(policy='replace_groups' if intent else 'preserve', matched=matched,
                preserved=before['hash'] == actual['hash'], baselineHash=before['hash'],
                expectedHash=expected['hash'], actualHash=actual['hash'],
                expected=expected, actual=actual, missingGroups=missing, unexpectedGroups=unexpected,
                componentsMatched=actual['components'] == expected['components'],
                declaredRemovedGroups=len(intent['removeGroups']) if intent else 0,
                declaredAddedGroups=len(intent['addGroups']) if intent else 0)
