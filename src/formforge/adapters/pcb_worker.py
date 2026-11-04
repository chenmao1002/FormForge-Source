"""Isolated KiCad 10 worker. Only this file imports the replaceable pcbnew API.

Invoked with KiCad's Python, never the web server's interpreter. Receives a
validated command file, does no network I/O and never chooses a circuit/layout.
"""
import json
import sys
import time
from pathlib import Path

import pcbnew as pcb
sys.path.insert(0,str(Path(__file__).resolve().parent))
from native_asserts import configure_native_asserts

configure_native_asserts('exception')


def publish_progress(path,data):
    """Advisory progress must not abort a native design operation on Windows."""
    temporary=path.with_suffix('.tmp')
    try:temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')
    except OSError:return False
    for attempt in range(5):
        try:temporary.replace(path);return True
        except PermissionError:time.sleep(.01*(attempt+1))
        except OSError:return False
    return False  # Next phase retries; the final result remains mandatory.


def mm(value): return round(pcb.ToMM(value), 6)
def point(value):
    # PCB coordinates are integral nanometres. FromMM truncates binary floats
    # and can turn an exact submitted 45-degree segment into a 1 nm skew.
    return pcb.VECTOR2I(*(round(v*1_000_000) for v in value))
def xy(value): return [mm(value.x), mm(value.y)]
def identity(item): return item.m_Uuid.AsString()


def library_position(fp,pad):
    x,y=xy(pad.GetFPRelativePosition())
    return [x,-y if fp.GetLayer()==pcb.B_Cu else y]


def outline(board, box):
    for item in list(board.GetDrawings()):
        if item.GetLayer() == pcb.Edge_Cuts: board.Delete(item)
    from board_outline import edges
    frame={**box,'cornerRadius':box.get('cornerRadius',min(3.,box['width']/4,box['height']/4))}
    for row in edges(frame):
        edge = pcb.PCB_SHAPE(board)
        edge.SetLayer(pcb.Edge_Cuts);edge.SetWidth(pcb.FromMM(.05))
        if row['kind']=='arc':
            edge.SetShape(pcb.SHAPE_T_ARC);edge.SetArcGeometry(point(row['start']),point(row['mid']),point(row['end']))
        else:
            edge.SetShape(pcb.SHAPE_T_SEGMENT);edge.SetStart(point(row['start']));edge.SetEnd(point(row['end']))
        board.Add(edge)


