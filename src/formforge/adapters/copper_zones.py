"""Native copper-zone editing and conservative, connected reference coverage."""
import math
import time

import pcbnew as pcb

LAYERS={'F.Cu':pcb.F_Cu,'In1.Cu':pcb.In1_Cu,'In2.Cu':pcb.In2_Cu,'B.Cu':pcb.B_Cu}


def mm(value):return round(pcb.ToMM(value),6)
def xy(value):return [mm(value.x),mm(value.y)]
def point(p):return pcb.VECTOR2I(pcb.FromMM(p[0]),pcb.FromMM(p[1]))
def identity(item):return item.m_Uuid.AsString()


def ring(chain):return [xy(chain.CPoint(i)) for i in range(chain.PointCount())]


def inventory(board):
    result=[]
    for zone in board.Zones():
        outline=zone.Outline()
        # Keepouts and legacy/unfilled zones can have no native fill cache.
        # Querying a missing layer in KiCad aborts the process, not a Python
        # exception. The native presence query is cheap and read-only.
        filled=zone.GetFilledPolysList(zone.GetLayer()) if zone.HasFilledPolysForLayer(zone.GetLayer()) else None
        result.append(dict(id=identity(zone),type='zone',net=zone.GetNetname(),layer=board.GetLayerName(zone.GetLayer()),
            outlines=[dict(outer=ring(outline.Outline(i)),holes=[ring(outline.Hole(i,h)) for h in range(outline.HoleCount(i))]) for i in range(outline.OutlineCount())],
            clearance=mm(zone.GetLocalClearance()),minimumThickness=mm(zone.GetMinThickness()),
            thermalGap=mm(zone.GetThermalReliefGap()),thermalSpoke=mm(zone.GetThermalReliefSpokeWidth()),priority=zone.GetAssignedPriority(),
            padConnection='thermal' if zone.GetPadConnection()==pcb.ZONE_CONNECTION_THERMAL else 'full' if zone.GetPadConnection()==pcb.ZONE_CONNECTION_FULL else 'native-'+str(int(zone.GetPadConnection())),
            islandRemoval='always' if zone.GetIslandRemovalMode()==pcb.ISLAND_REMOVAL_MODE_ALWAYS else 'native-'+str(int(zone.GetIslandRemovalMode())),
            ruleArea=zone.GetIsRuleArea(),copperLayers=[board.GetLayerName(l) for l in board.GetEnabledLayers().CuStack() if zone.IsOnLayer(l)],
            keepout=dict(tracks=zone.GetDoNotAllowTracks(),vias=zone.GetDoNotAllowVias(),pads=zone.GetDoNotAllowPads(),
                         footprints=zone.GetDoNotAllowFootprints(),zoneFills=zone.GetDoNotAllowZoneFills()) if zone.GetIsRuleArea() else None,
            filled=zone.IsFilled() and filled is not None,
            filledOutlineCount=filled.OutlineCount() if filled is not None else 0,filledAreaMm2=round(filled.Area()/1e12,6) if filled is not None else 0.))
    return result


def add(board,operation):
    zone=pcb.ZONE(board);zone.SetLayer(LAYERS[operation['layer']]);zone.SetNetCode(board.GetNetcodeFromNetname(operation['net']))
    zone.SetLocalClearance(pcb.FromMM(operation['clearance']));zone.SetMinThickness(pcb.FromMM(operation['minimumThickness']))
    zone.SetThermalReliefGap(pcb.FromMM(operation['thermalGap']));zone.SetThermalReliefSpokeWidth(pcb.FromMM(operation['thermalSpoke']))
    zone.SetPadConnection(pcb.ZONE_CONNECTION_THERMAL if operation['padConnection']=='thermal' else pcb.ZONE_CONNECTION_FULL)
    zone.SetAssignedPriority(operation['priority']);zone.SetIslandRemovalMode(pcb.ISLAND_REMOVAL_MODE_ALWAYS)
    x,y=operation['outline']['origin'];w,h=operation['outline']['width'],operation['outline']['height']
    polygon=zone.Outline();polygon.NewOutline()
    for p in ([x,y],[x+w,y],[x+w,y+h],[x,y+h]):polygon.Append(point(p))
    board.Add(zone);return zone


def refill(board):
    if len(list(board.Zones())) and not pcb.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Native copper-zone filling failed')
    board.BuildConnectivity()


