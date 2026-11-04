"""Explicit ordinary-copper ownership and independent zone generation floors."""
from ..store import DomainError


def replacements(basis, snapshot, policies):
    objects={o['id']:o for o in snapshot['objects']}
    protected={s['id'] for c in basis.get('pairedSections',[]) for rail in c['rails'] for s in rail['segments']}
    operations=[];selected=[];seen=set()
    for policy in policies:
        request=policy.get('replaceCopper')
        if not request:continue
        ids=[]
        for ref in request['objects']:
            id=ref['objectId'];obj=objects.get(id)
            if obj is None or obj['sha256']!=ref['expectedHash']:
                raise DomainError('version_conflict','普通重布对象不存在或旧SHA已变化。',409)
            if id in seen or id in protected or obj['type'] not in {'segment','via'} or obj['net']!=policy['net']:
                raise DomainError('invalid_input','普通重布仅可选择同网且不属于成组契约的独立直段/过孔，不可重复。')
            seen.add(id);ids.append(id)
            operations.append(dict(kind='remove_via' if obj['type']=='via' else 'remove_track',objectId=id,expectedHash=ref['expectedHash']))
        selected.append(dict(net=policy['net'],reason=request['reason'],removedObjectIds=ids))
    if len(operations)>500:raise DomainError('invalid_input','普通重布删除范围超过500项，需缩小批次。')
    return operations,selected


def zone_floors(rules):
    return (rules.get('zoneMinimumThickness') or rules['trackWidth'],rules.get('zoneThermalSpoke') or rules['trackWidth'])


def validate_zone_rules(snapshot, operations):
    selected=[o for o in operations if o['kind']=='set_zone_rules']
    if not selected:return
    if len(operations)!=1:raise DomainError('invalid_input','铜区生成下限须单独成批修改，不能隐式影响同批铜操作。')
    op=selected[0]
    if any(z['minimumThickness']<op['minimumThickness'] or z['thermalSpoke']<op['thermalSpoke'] for z in snapshot.get('zones',[])):
        raise DomainError('invalid_input','现有铜区低于明确的新生成下限；须先显式调整铜区。')
