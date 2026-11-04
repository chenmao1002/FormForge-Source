"""Agent-declared layout groups; deterministic relative placement, not circuit inference."""
import math
from ..store import DomainError


def check_groups(groups,objects):
    ids=[g['id'] for g in groups]
    if len(set(ids))!=len(ids):raise DomainError('invalid_input','功能组 ID 重复。')
    by_id={g['id']:g for g in groups}
    for g in groups:
        template=g.get('repeatOf')
        if template and (template not in by_id or template==g['id'] or by_id[template].get('repeatOf')):
            raise DomainError('invalid_input','重复通道须引用本次声明的独立基准组，不支持引用链。')
    symbols={o['reference']:o for o in objects if o['type']=='symbol'}
    resolved={};checks=[]
    for g in groups:
        issues=[];members={m['role']:m for m in g['members']};actual={}
        if len(members)!=len(g['members']) or g['anchorRole'] not in members:
            raise DomainError('invalid_input','功能组角色无效。')
        for role,member in members.items():
            symbol=symbols.get(member['reference'])
            if not symbol:issues.append(dict(reason='missing_symbol',role=role,reference=member['reference']));continue
            if not symbol.get('pinPositions'):
                issues.append(dict(reason='unsupported_pin_geometry',role=role,reference=member['reference']));continue
            actual[role]=dict(reference=member['reference'],objectId=symbol['id'],sheet=symbol['sheet'],at=symbol['at'],
                              pins=[dict(number=p['number'],offset=[round(p['at'][i]-symbol['at'][i],6) for i in range(2)]) for p in symbol['pinPositions']])
            fixed=member.get('fixedAt')
            if fixed is not None and math.dist(symbol['at'][:2],fixed)>g.get('toleranceMm',.05)+1e-7:
                issues.append(dict(reason='fixed_position',role=role,expected=fixed,actual=symbol['at'][:2]))
        if len({m['sheet'] for m in actual.values()})>1:issues.append(dict(reason='members_span_sheets'))
        for pin in g.get('interfaces',[]):
            symbol=symbols.get(pin['reference'])
            if not symbol or pin['pin'] not in {p['number'] for p in symbol.get('pinPositions',[])}:
                issues.append(dict(reason='unknown_interface_pin',**pin))
        anchor=actual.get(g['anchorRole'])
        if anchor:
            for m in actual.values():m['relativeAt']=[round(m['at'][i]-anchor['at'][i],6) for i in range(2)]
        result=dict(kind='layout_group',title=g['title'],groupId=g['id'],intent=g['intent'],declaration=g,
                    actualMembers=actual,issues=issues,passed=not issues,
                    scope='Declared geometry only; interfaces are checked for membership, not completeness or electrical function.')
        resolved[g['id']]=result;checks.append(result)
    for g in groups:
        if not g.get('repeatOf'):continue
        row=resolved[g['id']];base=resolved[g['repeatOf']];a=row['actualMembers'];b=base['actualMembers'];issues=row['issues']
        if not base['passed']:issues.append(dict(reason='invalid_reference_group',referenceGroup=g['repeatOf']))
        if {m['reference'] for m in a.values()} & {m['reference'] for m in b.values()}:
            issues.append(dict(reason='overlapping_members'))
        if g['anchorRole']!=by_id[g['repeatOf']]['anchorRole'] or set(a)!=set(b):issues.append(dict(reason='role_set_mismatch'))
        for role in sorted(set(a)&set(b)):
            left,right=a[role],b[role]
            if 'relativeAt' not in left or 'relativeAt' not in right:continue
            if math.dist(left['relativeAt'],right['relativeAt'])>g.get('toleranceMm',.05)+1e-7:
                issues.append(dict(reason='relative_position',role=role,expected=right['relativeAt'],actual=left['relativeAt']))
            if left['at'][2:]!=right['at'][2:]:issues.append(dict(reason='orientation',role=role,expected=right['at'][2:],actual=left['at'][2:]))
            lp={p['number']:p['offset'] for p in left['pins']};rp={p['number']:p['offset'] for p in right['pins']}
            if set(lp)!=set(rp) or any(math.dist(lp[n],rp[n])>g.get('toleranceMm',.05)+1e-7 for n in set(lp)&set(rp)):
                issues.append(dict(reason='pin_geometry',role=role))
        row['passed']=not issues
    return checks