def inventory(board):
    footprints = [];silkscreen=[];silk_polygons=[]
    for fp in board.GetFootprints():
        for item in fp.GraphicalItems():
            if isinstance(item,pcb.PCB_SHAPE) and item.GetShape()==pcb.SHAPE_T_SEGMENT and item.GetLayer() in (pcb.F_SilkS,pcb.B_SilkS):
                silkscreen.append(dict(id=identity(item),type='silkscreen_line',footprintId=identity(fp),reference=fp.GetReference(),
                    start=xy(item.GetStart()),end=xy(item.GetEnd()),width=mm(item.GetWidth()),layer=board.GetLayerName(item.GetLayer())))
            elif isinstance(item,pcb.PCB_SHAPE) and item.GetShape()==pcb.SHAPE_T_POLY and item.GetLayer() in (pcb.F_SilkS,pcb.B_SilkS):
                box=item.GetBoundingBox()
                silk_polygons.append(dict(id=identity(item),type='silkscreen_polygon',footprintId=identity(fp),reference=fp.GetReference(),
                    width=mm(item.GetWidth()),layer=board.GetLayerName(item.GetLayer()),bounds=dict(origin=xy(box.GetOrigin()),size=xy(box.GetSize()))))
        box = fp.GetBoundingBox(False, False)
        footprints.append(dict(id=identity(fp), reference=fp.GetReference(), value=fp.GetValue(),
            footprint=fp.GetFPID().GetUniStringLibId(), at=xy(fp.GetPosition()), rotation=fp.GetOrientationDegrees(),
            layer=board.GetLayerName(fp.GetLayer()), path=fp.GetPath().AsString(),instanceAttributes=fp.GetAttributes()&(pcb.FP_DNP|pcb.FP_EXCLUDE_FROM_BOM|pcb.FP_EXCLUDE_FROM_POS_FILES|pcb.FP_BOARD_ONLY),locked=fp.IsLocked(),
            texts=[dict(field=f.GetName(),at=xy(f.GetPosition()),rotation=f.GetTextAngle().AsDegrees(),visible=f.IsVisible(),layer=board.GetLayerName(f.GetLayer())) for f in fp.GetFields() if f.GetName() in {'Reference','Value'}],
            bounds=dict(origin=xy(box.GetOrigin()), size=xy(box.GetSize())),
            pads=[dict(id=identity(p), number=p.GetNumber(), at=xy(p.GetPosition()), localAt=library_position(fp,p), size=xy(p.GetSize()),
                       net=p.GetNetname(), netCode=p.GetNetCode(),drill=xy(p.GetDrillSize()),platedThrough=p.GetAttribute()==pcb.PAD_ATTRIB_PTH,
                       copperLayers=[board.GetLayerName(l)for l in board.GetEnabledLayers().CuStack()if p.IsOnLayer(l)]) for p in fp.Pads()]))
    traces = []
    for item in board.GetTracks():
        via = isinstance(item, pcb.PCB_VIA)
        row = dict(id=identity(item), type='via' if via else 'arc' if isinstance(item,pcb.PCB_ARC) else 'segment', net=item.GetNetname(),
                   netCode=item.GetNetCode(), width=mm(item.GetWidth(pcb.F_Cu) if via else item.GetWidth()))
        if via: row.update(at=xy(item.GetPosition()), drill=mm(item.GetDrillValue()))
        else: row.update(start=xy(item.GetStart()), end=xy(item.GetEnd()), layer=board.GetLayerName(item.GetLayer()))
        traces.append(row)
    edges = [dict(id=identity(e), start=xy(e.GetStart()), end=xy(e.GetEnd()), shape=int(e.GetShape()),**(dict(mid=xy(e.GetArcMid())) if e.GetShape()==pcb.SHAPE_T_ARC else {}))
             for e in board.GetDrawings() if e.GetLayer()==pcb.Edge_Cuts]
    from route_engine import physical_groups
    from copper_zones import inventory as zone_inventory
    from board_stackup import read_native
    from body_gap import BodyGapGuard
    guard=BodyGapGuard(board);feedback=guard.report();conflicts=guard.audit(board)
    rows={r['footprintId']:r for r in feedback.pop('footprints')}
    for fp in footprints:
        fp['bodyGap']={**rows[fp['id']],'conflicts':[c for c in conflicts if c['footprintId']==fp['id']]}
    feedback.update(existingConflictCount=len(conflicts),enforced=False)
    from routing_rules import style_audit,trace_chains
    chains=trace_chains(footprints,traces)
    pads_by_net={}
    for fp in board.GetFootprints():
        for pad in fp.Pads():pads_by_net.setdefault(pad.GetNetname(),[]).append(pad)
    def pad_contains(net,layer,at):
        native_layer=board.GetLayerID(layer);test=pcb.SHAPE_CIRCLE(point(at),0)
        return any(p.IsOnLayer(native_layer)and p.GetEffectiveShape(native_layer).Collide(test,0)for p in pads_by_net.get(net,[]))
    feedback['routingQuality']=style_audit(footprints,traces,pad_contains,chains)
    from pad_exits import native_audit
    feedback['padExits']=native_audit(board)
    from placement_feedback import review as placement_review
    feedback['placementReview']=placement_review(footprints,traces,chains)
    from connector_access import review as connector_review
    feedback['connectorAccess']=connector_review(board)
    board_texts=[]
    for item in board.GetDrawings():
        if isinstance(item,pcb.PCB_TEXT) and item.GetLayer() in (pcb.F_SilkS,pcb.B_SilkS):
            box=item.GetBoundingBox()
            board_texts.append(dict(id=identity(item),type='board_text',text=item.GetText(),at=xy(item.GetPosition()),
                rotation=item.GetTextAngle().AsDegrees(),layer=board.GetLayerName(item.GetLayer()),size=xy(item.GetTextSize()),
                thickness=mm(item.GetTextThickness()),mirrored=item.IsMirrored(),bounds=dict(origin=xy(box.GetOrigin()),size=xy(box.GetSize()))))
    return dict(engineVersion=pcb.GetBuildVersion(), units='mm', thickness=mm(board.GetDesignSettings().GetBoardThickness()),
                layoutFeedback=feedback,
                physicalStackup=read_native(board.GetFileName()),copperLayers=[board.GetLayerName(l) for l in board.GetEnabledLayers().CuStack()],physicalGroups=physical_groups(board),zones=zone_inventory(board),
                footprints=sorted(footprints,key=lambda f:f['reference']),silkscreenLines=sorted(silkscreen,key=lambda g:g['id']),silkscreenPolygons=sorted(silk_polygons,key=lambda g:g['id']),boardTexts=sorted(board_texts,key=lambda t:t['id']), tracks=traces, outline=edges,
                nets=[dict(code=int(code), name=net.GetNetname()) for code,net in board.GetNetsByNetcode().items()])


