"""Bind CLI-native PCB names to XML pin partitions without inventing escapes."""
from copy import deepcopy

from .connectivity import keys
from .native_syntax import parse
from ..store import DomainError


def named_graph(expected, path):
    if not path.is_file():
        raise DomainError('invalid_input', '交接缺少 KiCad 原生 PCB 网络名，请重新生成 PCB 交接。')
    try:
        tree = parse(path.read_text(encoding='utf8'))
        if tree.kind != 'export': raise ValueError('Not a native netlist')
        names, used_names, used_pins = {}, set(), set()
        for net in tree.one('nets').children('net'):
            pins = [(n.one('ref').values[1], n.one('pin').values[1]) for n in net.children('node')]
            if not pins: continue
            name = net.one('name').values[1]
            signature = tuple(sorted(pins))
            if not name or name in used_names or len(pins) != len(set(pins)) or set(pins) & used_pins:
                raise ValueError('Duplicate or empty net identity')
            names[signature] = name; used_names.add(name); used_pins.update(pins)
        exported = {keys(g) for g in expected['groups'] if g.get('name') not in (None, '(isolated)')}
        if names.keys() != exported: raise ValueError('Native and XML pin partitions differ')
        result = deepcopy(expected)
        for g in result['groups']:
            g['name'] = names.get(keys(g), '__FF_NC_' + g['id'][:16])
        if len({g['name'] for g in result['groups']}) != len(result['groups']):
            raise ValueError('Native and synthetic names collide')
        return result
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise DomainError('invalid_input', '原生 PCB 网表与 XML 完整引脚分组不一致，不能创建或同步 PCB。') from exc
