"""Explicit, bounded detours of one uniform straight copper chain.

The original end caps and layer/width remain fixed. Interior contacts and branch
chains are deliberately unsupported; a detour never uses pad-only reconnection.
"""
import math


POLICY = 'bounded-uniform-straight-copper-detour-v1'
TOL = 1e-6


def _on(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    return (abs((p[0]-a[0])*dy-(p[1]-a[1])*dx) <= TOL*max(1., math.hypot(dx, dy))
            and min(a[0], b[0])-TOL <= p[0] <= max(a[0], b[0])+TOL
            and min(a[1], b[1])-TOL <= p[1] <= max(a[1], b[1])+TOL)


def _inside(p, area):
    return all(area['origin'][i]-TOL <= p[i] <= area['origin'][i]+area[('width', 'height')[i]]+TOL for i in (0, 1))


def interior_contact(a, b, c, d):
    """True if retained linear copper touches the chain away from its caps."""
    if any(_on(p, a, b) and math.dist(p, a) > TOL and math.dist(p, b) > TOL for p in (c, d)):
        return True
    u = [b[i]-a[i] for i in (0, 1)];v = [d[i]-c[i] for i in (0, 1)]
    cross = u[0]*v[1]-u[1]*v[0]
    if abs(cross) <= TOL:
        return _on(a, c, d) and _on(b, c, d)
    delta = [c[i]-a[i] for i in (0, 1)]
    t = (delta[0]*v[1]-delta[1]*v[0])/cross
    s = (delta[0]*u[1]-delta[1]*u[0])/cross
    return TOL < t < 1-TOL and -TOL <= s <= 1+TOL


def prepare(snapshot, settings, policies, protected=()):
    request = settings.get('copperRework')
    if not request:
        return None
    objects = {o['id']: o for o in snapshot['objects']}
    tracks = {t['id']: t for t in snapshot['tracks']}
    refs = request['objects'];ids = [ref['objectId'] for ref in refs]
    if not ids or len(ids) != len(set(ids)) or set(ids) & set(protected):
        raise ValueError('Local copper detour requires unique, unprotected straight copper objects.')
    selected = []
    for ref in refs:
        obj = objects.get(ref['objectId']);track = tracks.get(ref['objectId'])
        if obj is None or obj['sha256'] != ref['expectedHash']:
            raise ValueError('Local copper detour source SHA changed.')
        if track is None or track['type'] != 'segment':
            raise ValueError('Local copper detour supports straight segments only; vias remain fixed.')
        selected.append(track)
    net, layer, width = (selected[0][key] for key in ('net', 'layer', 'width'))
    if any((t['net'], t['layer'], t['width']) != (net, layer, width) for t in selected):
        raise ValueError('Local copper detour requires one uniform net, layer and width.')
    policy = next((p for p in policies if p['net'] == net), None)
    if policy is None or layer not in policy.get('layers', ['F.Cu', 'B.Cu']) or policy['width']+TOL < width:
        raise ValueError('Local copper detour must retain its selected layer and original width.')
    adjacency = {}
    for track in selected:
        for point in (track['start'], track['end']):
            adjacency.setdefault(tuple(point), []).append(track['id'])
    ends = sorted(point for point, edges in adjacency.items() if len(edges) == 1)
    if len(ends) != 2 or any(len(edges) > 2 for edges in adjacency.values()):
        raise ValueError('Local copper detour does not support branches or closed chains.')
    a, b = map(list, ends)
    if any(not _on(point, a, b) for point in adjacency):
        raise ValueError('Local copper detour supports collinear source chains only.')
    if abs(sum(math.dist(t['start'], t['end']) for t in selected)-math.dist(a, b)) > TOL:
        raise ValueError('Local copper detour source must be one connected, nonoverlapping chain.')
    dx, dy = abs(b[0]-a[0]), abs(b[1]-a[1])
    if dx+dy < .004 or not (dx < TOL or dy < TOL or abs(dx-dy) < TOL):
        raise ValueError('Local copper detour requires a cardinal or 45-degree source chain.')
    if not all(_inside(point, settings['area']) for point in adjacency):
        raise ValueError('Local copper detour source is outside the declared local area.')
    for track in snapshot['tracks']:
        if track['id'] in ids or track['net'] != net:
            continue
        if track['type'] == 'via':
            if _on(track['at'], a, b) and all(math.dist(track['at'], end) > TOL for end in (a, b)):
                raise ValueError('Local copper detour does not support an interior via anchor.')
        elif track['type'] == 'segment' and track['layer'] == layer and interior_contact(a, b, track['start'], track['end']):
            raise ValueError('Local copper detour does not support an interior copper junction.')
    for footprint in snapshot['footprints']:
        for pad in footprint['pads']:
            if pad.get('net') == net and layer in pad.get('copperLayers', [layer]) and _on(pad['at'], a, b) and all(math.dist(pad['at'], end) > TOL for end in (a, b)):
                raise ValueError('Local copper detour does not support an interior pad anchor.')
    offset = request.get('maxOffsetMm', .5)
    if not isinstance(offset, (int, float)) or not math.isfinite(offset) or not 0 < offset <= 1:
        raise ValueError('Local copper detour offset must be positive and at most 1 mm.')
    return dict(policy=POLICY, net=net, layer=layer, width=width, anchors=[a, b],
                maxOffsetMm=offset, area=settings['area'], reason=request['reason'], sourceObjectIds=ids,
                removeCopper=[dict(objectId=id, type='segment', net=net) for id in ids],
                removals=[dict(kind='remove_track', **ref) for ref in refs])


def detours(spec):
    a, b = spec['anchors'];distance = math.dist(a, b)
    unit = [(b[i]-a[i])/distance for i in (0, 1)];normal = [-unit[1], unit[0]]
    size = min(spec['maxOffsetMm'], distance/4)
    variants = []
    for sign in (-1, 1):
        offset = sign*size
        points = [a, [round(a[i]+unit[i]*size+normal[i]*offset, 6) for i in (0, 1)],
                  [round(b[i]-unit[i]*size+normal[i]*offset, 6) for i in (0, 1)], b]
        if not all(_inside(point, spec['area']) for point in points):
            continue
        variants.append(dict(offsetMm=round(offset, 6), operations=[dict(kind='add_track', net=spec['net'],
            layer=spec['layer'], width=spec['width'], points=points)]))
    return variants


def proposal_quality(operations):
    if __package__:
        from .route_readability_policy import metrics
    else:
        from route_readability_policy import metrics
    result = []
    for operation in operations:
        measure = metrics(operation['points'])
        result.append(dict(net=operation['net'], layer=operation['layer'], before=measure, after=dict(measure),
                           improved=False, maxAddedLengthMm=0, explicitLocalDetour=True))
    return result


def native_source_check(board, spec):
    """Reject off-axis/finite-copper contacts that a centreline-only read misses."""
    import pcbnew as pcb
    from route_engine import uid, xy, point
    selected = {uid(t): t for t in board.GetTracks() if uid(t) in spec['sourceObjectIds']}
    if set(selected) != set(spec['sourceObjectIds']):
        raise ValueError('Local copper detour native source identity changed.')
    layer = board.GetLayerID(spec['layer']);net = board.GetNetcodeFromNetname(spec['net'])
    anchors = spec['anchors'];dots = [pcb.SHAPE_CIRCLE(point(at), 0) for at in anchors]
    shapes = [item.GetEffectiveShape(layer) for item in selected.values()]
    items = list(board.GetTracks())+[p for fp in board.GetFootprints() for p in fp.Pads()]
    for item in items:
        if uid(item) in selected or item.GetNetCode() != net or not item.IsOnLayer(layer):
            continue
        shape = item.GetEffectiveShape(layer)
        if not any(shape.Collide(source, 0) for source in shapes):
            continue
        if isinstance(item, pcb.PCB_ARC):
            raise ValueError('Local copper detour does not support an attached native arc.')
        if not any(shape.Collide(dot, 0) for dot in dots):
            raise ValueError('Local copper detour has a finite copper contact away from its fixed caps.')
        if isinstance(item, (pcb.PAD, pcb.PCB_VIA)) and all(math.dist(xy(item.GetPosition()), at) > TOL for at in anchors):
            raise ValueError('Local copper detour has an off-centre pad/via contact.')
        if isinstance(item, pcb.PCB_TRACK) and not isinstance(item, pcb.PCB_VIA) and interior_contact(*anchors, xy(item.GetStart()), xy(item.GetEnd())):
            raise ValueError('Local copper detour has an interior native copper junction.')
    for zone in board.Zones():
        if zone.GetNetCode() == net and zone.IsOnLayer(layer) and zone.HasFilledPolysForLayer(layer):
            if any(zone.GetFilledPolysList(layer).Collide(shape, 0) for shape in shapes):
                raise ValueError('Local copper detour does not support source contacts to a filled plane.')


def screen_operations(board, params, spec, operations):
    """Native narrow screening after source removal/movement, before route search."""
    from route_engine import Geometry
    from body_gap import BodyGapGuard
    if operations not in [row['operations'] for row in detours(spec)]:
        raise ValueError('Local copper detour differs from its bounded source contract.')
    policy = next(p for p in params['nets'] if p['net'] == spec['net'])
    geometry = Geometry(board, params['outline'], params['rules'], body_guard=BodyGapGuard(board))
    layer = geometry.names.index(spec['layer']);net = board.GetNetcodeFromNetname(spec['net'])
    for operation in operations:
        for a, b in zip(operation['points'], operation['points'][1:]):
            if not geometry.clear(a, b, layer, net, spec['width'], policy['clearance']):
                return 'native_detour_clearance_or_join'
            if not geometry.pad_exit_clear(a, b, layer, net, spec['width'], (params.get('routingStyle') or {}).get('padExitLengthMm', .15)):
                return 'native_detour_pad_exit'
    return None
