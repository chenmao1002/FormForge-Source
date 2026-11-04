"""Resolve bounded KiCad property inheritance without changing the frozen library."""
import re

from .native_syntax import Node, parse
from .schematic import fail, safe_string, sha


MAX_CHAIN = 32


def _fields(symbol):
    fields = {}
    for field in symbol.children('property'):
        if (len(field.values) < 3 or not all(isinstance(v, str) for v in field.values[1:3])
                or not field.values[1] or field.values[1] in fields):
            fail('继承链字段格式无效或名称重复。')
        fields[field.values[1]] = field
    return fields


def _rename_unit(unit, root_name, leaf_name):
    # Rebuild only the wrapper; retain original graphic/pin node bytes.
    if (len(unit.values) < 2 or not isinstance(unit.values[1], str)
            or not re.fullmatch(re.escape(root_name) + r'_\d+_\d+', unit.values[1])
            or any(not isinstance(v, Node) for v in unit.values[2:])):
        fail('继承父符号含未知子单元结构。')
    name = leaf_name + unit.values[1][len(root_name):]
    return '(symbol ' + safe_string(name) + '\n' + '\n'.join(v.raw for v in unit.values[2:]) + ')'


def resolve_symbol(tree, symbol):
    """Return a self-contained definition and leaf-to-root evidence, or original bytes.

    Only same-library property overrides are accepted. Unknown derived settings,
    graphics and pins are rejected instead of being silently discarded.
    """
    if not symbol.children('extends'):
        return symbol, None
    if tree is None:
        fail('解析继承符号需要完整的同库上下文。')
    by_name = {item.values[1]: item for item in tree.children('symbol')}
    chain, seen = [], set()
    current = symbol
    while True:
        name = current.values[1]
        if name in seen:
            fail('符号继承链存在循环。')
        if len(chain) >= MAX_CHAIN:
            fail(f'符号继承链超过 {MAX_CHAIN} 层上限。')
        seen.add(name)
        chain.append(current)
        _fields(current)
        parents = current.children('extends')
        if not parents:
            break
        if (len(parents) != 1 or len(parents[0].values) != 2
                or not isinstance(parents[0].values[1], str) or not parents[0].values[1]
                or ':' in parents[0].values[1]):
            fail('继承只支持唯一且明确的同库父符号。')
        if any(not isinstance(v, Node) for v in current.values[2:]):
            fail('派生符号含未知结构。')
        for part in current.values[2:]:
            if part.kind not in {'extends', 'property', 'embedded_fonts'}:
                fail('派生符号只支持属性覆盖，不能修改图形、引脚或其他设置。')
            if part.kind == 'embedded_fonts' and part.values != ['embedded_fonts', 'no']:
                fail('派生符号暂不支持内嵌字体。')
        if len(current.children('embedded_fonts')) > 1:
            fail('派生符号内嵌字体设置重复。')
        parent_name = parents[0].values[1]
        if parent_name not in by_name:
            fail('冻结库中缺少继承父符号：' + parent_name)
        current = by_name[parent_name]

    root, leaf_name = chain[-1], symbol.values[1]
    if any(not isinstance(v, Node) for v in root.values[2:]):
        fail('继承父符号含未知结构。')
    fields = {}
    for item in reversed(chain):
        fields.update(_fields(item))  # Explicit empty values override; absent ones inherit.
    parts = [field.raw for field in fields.values()]
    for part in root.values[2:]:
        if part.kind == 'property':
            continue
        parts.append(_rename_unit(part, root.values[1], leaf_name) if part.kind == 'symbol' else part.raw)
    raw = '(symbol ' + safe_string(leaf_name) + '\n' + '\n'.join(parts) + ')'
    resolved = parse(raw)
    evidence = dict(kind='same-library-property-inheritance-v1', baseSymbol=root.values[1],
                    chain=[dict(name=item.values[1], sha256=sha(item.raw)) for item in chain],
                    resolvedSha256=sha(raw))
    return resolved, evidence
