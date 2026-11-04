"""A witnessed through-via star moved with every incident straight copper arm.

Only the via centre moves. Each arm retains its original width, copper layer,
remote endpoint and end tangents. A segment through the via becomes two arms.
Plane connectivity is left to the normal refill and final native checks.
"""
import math

if __package__:
    from .local_copper_rework import TOL, _on, _inside, interior_contact, proposal_quality as _quality
else:
    from local_copper_rework import TOL, _on, _inside, interior_contact, proposal_quality as _quality


POLICY = 'bounded-through-via-star-contact-rework-v1'


def prepare(snapshot, settings, policies, protected=()):
    request = settings.get('contactRework')
    if not request:
        return None
    objects = {o['id']: o for o in snapshot['objects']}
    tracks = {t['id']: t for t in snapshot['tracks']}
    refs = request['objects'];ids = [r['objectId'] for r in refs]
    if not ids or len(ids) != len(set(ids)) or set(ids) & set(protected):
        raise ValueError('Local contact requires unique, unprotected source objects.')
    selected = []
    for ref in refs:
        obj = objects.get(ref['objectId']);track = tracks.get(ref['objectId'])
        if obj is None or track is None or obj['sha256'] != ref['expectedHash']:
            raise ValueError('Local contact source identity or SHA changed.')
        selected.append(dict(track, sha256=obj['sha256']))
    vias = [t for t in selected if t['type'] == 'via']
    segments = [t for t in selected if t['type'] == 'segment']
    if len(vias) != 1 or not segments or len(segments)+1 != len(selected):
        raise ValueError('Local contact requires one via and all incident straight segments.')
    via = vias[0];at = via['at'];net = via['net']
    if any(t['net'] != net for t in segments):
        raise ValueError('Local contact source objects must share one explicit net.')
    policy = next((p for p in policies if p['net'] == net), None)
    if policy is None or policy['width']+TOL < max(t['width'] for t in segments):
        raise ValueError('Local contact routing policy cannot reduce source arm widths.')
    if any(t['layer'] not in policy.get('layers', ['F.Cu', 'B.Cu']) for t in segments):
        raise ValueError('Every original contact arm layer must be explicitly selected.')
    legs = []
    for segment in segments:
        a, b = segment['start'], segment['end']
        dx, dy = abs(a[0]-b[0]), abs(a[1]-b[1])
        if dx+dy < .004 or not (dx < TOL or dy < TOL or abs(dx-dy) < TOL) or not _on(at, a, b):
            raise ValueError('Local contact arms must be cardinal/45-degree segments through the via centre.')
        for anchor in (a, b):
            if math.dist(anchor, at) > TOL:
                legs.append(dict(sourceObjectId=segment['id'], anchor=list(anchor),
                                 layer=segment['layer'], width=segment['width']))
    if not _inside(at, settings['area']) or any(not _inside(leg['anchor'], settings['area']) for leg in legs):
        raise ValueError('Local contact via and all fixed remote anchors must lie inside the local area.')
    for i, first in enumerate(legs):
        for second in legs[i+1:]:
            if first['layer'] == second['layer'] and interior_contact(at, first['anchor'], at, second['anchor']):
                raise ValueError('Local contact does not support overlapping or branching source arms.')
    selected_ids = set(ids)
    for track in snapshot['tracks']:
        if track['id'] in selected_ids or track.get('net') != net:
            continue
        if track['type'] == 'segment' and _on(at, track['start'], track['end']):
            raise ValueError('Local contact source omits an incident straight segment.')
        if track['type'] == 'via' and math.dist(track['at'], at) <= TOL:
            raise ValueError('Local contact does not support coincident source vias.')
        for leg in legs:
            if track['type'] == 'segment' and track['layer'] == leg['layer'] and interior_contact(at, leg['anchor'], track['start'], track['end']):
                raise ValueError('Local contact source has an undeclared intermediate copper junction.')
            if track['type'] == 'via' and _on(track['at'], at, leg['anchor']) and math.dist(track['at'], leg['anchor']) > TOL:
                raise ValueError('Local contact source has an intermediate via anchor.')
    for fp in snapshot['footprints']:
        for pad in fp['pads']:
            if pad.get('net') != net:
                continue
            for leg in legs:
                if leg['layer'] in pad.get('copperLayers', [leg['layer']]) and _on(pad['at'], at, leg['anchor']) and math.dist(pad['at'], leg['anchor']) > TOL:
                    raise ValueError('Local contact source has a pad anchor at or inside a movable arm.')
    maximum = request.get('maxOffsetMm', .5)
    if not isinstance(maximum, (int, float)) or not math.isfinite(maximum) or not 0 < maximum <= 1:
        raise ValueError('Local contact offset must be positive and at most 1 mm.')
    refs_by_id = {ref['objectId']: ref for ref in refs}
    return dict(policy=POLICY, net=net, viaId=via['id'], sourceVia=via, segments=segments, legs=legs,
                sourceObjectIds=[via['id']]+[t['id'] for t in segments], anchors=[leg['anchor'] for leg in legs],
                maxOffsetMm=maximum, area=settings['area'], reason=request['reason'],
                removeCopper=[dict(objectId=t['id'], type='segment', net=net) for t in segments],
                removals=[dict(kind='remove_track', **refs_by_id[t['id']]) for t in segments])