def planning_inventory(board):
    """Placement and pin facts only; do not compute routing/clearance audits."""
    footprints=[]
    for fp in board.GetFootprints():
        box=fp.GetBoundingBox(False,False)
        footprints.append(dict(id=identity(fp),reference=fp.GetReference(),at=xy(fp.GetPosition()),
            rotation=fp.GetOrientationDegrees(),layer=board.GetLayerName(fp.GetLayer()),locked=fp.IsLocked(),
            bounds=dict(origin=xy(box.GetOrigin()),size=xy(box.GetSize())),
            pads=[dict(id=identity(p),number=p.GetNumber(),net=p.GetNetname(),at=xy(p.GetPosition()))for p in fp.Pads()]))
    return dict(footprints=sorted(footprints,key=lambda f:f['reference']),tracks=[],layoutFeedback={},
                copperLayers=[board.GetLayerName(l)for l in board.GetEnabledLayers().CuStack()])


def create(params):
    board = pcb.BOARD()
    board.SetCopperLayerCount(2)
    board.GetDesignSettings().SetBoardThickness(pcb.FromMM(params['thickness']))
    board.SetFileName(params['board'])
    netlist = params['netlist']
    nets = {}
    for group in netlist['groups']:
        net = pcb.NETINFO_ITEM(board, group['name'], len(nets)+1)
        board.Add(net);nets[group['id']] = net
    membership = {(p['reference'],p['pin']): nets[g['id']] for g in netlist['groups'] for p in g['pins']}
    placements = {r['reference']:r for r in params['placements']}
    for comp in params['components']:
        library, name = comp['footprint'].split(':')
        path = Path(params['footprintRoot']) / (library+'.pretty')
        fp = pcb.FootprintLoad(str(path), name)
        if fp is None: raise ValueError('Cannot load frozen footprint '+comp['footprint'])
        fp.SetFPID(pcb.LIB_ID(library,name));fp.SetReference(comp['reference']);fp.SetValue(comp['value'])
        fields={**fp.GetFieldsText(),**comp.get('fields',{}),'Reference':comp['reference'],'Value':comp['value']}
        fields.pop('Footprint',None)
        fp.SetFields(fields)
        for field in fp.GetFields():
            if field.GetName() not in {'Reference','Value'}:field.SetVisible(False)
        fp.SetPath(pcb.KIID_PATH(comp['path']));fp.SetSheetname(comp['sheetname']);fp.SetSheetfile(comp['sheetfile'])
        board.Add(fp)
        placement = placements[comp['reference']]
        fp.SetPosition(point(placement['at']));fp.SetOrientationDegrees(placement['rotation'])
        for pad in fp.Pads():
            if pad.GetNumber():pad.SetNet(membership[(comp['reference'],pad.GetNumber())])
    outline(board, params['outline'])
    if not pcb.SaveBoard(params['board'], board): raise ValueError('Cannot save board')
    return pcb.LoadBoard(params['board'])


