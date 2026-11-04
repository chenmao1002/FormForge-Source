"""Preserve native source pad components during ordinary manual PCB edits.

This consumes saved-and-reloaded native copper connectivity, never net labels
or zone UUIDs as a substitute for physical connection.
"""

POLICY = 'preserve-native-source-pad-components-v1'


def proof(before, after):
    old, new = before.get('physicalGroups'), after.get('physicalGroups')
    if not isinstance(old, list) or not isinstance(new, list):
        return dict(policy=POLICY, passed=False, reason='native_physical_groups_missing',
                    brokenSourceGroups=[], padSetMatched=False)
    old_ids = {p for group in old for p in group['pads']}
    new_ids = {p for group in new for p in group['pads']}
    groups = [(g['net'], set(g['pads'])) for g in new]
    pads = {p['id']: dict(objectId=p['id'], footprintId=f['id'], reference=f['reference'],
                        pin=p['number'], at=p['at'])
            for f in before.get('footprints', []) for p in f['pads'] if p.get('id')}
    broken = []
    for group in old:
        members = set(group['pads'])
        if any(net == group['net'] and members <= observed for net, observed in groups):
            continue
        broken.append(dict(net=group['net'], sourcePadIds=sorted(members),
            sourcePads=[pads.get(p, dict(objectId=p)) for p in sorted(members)],
            observedComponents=[dict(net=net, pads=sorted(observed)) for net, observed in groups
                                if members & observed], missingPadIds=sorted(members-new_ids)))
    affected = {row['net'] for row in broken}
    current = {t['id']: t for t in after.get('tracks', [])}
    # Diagnostic only: the native pad partition, not object counts, decides.
    changed = [t for t in before.get('tracks', []) if t['net'] in affected
               and (t['id'] not in current or any(t.get(k) != current[t['id']].get(k)
                   for k in ('type', 'net', 'layer', 'start', 'end', 'at', 'width', 'drill')))]
    return dict(policy=POLICY, passed=not broken and old_ids == new_ids,
        padSetMatched=old_ids == new_ids, missingPadIds=sorted(old_ids-new_ids),
        addedPadIds=sorted(new_ids-old_ids), sourceGroupCount=len(old), actualGroupCount=len(new),
        brokenSourceGroupCount=len(broken), brokenSourceGroups=broken,
        changedSourceCopper=[{k:t[k] for k in ('id','type','net','layer','start','end','at','width','drill') if k in t}
                             for t in changed],
        scope='Every existing native pad component must remain within one same-net component. '
              'Merging components and removing redundant copper are allowed. '
              'This is not an SI/PI, full-copper-shape, or manufacturing proof.')