def connected_fill(board,pads,layer,reference_net):
    """Only independently witnessed islands in these pads' native component."""
    connected={identity(item) for pad in pads for item in board.GetConnectivity().GetConnectedItems(pad)}|{identity(p) for p in pads}
    reference=pcb.SHAPE_POLY_SET();zones=[];unproven=[]
    witnesses=[p for f in board.GetFootprints() for p in f.Pads() if identity(p) in connected and p.IsOnLayer(layer) and p.GetNetname()==reference_net]
    witnesses.extend(t for t in board.GetTracks() if identity(t) in connected and t.IsOnLayer(layer) and t.GetNetname()==reference_net)
    shapes=[item.GetEffectiveShape(layer) for item in witnesses]
    for zone in board.Zones():
        if not zone.IsOnLayer(layer) or zone.GetNetname()!=reference_net or zone.GetIsRuleArea():continue
        filled=zone.GetFilledPolysList(layer) if zone.HasFilledPolysForLayer(layer) else None
        row=dict(id=identity(zone),filled=zone.IsFilled() and filled is not None,outlines=filled.OutlineCount() if filled is not None else 0,provenOutlines=0)
        zones.append(row)
        # A zone UUID can own separate islands. Each island needs an exact
        # intersection with a native item in the anchor's physical component.
        if not row['filled']:unproven.append(dict(id=row['id'],reason='not_filled'));continue
        for index in range(filled.OutlineCount()):
            island=pcb.SHAPE_POLY_SET();island.AddOutline(filled.Outline(index))
            for hole in range(filled.HoleCount(index)):island.AddHole(filled.Hole(index,hole),0)
            if any(island.Collide(shape,0) for shape in shapes):reference.BooleanAdd(island);row['provenOutlines']+=1
            else:unproven.append(dict(id=row['id'],outline=index,reason='no_anchor_component_witness'))
    return reference,zones,unproven