def edit(params):
    board = pcb.LoadBoard(params['board'])
    footprints=list(board.GetFootprints());objects = {identity(f):f for f in footprints}
    objects.update({identity(t):t for t in board.GetTracks()})
    objects.update({identity(z):z for z in board.Zones()})
    objects.update({identity(t):t for t in board.GetDrawings() if isinstance(t,pcb.PCB_TEXT) and t.GetLayer() in (pcb.F_SilkS,pcb.B_SilkS)})
    parents={identity(g):f for f in footprints for g in f.GraphicalItems()}
    objects.update({identity(g):g for f in footprints for g in f.GraphicalItems()})
    layers = {'F.Cu':pcb.F_Cu,'In1.Cu':pcb.In1_Cu,'In2.Cu':pcb.In2_Cu,'B.Cu':pcb.B_Cu}
    created=[];mechanical=[]
    for op_index,operation in enumerate(params['operations']):
        kind = operation['kind']
        if kind in {'set_board_rules','release_pair','set_zone_rules','set_via_rules'}:continue  # Metadata is validated and versioned by the service.
        elif kind=='add_board_text':
            text=pcb.PCB_TEXT(board);text.SetText(operation['text']);text.SetPosition(point(operation['at']))
            text.SetLayer(pcb.F_SilkS if operation['layer']=='F.Silkscreen'else pcb.B_SilkS)
            text.SetTextSize(point([operation['size'],operation['size']]));text.SetTextThickness(pcb.FromMM(operation['thickness']))
            text.SetTextAngle(pcb.EDA_ANGLE(operation['rotation'],pcb.DEGREES_T));text.SetMirrored(operation['layer']=='B.Silkscreen')
            text.SetHorizJustify(pcb.GR_TEXT_H_ALIGN_CENTER);text.SetVertJustify(pcb.GR_TEXT_V_ALIGN_CENTER);board.Add(text)
        elif kind=='remove_board_text':board.Delete(objects[operation['objectId']])
        elif kind=='add_mounting_hole':
            lib,name=operation['frozenFootprint'].split(':')
            fp=pcb.FootprintLoad(str(Path(operation['footprintRoot'])/(lib+'.pretty')),name)
            if fp is None:raise ValueError('Cannot load frozen mechanical footprint')
            fp.SetFPID(pcb.LIB_ID(lib,name));fp.SetReference(operation['reference'])
            fp.SetAttributes(fp.GetAttributes()|pcb.FP_BOARD_ONLY|pcb.FP_EXCLUDE_FROM_BOM|pcb.FP_EXCLUDE_FROM_POS_FILES)
            fp.Reference().SetVisible(False);fp.Value().SetVisible(False);fp.SetPosition(point(operation['at']));board.Add(fp)
            mechanical.append(dict(id=identity(fp),padId=identity(next(iter(fp.Pads()))),operation=op_index))
        elif kind=='remove_mounting_hole':board.Delete(objects[operation['objectId']])
        elif kind=='set_stackup':
            from board_stackup import validate
            profile=operation['profile'];names=validate(profile)
            enabled={layers[n] for n in names}
            for item in list(board.GetTracks())+list(board.Zones()):
                if not isinstance(item,pcb.PCB_VIA) and item.GetLayer() not in enabled:raise ValueError('Cannot disable a layer containing copper')
            board.SetCopperLayerCount(len(names));board.GetDesignSettings().SetBoardThickness(pcb.FromMM(profile['nominalThickness']))
        elif kind=='set_footprint_text':
            field=next(f for f in objects[operation['objectId']].GetFields() if f.GetName()==operation['field'])
            field.SetPosition(point(operation['at']));field.SetTextAngle(pcb.EDA_ANGLE(operation['rotation'],pcb.DEGREES_T));field.SetVisible(operation['visible'])
        elif kind in {'set_footprint_silkscreen_line','remove_footprint_silkscreen_line'}:
            line=objects[operation['objectId']]
            if not isinstance(line,pcb.PCB_SHAPE) or line.GetShape()!=pcb.SHAPE_T_SEGMENT or line.GetLayer() not in (pcb.F_SilkS,pcb.B_SilkS):
                raise ValueError('Only existing footprint silkscreen straight lines are editable')
            if kind=='remove_footprint_silkscreen_line':parents[operation['objectId']].Delete(line)
            else:line.SetStart(point(operation['start']));line.SetEnd(point(operation['end']))
        elif kind=='remove_footprint_silkscreen_polygon':
            polygon=objects[operation['objectId']]
            if not isinstance(polygon,pcb.PCB_SHAPE) or polygon.GetShape()!=pcb.SHAPE_T_POLY or polygon.GetLayer() not in (pcb.F_SilkS,pcb.B_SilkS):
                raise ValueError('Only existing footprint silkscreen polygons can be removed')
            parents[operation['objectId']].Delete(polygon)
        elif kind == 'move_footprint':
            fp = objects[operation['objectId']]
            side = operation.get('layer')
            if side is not None and fp.GetLayer() != layers[side]:
                fp.Flip(fp.GetPosition(),pcb.FLIP_DIRECTION_TOP_BOTTOM)
            fp.SetPosition(point(operation['at']));fp.SetOrientationDegrees(operation['rotation'])
        elif kind == 'move_via':
            via=objects[operation['objectId']]
            if not isinstance(via,pcb.PCB_VIA) or via.GetNetname()!=operation['net']:raise ValueError('Local contact via identity changed')
            via.SetPosition(point(operation['at']))
        elif kind == 'set_outline': outline(board,operation['outline'])
        elif kind in {'remove_track','remove_via','remove_zone'}:board.Delete(objects[operation['objectId']])
        elif kind=='add_zone':
            from copper_zones import add
            if not board.IsLayerEnabled(layers[operation['layer']]):raise ValueError('Copper layer is disabled')
            zone=add(board,operation);created.append(dict(id=identity(zone),operation=op_index,segment=0))
        else:
            code=board.GetNetcodeFromNetname(operation['net'])
            if not code:raise ValueError('Unknown or empty copper net')
            if kind == 'add_track':
                if not board.IsLayerEnabled(layers[operation['layer']]):raise ValueError('Copper layer is disabled')
                for segment_index,(a,b) in enumerate(zip(operation['points'],operation['points'][1:])):
                    track=pcb.PCB_TRACK(board);track.SetStart(point(a));track.SetEnd(point(b))
                    track.SetWidth(pcb.FromMM(operation['width']));track.SetLayer(layers[operation['layer']]);track.SetNetCode(code);board.Add(track)
                    created.append(dict(id=identity(track),operation=op_index,segment=segment_index))
            elif kind == 'add_via':
                via=pcb.PCB_VIA(board);via.SetViaType(pcb.VIATYPE_THROUGH);via.SetLayerPair(pcb.F_Cu,pcb.B_Cu)
                via.SetPosition(point(operation['at']));via.SetWidth(pcb.FromMM(operation['diameter']))
                via.SetDrill(pcb.FromMM(operation['drill']));via.SetNetCode(code);board.Add(via)
                created.append(dict(id=identity(via),operation=op_index,segment=0))
            else:raise ValueError('Unsupported PCB operation')
    if all(op['kind']=='set_zone_rules' for op in params['operations']):return board,created,mechanical
    from copper_zones import refill
    if not all(op['kind'] in {'set_footprint_silkscreen_line','remove_footprint_silkscreen_line','remove_footprint_silkscreen_polygon'} for op in params['operations']):refill(board)
    if not pcb.SaveBoard(params['board'],board):raise ValueError('Cannot save board')
    for operation in params['operations']:
        if operation['kind']=='set_stackup':
            from board_stackup import write_native
            write_native(params['board'],operation['profile'])
    return pcb.LoadBoard(params['board']),created,mechanical