def _arm(leg, source, target):
    anchor = leg['anchor'];distance = math.dist(anchor, source)
    unit = [(source[i]-anchor[i])/distance for i in (0, 1)];normal = [-unit[1], unit[0]]
    delta = [target[i]-anchor[i] for i in (0, 1)]
    parallel = sum(delta[i]*unit[i] for i in (0, 1));perpendicular = sum(delta[i]*normal[i] for i in (0, 1))
    if parallel <= abs(perpendicular)+.002:
        return None
    if abs(perpendicular) <= TOL:
        return [list(anchor), list(target)]
    # Leave the fixed remote junction along its original tangent, then lift
    # promptly into the corridor. This is a candidate shape, not a rule floor.
    straight = min(.05, (parallel-abs(perpendicular))/2)
    straight=max(straight,leg.get('minimumStraightMm',0))
    if straight+abs(perpendicular)>parallel+TOL:return None
    return [list(anchor), [round(anchor[i]+unit[i]*straight, 6) for i in (0, 1)],
            [round(anchor[i]+unit[i]*(straight+abs(perpendicular))+normal[i]*perpendicular, 6) for i in (0, 1)], list(target)]


def variants(spec):
    result = [];source = spec['sourceVia']['at'];size = spec['maxOffsetMm']
    for offset in ([size, 0], [-size, 0], [0, size], [0, -size]):
        target = [round(source[i]+offset[i], 6) for i in (0, 1)]
        if not _inside(target, spec['area']):
            continue
        operations = [dict(kind='move_via', objectId=spec['viaId'], expectedHash=spec['sourceVia']['sha256'], at=target, net=spec['net'])]
        for leg in spec['legs']:
            points = _arm(leg, source, target)
            if points is None or not all(_inside(point, spec['area']) for point in points):
                break
            operations.append(dict(kind='add_track', net=spec['net'], layer=leg['layer'], width=leg['width'], points=points))
        else:
            result.append(dict(offset=list(offset), operations=operations))
    return result


def proposal_quality(operations):
    return _quality([op for op in operations if op['kind'] == 'add_track'])


def _pad_geometry(pad, layer):
    from route_engine import xy
    return dict(at=xy(pad.GetPosition()), attribute=int(pad.GetAttribute()),
                copperShape=pad.GetEffectiveShape(layer).Format(False))


def _segment_geometry(track):
    import pcbnew as pcb
    from route_engine import xy
    return dict(width=pcb.ToMM(track.GetWidth()), start=xy(track.GetStart()), end=xy(track.GetEnd()))


