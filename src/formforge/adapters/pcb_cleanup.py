"""Remove only straight copper wholly covered by a same-net/layer/width segment."""
from collections import defaultdict
from math import gcd

from .native_syntax import parse
from ..store import DomainError

POLICY='contained-collinear-copper-v1'


def locked_ids(path):
    tree=parse(path.read_text(encoding='utf-8-sig'))
    return {n.one('uuid').values[1] for n in tree.children('segment')
            if 'locked' in n.values or any(x.values[1:]!=['no'] for x in n.children('locked'))}


def plan(snapshot, basis, nets, locked=(), check_cancel=lambda:None):
    if not nets or len(nets)!=len(set(nets)) or any(not n for n in nets):
        raise DomainError('invalid_input','自动清理需明确不重复的既有网络。')
    known={p['net'] for f in snapshot['footprints'] for p in f['pads'] if p.get('netCode')}
    if not set(nets)<=known:raise DomainError('invalid_input','清理网络不属于当前原生焊盘网络。')
    protected_ids={s['id'] for c in basis.get('pairedSections',[]) for r in c['rails'] for s in r['segments']}
    protected_nets={r['net'] for c in basis.get('pairedSections',[]) for r in c['rails']}
    locked=set(locked);groups=defaultdict(list);skipped=[]
    for t in snapshot['tracks']:
        check_cancel()
        if t['net'] not in nets:continue
        reason='paired_contract_net' if t['net'] in protected_nets or t['id'] in protected_ids else 'not_straight_segment' if t['type']!='segment' else None
        if reason:skipped.append(dict(objectId=t['id'],net=t['net'],reason=reason));continue
        a,b=[tuple(round(v*1_000_000) for v in t[k]) for k in ('start','end')]
        dx,dy=b[0]-a[0],b[1]-a[1];divisor=gcd(dx,dy)
        if not divisor:skipped.append(dict(objectId=t['id'],net=t['net'],reason='zero_length'));continue
        dx,dy=dx//divisor,dy//divisor
        if dx<0 or (dx==0 and dy<0):dx,dy=-dx,-dy
        key=(t['net'],t['layer'],round(t['width']*1_000_000),dx,dy,dx*a[1]-dy*a[0])
        lo,hi=sorted((dx*a[0]+dy*a[1],dx*b[0]+dy*b[1]))
        groups[key].append((lo,hi,t))
    removals=[]
    for entries in groups.values():
        kept=[]
        # Longest first, then stable UUID. Every removed segment has a retained
        # covering segment; no chained deletion or union-of-fragments reasoning.
        for lo,hi,t in sorted(entries,key=lambda e:(-(e[1]-e[0]),not(e[2]['id'] in locked or e[2].get('locked',False)),e[2]['id'])):
            check_cancel()
            covering=next((p for left,right,p in kept if left<=lo and hi<=right),None)
            if covering and t['id'] not in locked and not t.get('locked',False):
                removals.append(dict(objectId=t['id'],coveredBy=covering['id'],net=t['net'],layer=t['layer'],
                    width=t['width'],start=t['start'],end=t['end'],reason='same_width_collinear_containment'))
            else:
                kept.append((lo,hi,t))
                if covering:skipped.append(dict(objectId=t['id'],net=t['net'],reason='locked'))
    if len(removals)>500:raise DomainError('invalid_input','冗余铜超过500项，请缩小明确网络批次。')
    objects={t['id']:t for t in snapshot['objects']}
    operations=[dict(kind='remove_track',objectId=r['objectId'],expectedHash=objects[r['objectId']]['sha256']) for r in removals]
    return dict(kind='cleanup_tracks',policy=POLICY,nets=nets,operations=operations,removedCount=len(removals),
        removed=removals,skipped=skipped,sourceTrackCount=len(snapshot['tracks']),remainingTrackCount=len(snapshot['tracks'])-len(removals),
        scope='Only complete same-net/layer/width collinear containment in integer native nanometres. '
              'No partial trim, endpoint merge, branch deletion, width transition, arc, via or paired-net cleanup. '
              'Copper union is unchanged by each removal; saved native checks and source connectivity still required.')