def sync(params):
    board=pcb.LoadBoard(params['board']);footprints={f.GetReference():f for f in board.GetFootprints()}
    membership={}
    for group in params['netlist']['groups']:
        name=group['name'];net=board.FindNet(name)
        if not net or not net.GetNetCode():
            net=pcb.NETINFO_ITEM(board,name,max((int(code) for code in board.GetNetsByNetcode()),default=0)+1);board.Add(net)
        for pin in group['pins']:membership[(pin['reference'],pin['pin'])]=net
    for comp in params['components']:
        fp=footprints.get(comp['reference'])
        replacement=next((r for r in params.get('replacements',[]) if r['reference']==comp['reference']),None)
        if replacement:
            from pcb_footprint_replace import replace
            fp=replace(board,fp,replacement,params['footprintRoot'])
        if fp is None:
            lib,name=comp['footprint'].split(':');fp=pcb.FootprintLoad(str(Path(params['footprintRoot'])/(lib+'.pretty')),name)
            if fp is None:raise ValueError('Cannot load newly frozen footprint')
            fp.SetFPID(pcb.LIB_ID(lib,name));fp.SetReference(comp['reference']);fp.SetValue(comp['value']);board.Add(fp)
            placement=next(p for p in params['placements'] if p['reference']==comp['reference'])
            fp.SetPosition(point(placement['at']));fp.SetOrientationDegrees(placement['rotation'])
        fields={**fp.GetFieldsText(),**comp.get('fields',{}),'Reference':comp['reference'],'Value':comp['value']};fields.pop('Footprint',None)
        fp.SetFields(fields)
        for field in fp.GetFields():
            if field.GetName() not in {'Reference','Value'}:field.SetVisible(False)
        fp.SetPath(pcb.KIID_PATH(comp['path']));fp.SetSheetname(comp['sheetname']);fp.SetSheetfile(comp['sheetfile'])
        for pad in fp.Pads():
            if pad.GetNumber():pad.SetNet(membership[(comp['reference'],pad.GetNumber())])
    from copper_zones import refill
    refill(board)
    if not pcb.SaveBoard(params['board'],board):raise ValueError('Cannot save synchronized board')
    # Keep the retired proxies on this board through the final snapshot.
    # Readback is performed in a fresh native process by the service.
    return board