def native_source_check(board, spec):
    import pcbnew as pcb
    from route_engine import uid, xy, point
    tracks = {uid(t): t for t in board.GetTracks()};via = tracks.get(spec['viaId'])
    if not isinstance(via, pcb.PCB_VIA) or via.GetViaType() != pcb.VIATYPE_THROUGH or via.TopLayer() != pcb.F_Cu or via.BottomLayer() != pcb.B_Cu:
        raise ValueError('Local contact requires the original through-via span.')
    source = spec['sourceVia'];at = source['at'];net = board.GetNetcodeFromNetname(spec['net'])
    if (xy(via.GetPosition()) != at or via.GetNetCode() != net or abs(pcb.ToMM(via.GetWidth(pcb.F_Cu))-source['width']) > TOL
            or abs(pcb.ToMM(via.GetDrillValue())-source['drill']) > TOL):
        raise ValueError('Local contact native via geometry changed.')
    selected = set(spec['sourceObjectIds']);items = list(board.GetTracks())+[p for f in board.GetFootprints() for p in f.Pads()]
    layers = list(board.GetEnabledLayers().CuStack())
    from routing_rules import native_exit_options
    for leg in spec['legs']:
        delta=[at[i]-leg['anchor'][i] for i in (0,1)];distance=math.hypot(*delta)
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                layer=board.GetLayerID(leg['layer'])
                if pad.GetNetCode()!=net or not pad.IsOnLayer(layer) or xy(pad.GetPosition())!=leg['anchor']:continue
                options=[r['length']for r in native_exit_options(pad,max(.15,leg['width']/2+.001),layer)
                    if all(abs(delta[i]/distance-r['axis'][i])<TOL for i in (0,1))]
                if not options:raise ValueError('Native contact arm has no centred straight pad escape')
                leg['minimumStraightMm']=max(leg.get('minimumStraightMm',0),min(options))
    retained = [];retained_pads=[]
    for item in items:
        if uid(item) in selected or item.GetNetCode() != net:
            continue
        if any(item.IsOnLayer(layer) and item.GetEffectiveShape(layer).Collide(via.GetEffectiveShape(layer), 0) for layer in layers):
            if isinstance(item,pcb.PAD):
                if math.dist(xy(item.GetPosition()),at)<=TOL:
                    raise ValueError('Local contact cannot move a via anchored at a pad centre.')
                retained_pads.extend(dict(id=uid(item),layer=board.GetLayerName(layer),geometry=_pad_geometry(item,layer))for layer in layers
                    if item.IsOnLayer(layer) and item.GetEffectiveShape(layer).Collide(via.GetEffectiveShape(layer),0))
                continue
            if not isinstance(item, pcb.PCB_TRACK) or isinstance(item, (pcb.PCB_ARC, pcb.PCB_VIA)):
                raise ValueError('Local contact has an additional attached pad, via or arc.')
            a, b = xy(item.GetStart()), xy(item.GetEnd())
            if _on(at, a, b):
                raise ValueError('Local contact source omits a centred incident segment.')
            retained.append(dict(id=uid(item), layer=board.GetLayerName(item.GetLayer()),
                                 width=pcb.ToMM(item.GetWidth()), start=a, end=b))
    spec['retainedContacts'] = sorted(retained, key=lambda row: row['id'])
    spec['retainedViaPads']=retained_pads
    arm_contacts = {}
    for segment in spec['segments']:
        item = tracks.get(segment['id']);layer = board.GetLayerID(segment['layer'])
        if (item is None or isinstance(item, (pcb.PCB_VIA, pcb.PCB_ARC)) or item.GetNetCode() != net
                or item.GetLayer() != layer or abs(pcb.ToMM(item.GetWidth())-segment['width']) > TOL
                or {tuple(xy(item.GetStart())), tuple(xy(item.GetEnd()))} != {tuple(segment['start']), tuple(segment['end'])}):
            raise ValueError('Local contact native incident segment changed.')
        anchors = [leg['anchor'] for leg in spec['legs'] if leg['sourceObjectId'] == segment['id']]
        for other in items:
            if uid(other) in selected or other.GetNetCode() != net or not other.IsOnLayer(layer):
                continue
            shape = other.GetEffectiveShape(layer)
            if not shape.Collide(item.GetEffectiveShape(layer), 0):
                continue
            if isinstance(other, pcb.PCB_ARC):
                raise ValueError('Local contact does not support an attached native arc.')
            if isinstance(other, pcb.PAD) and other.GetAttribute() == pcb.PAD_ATTRIB_SMD and not _on(xy(other.GetPosition()), segment['start'], segment['end']):
                # Preserve an existing off-centre finite overlap with a fixed
                # SMD pad. A pad centre on an arm remains an unsupported anchor.
                key = (uid(other), segment['layer'])
                record = arm_contacts.setdefault(key, dict(type='pad', id=uid(other), layer=segment['layer'],
                    geometry=_pad_geometry(other, layer), sourceObjectIds=[]))
                record['sourceObjectIds'].append(segment['id'])
                continue
            allowed = False
            for anchor in anchors:
                if not shape.Collide(pcb.SHAPE_CIRCLE(point(anchor), 0), 0):
                    continue
                if isinstance(other, (pcb.PAD, pcb.PCB_VIA)):
                    allowed = math.dist(xy(other.GetPosition()), anchor) <= TOL
                elif isinstance(other, pcb.PCB_TRACK):
                    a, b = xy(other.GetStart()), xy(other.GetEnd())
                    allowed = _on(anchor, a, b) and not any(interior_contact(at, end, a, b) for end in anchors)
                if allowed:
                    break
            if not allowed:
                if isinstance(other, pcb.PCB_TRACK) and not isinstance(other, (pcb.PCB_ARC, pcb.PCB_VIA)):
                    a, b = xy(other.GetStart()), xy(other.GetEnd())
                    if not any(interior_contact(at, end, a, b) for end in anchors):
                        key = (uid(other), segment['layer'])
                        record = arm_contacts.setdefault(key, dict(type='segment', id=uid(other), layer=segment['layer'],
                            geometry=_segment_geometry(other), sourceObjectIds=[]))
                        record['sourceObjectIds'].append(segment['id'])
                        continue
                raise ValueError('Local contact has an unsupported intermediate centreline junction: '+uid(other))
    spec['retainedArmContacts'] = [arm_contacts[key] for key in sorted(arm_contacts)]