def reference_check(board,params):
    board.BuildConnectivity();reference_net=params['referenceNet']
    enabled=[board.GetLayerName(l) for l in board.GetEnabledLayers().CuStack()]
    mappings=params.get('layerReferences',[]);mapping={r['signalLayer']:r['referenceLayer'] for r in mappings}
    if len(enabled)>2 and not mapping:raise ValueError('Multilayer reference checks require explicit adjacent-layer mappings')
    if len(mapping)!=len(mappings):raise ValueError('Signal layer reference mappings cannot repeat')
    for signal,reference in mapping.items():
        if signal not in enabled or reference not in enabled or abs(enabled.index(signal)-enabled.index(reference))!=1:
            raise ValueError('Mapped reference must be an enabled adjacent copper layer')
    if not mapping and params['referenceLayer'] not in enabled:raise ValueError('Reference layer is disabled')
    if reference_net in params['nets']:raise ValueError('Reference net cannot also be selected as a signal net')
    pads=[p for f in board.GetFootprints() if f.GetReference()==params['anchor']['reference'] for p in f.Pads() if p.GetNumber()==params['anchor']['pin']]
    if not pads or any(p.GetNetname()!=reference_net for p in pads):raise ValueError('Reference anchor is absent or belongs to another net')
    error=pcb.FromMM(.001);cache={};zones=[];unproven=[]
    def reference_for(name):
        if name not in cache:
            region,found,missing=connected_fill(board,pads,LAYERS[name],reference_net)
            if region.OutlineCount():region.Inflate(-error,pcb.CORNER_STRATEGY_ROUND_ALL_CORNERS,error)
            zones.extend({**r,'layer':name} for r in found);unproven.extend({**r,'layer':name} for r in missing);cache[name]=region
        return cache[name]
    for name in sorted(set(mapping.values()),key=enabled.index) if mapping else [params['referenceLayer']]:reference_for(name)
    from reference_terminals import TerminalEvidence
    terminal_evidence=TerminalEvidence(board,pads,cache,LAYERS,reference_net,params.get('terminalAntipads'))
    rows=[];selected=set(params['nets']);observed=set();segments=set();missing_count=0;strict_missing=0;exceptions=0
    connected={identity(i) for p in pads for i in board.GetConnectivity().GetConnectedItems(p)}
    return_vias=[v for v in board.GetTracks() if isinstance(v,pcb.PCB_VIA) and v.GetNetname()==reference_net and identity(v) in connected]
    distance_limit=params.get('maxReturnViaDistance');required_refs=list(cache)
    projection_started=time.monotonic();projected_shapes={};projection_groups={}
    for track in board.GetTracks():
        if track.GetNetname() not in selected or isinstance(track,pcb.PCB_VIA):continue
        signal_layer=board.GetLayerName(track.GetLayer());reference_layer=mapping.get(signal_layer) if mapping else params['referenceLayer']
        if reference_layer is None or signal_layer==reference_layer:continue
        projected=pcb.SHAPE_POLY_SET()
        track.TransformShapeToPolygon(projected,track.GetLayer(),pcb.FromMM(params['margin']),error,pcb.ERROR_OUTSIDE)
        projected_shapes[identity(track)]=projected
        projection_groups.setdefault((signal_layer,reference_layer),pcb.SHAPE_POLY_SET()).Append(projected)
    projection_missing={}
    for key,projected in projection_groups.items():
        projected.Simplify();projected.BooleanSubtract(reference_for(key[1]));projection_missing[key]=projected
    exact_projection_fallbacks=0
    # Plane contact depends on the ground via and fixed reference regions,
    # not on which selected signal via is currently being checked.
    eligible_returns=None
    for track in board.GetTracks():
        if track.GetNetname() not in selected:continue
        observed.add(track.GetNetname())
        if isinstance(track,pcb.PCB_VIA):
            if eligible_returns is None:
                eligible_returns=[]
                if distance_limit is not None and mapping:
                    eligible_returns=[(xy(via.GetPosition()),identity(via))for via in return_vias
                        if all(via.IsOnLayer(LAYERS[name]) and reference_for(name).Collide(via.GetEffectiveShape(LAYERS[name]),0) for name in required_refs)]
            at=xy(track.GetPosition());choices=[(math.dist(at,position),id)for position,id in eligible_returns]
            nearest=min(choices) if choices else None;passed=nearest is not None and nearest[0]<=distance_limit+1e-6
            rows.append(dict(id=identity(track),net=track.GetNetname(),covered=passed,transitionGeometryPassed=passed,
                reason='anchor_connected_return_via_proximity' if passed else 'via_reference_transition_not_proven',
                at=xy(track.GetPosition()),diameter=mm(track.GetWidth(pcb.F_Cu)),
                returnViaId=nearest[1] if nearest else None,returnViaDistance=round(nearest[0],6) if nearest else None,maxReturnViaDistance=distance_limit,referenceLayers=required_refs))
            missing_count+=not passed;strict_missing+=not passed;continue
        segments.add(track.GetNetname())
        signal_layer=board.GetLayerName(track.GetLayer());reference_layer=mapping.get(signal_layer) if mapping else params['referenceLayer']
        if reference_layer is None:
            rows.append(dict(id=identity(track),net=track.GetNetname(),signalLayer=signal_layer,covered=False,reason='signal_reference_not_declared'));missing_count+=1;strict_missing+=1;continue
        if signal_layer==reference_layer:
            rows.append(dict(id=identity(track),net=track.GetNetname(),covered=False,reason='signal_on_reference_layer'));missing_count+=1;strict_missing+=1;continue
        source_projection=projected_shapes[identity(track)];batch_missing=projection_missing[signal_layer,reference_layer]
        projected=pcb.SHAPE_POLY_SET()
        if batch_missing.OutlineCount() and batch_missing.Collide(source_projection,0):
            # P ∩ ((union P) - R) is exactly P - R because P is a member of
            # the aggregate union.  Intersecting the small track polygon with
            # the already computed batch gap avoids subtracting the complex
            # whole-plane reference again for every affected fragment.
            exact_projection_fallbacks+=1;projected.Append(source_projection);projected.BooleanIntersection(batch_missing)
        raw_covered=projected.OutlineCount()==0;strict_missing+=not raw_covered;evidence=None
        if not raw_covered:projected,evidence=terminal_evidence.analyze(track,reference_layer,projected)
        covered=projected.OutlineCount()==0;exceptions+=covered and not raw_covered
        missing_count+=not covered
        rows.append(dict(id=identity(track),net=track.GetNetname(),signalLayer=signal_layer,referenceLayer=reference_layer,covered=covered,missingAreaMm2=round(projected.Area()/1e12,6),
            start=xy(track.GetStart()),end=xy(track.GetEnd()),width=mm(track.GetWidth()),rawCovered=raw_covered,terminalEvidence=evidence,
            missingBounds=None if covered else dict(origin=xy(projected.BBox().GetOrigin()),size=xy(projected.BBox().GetSize()))))
    absent=sorted(selected-observed);no_segments=sorted(selected-segments)
    passed=bool(rows) and not missing_count and not absent and not no_segments
    return dict(referenceCheckPassed=passed,referenceNet=reference_net,referenceLayer=params['referenceLayer'],anchor=params['anchor'],margin=params['margin'],
        strictReferenceCheckPassed=bool(rows) and not strict_missing and not absent and not no_segments,strictMissingTrackCount=strict_missing,
        terminalAntipads=params.get('terminalAntipads'),qualifiedTerminalTrackCount=exceptions,
        boardBounds=dict(origin=xy(board.GetBoardEdgesBoundingBox().GetOrigin()),size=xy(board.GetBoardEdgesBoundingBox().GetSize())),
        layerReferences=mappings,
        projectionBatch=dict(projectedTrackCount=len(projected_shapes),groupCount=len(projection_groups),exactFallbackTrackCount=exact_projection_fallbacks,
            elapsedSeconds=round(time.monotonic()-projection_started,6),scope='Exact aggregate difference skips per-track subtraction only when no aggregate missing polygon collides with that track projection.'),
        conservativePolygonErrorMm=.001,referenceZones=zones,unprovenZones=unproven,tracks=rows,missingTrackCount=missing_count,netsWithoutCopper=absent,netsWithoutSignalSegments=no_segments,
        maxReturnViaDistance=distance_limit,
        impedanceVerified=False,returnCurrentSimulated=False,manufacturingApproved=False,hardwareVerified=False,
        scope='Saved copper projection and anchor-connected islands. Optional bounded, exclusive native terminal holes require declared near-return conditions. Raw gaps remain reported. No impedance, return-current simulation or manufacturing approval.')