def main():
    params=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    if not pcb.GetBuildVersion().startswith('10.'):raise ValueError('KiCad 10 required')
    action=params['action']
    if action=='planning_read':
        result=planning_inventory(pcb.LoadBoard(params['board']))
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');return
    if action=='planning_probe':
        from pcb_planning_native import probe
        result=probe(pcb.LoadBoard(params['board']),params)
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');return
    if action=='normalize_fixture_copy':
        # Internal regression utility: the caller owns a private fixture copy.
        # Refresh both comparison sides with the same native filler. Legacy
        # cached fills must not be compared with newly regenerated candidates.
        # Source tracks/pads/placement/outline and connectivity must stay put.
        board=pcb.LoadBoard(params['board']);before=inventory(board)
        from copper_zones import refill
        refill(board)
        if not pcb.SaveBoard(params['board'],board):raise ValueError('Cannot normalize private native fixture')
        result=inventory(pcb.LoadBoard(params['board']))
        fields=('physicalGroups','footprints','tracks','outline')
        def semantic(value):
            if isinstance(value,dict):
                return {k:({'F.SilkS':'F.Silkscreen','B.SilkS':'B.Silkscreen'}.get(v,v) if k=='layer' else semantic(v))
                        for k,v in value.items() if k not in {'netCode','bodyGap'}}
            if isinstance(value,list):
                rows=[semantic(v) for v in value]
                return sorted(rows,key=lambda row:json.dumps(row,sort_keys=True)) if rows and all(isinstance(v,dict) for v in rows) else rows
            return value
        canonical=lambda rows:sorted(json.dumps(semantic(row),sort_keys=True) for row in rows)
        checks={k:canonical(before[k])==canonical(result[k]) for k in fields};preserved=all(checks.values())
        result['normalizationChecks']=checks
        result['normalizationPreserved']=preserved
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        if not preserved:raise ValueError('Native fixture normalization changed identities or geometry')
        return
    if action=='export_footprint_variants':
        board=pcb.LoadBoard(params['board']);footprints={identity(f):f for f in board.GetFootprints()};files=[]
        for row in params['variants']:
            fp=pcb.FOOTPRINT(footprints[row['footprintId']])
            name=fp.GetFPID().GetUniStringLibId().split(':',1)[1]
            if fp.GetLayer()==pcb.B_Cu:fp.Flip(fp.GetPosition(),False)
            fp.SetOrientationDegrees(0);fp.SetPosition(point([0,0]));fp.SetReference('REF**');fp.SetValue(name)
            pcb.FootprintSave(row['libraryDirectory'],fp)
            path=Path(row['libraryDirectory'])/(name+'.kicad_mod')
            if not path.is_file() or pcb.FootprintLoad(row['libraryDirectory'],name) is None:raise ValueError('Derived footprint native readback failed')
            files.append(str(path))
        Path(sys.argv[2]).write_text(json.dumps(dict(files=files)),encoding='utf8');return
    if action=='reference_check':
        from copper_zones import reference_check
        result=reference_check(pcb.LoadBoard(params['board']),params)
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');return
    if action=='local_candidates':
        from local_placement import candidates
        result=candidates(pcb.LoadBoard(params['board']),params)
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');return
    if action in {'existing_tracks_plan','interface_silkscreen_plan'}:
        if action=='existing_tracks_plan':from existing_track_optimization import plan
        else:from interface_silkscreen_placement import plan
        result=plan(pcb.LoadBoard(params['board']),params)
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');return
    if action=='silkscreen_plan':
        from silkscreen_placement import plan
        result=plan(pcb.LoadBoard(params['board']),params)
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');return
    if action in {'route_plan','pair_plan'}:
        if action=='pair_plan':from pair_routes import plan
        else:from route_engine import plan
        progress_file=Path(sys.argv[2]).with_name('progress.json');event_index=0
        def progress(data):
            nonlocal event_index
            event_index+=1;publish_progress(progress_file,dict(eventIndex=event_index,**data))
        result=plan(pcb.LoadBoard(params['board']),params,progress)
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');return
    created=[];mechanical=[]
    if action=='edit':board,created,mechanical=edit(params)
    elif action=='sync':board=sync(params)
    else:board=create(params) if action=='initialize' else pcb.LoadBoard(params['board'])
    result=inventory(board)
    result['createdObjects']=created
    result['createdMechanicalFeatures']=mechanical
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