def _angles_preserved(spec, operations):
    source = spec['sourceVia']['at'];target = operations[0]['at'];by_layer = {}
    for leg, operation in zip(spec['legs'], operations[1:]):
        original = [leg['anchor'][i]-source[i] for i in (0, 1)]
        current = [operation['points'][-2][i]-target[i] for i in (0, 1)]
        by_layer.setdefault(leg['layer'], []).append((original, current))
    def cosine(a, b):return sum(a[i]*b[i] for i in (0, 1))/math.hypot(*a)/math.hypot(*b)
    return all(len(rows) != 2 or abs(cosine(rows[0][0], rows[1][0])-cosine(rows[0][1], rows[1][1])) <= 1e-5 for rows in by_layer.values())


def screen_operations(board, params, spec, operations):
    """Segments are already removed; via is still at its original position.

    Temporarily detach only that via so its own hole is excluded from Geometry.
    It is restored unchanged in finally. The caller applies move_via afterwards
    and retains this board throughout the candidate round, as for copper detours.
    """
    import pcbnew as pcb
    from route_engine import Geometry, uid, xy, point
    from body_gap import BodyGapGuard
    if operations not in [row['operations'] for row in variants(spec)]:
        raise ValueError('Local contact operations differ from the bounded source contract.')
    via = next((t for t in board.GetTracks() if uid(t) == spec['viaId']), None)
    if via is None or xy(via.GetPosition()) != spec['sourceVia']['at']:
        raise ValueError('Local contact screen requires the original unmoved via.')
    if not _angles_preserved(spec, operations):
        return 'native_contact_two_arm_angle_changed'
    pads = {uid(p): p for fp in board.GetFootprints() for p in fp.Pads()}
    arm_objects = {**{uid(t): t for t in board.GetTracks()}, **pads}
    for contact in spec.get('retainedArmContacts', []):
        item = arm_objects.get(contact['id']);layer = board.GetLayerID(contact['layer'])
        if item is None or item.GetNetname() != spec['net'] or not item.IsOnLayer(layer):
            return 'native_contact_retained_arm_object_changed'
        if contact['type'] == 'pad':
            matches = isinstance(item, pcb.PAD) and _pad_geometry(item, layer) == contact['geometry']
        else:
            matches = (isinstance(item, pcb.PCB_TRACK) and not isinstance(item, (pcb.PCB_ARC, pcb.PCB_VIA))
                       and _segment_geometry(item) == contact['geometry'])
        if not matches:
            return 'native_contact_retained_arm_object_changed'
    refs = getattr(board, '_formforge_contact_refs', []);refs.append(via);board._formforge_contact_refs = refs
    original = xy(via.GetPosition())
    board.Remove(via)
    try:
        policy = next(p for p in params['nets'] if p['net'] == spec['net'])
        geometry = Geometry(board, params['outline'], params['rules'], body_guard=BodyGapGuard(board))
        net = board.GetNetcodeFromNetname(spec['net']);source = spec['sourceVia']
        if not geometry.via_clear(operations[0]['at'], net, source['width'], source['drill'], policy['clearance'],
                retained_pad_ids={p['id']for p in spec.get('retainedViaPads',[])}):
            return 'native_contact_via_clearance'
        # The source may touch fixed, same-net end caps without their
        # centrelines passing through the via. Keep those exact objects and
        # independently retain every witnessed finite-copper contact.
        via.SetPosition(point(operations[0]['at']))
        for contact in spec.get('retainedViaPads',[]):
            pad=pads.get(contact['id']);layer=board.GetLayerID(contact['layer'])
            if pad is None or pad.GetNetCode()!=net or not pad.IsOnLayer(layer) or _pad_geometry(pad,layer)!=contact['geometry']:
                return 'native_contact_retained_via_pad_changed'
            if not pad.GetEffectiveShape(layer).Collide(via.GetEffectiveShape(layer),0):
                return 'native_contact_retained_via_pad_disconnected'
        fixed = {uid(item): item for item in board.GetTracks()}
        for contact in spec.get('retainedContacts', []):
            item = fixed.get(contact['id']);layer = board.GetLayerID(contact['layer'])
            if (not isinstance(item, pcb.PCB_TRACK) or isinstance(item, (pcb.PCB_ARC, pcb.PCB_VIA))
                    or item.GetNetCode() != net or item.GetLayer() != layer
                    or abs(pcb.ToMM(item.GetWidth())-contact['width']) > TOL
                    or xy(item.GetStart()) != contact['start'] or xy(item.GetEnd()) != contact['end']):
                return 'native_contact_retained_copper_changed'
            if not item.GetEffectiveShape(layer).Collide(via.GetEffectiveShape(layer), 0):
                return 'native_contact_retained_copper_disconnected'
        neck = (params.get('routingStyle') or {}).get('padExitLengthMm', .15)
        for operation in operations[1:]:
            layer = geometry.names.index(operation['layer'])
            for a, b in zip(operation['points'], operation['points'][1:]):
                if not geometry.clear(a, b, layer, net, operation['width'], policy['clearance']):
                    return 'native_contact_arm_clearance_or_join'
                if not geometry.pad_exit_clear(a, b, layer, net, operation['width'], neck):
                    return 'native_contact_arm_pad_exit'
        for contact in spec.get('retainedArmContacts', []):
            item = arm_objects[contact['id']];layer = board.GetLayerID(contact['layer']);shape = item.GetEffectiveShape(layer)
            for source_id in contact['sourceObjectIds']:
                replacement = [operation for leg, operation in zip(spec['legs'], operations[1:])
                               if leg['sourceObjectId'] == source_id and operation['layer'] == contact['layer']]
                if not any(shape.Collide(pcb.SHAPE_SEGMENT(point(a), point(b), pcb.FromMM(operation['width'])), 0)
                           for operation in replacement for a, b in zip(operation['points'], operation['points'][1:])):
                    return 'native_contact_retained_arm_object_disconnected'
        return None
    finally:
        via.SetPosition(point(original))
        board.Add(via)
