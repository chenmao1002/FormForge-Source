"""Continuous conservative translation proof against native enclosure/PCBA solids."""
import json,math
from pathlib import Path
from insertion_spec import InsertionInterface
from mechanical_pcb import exact_box,bounds,sha

NORMALS=dict(left=(-1,0,0),right=(1,0,0),front=(0,-1,0),back=(0,1,0),top=(0,0,1))
def box_shape(box):
    import cadquery as cq
    return cq.Workplane('XY').box(*box['size']).val().translate(box['center'])
def rotate(shape,angles):
    for axis,angle in zip([(1,0,0),(0,1,0),(0,0,1)],angles):
        if angle:shape=shape.rotate((0,0,0),axis,angle)
    return shape
def raw_shapes(geometry,root):
    import cadquery as cq
    if geometry['kind']=='box':return [box_shape(geometry)]
    p=root/geometry['step']
    if sha(p)!=geometry['step_sha256']:raise ValueError('Frozen mating STEP changed')
    values=cq.importers.importStep(str(p)).solids().vals()
    if not values or len(values)>2000 or any(not s.isValid() or s.Volume()<=0 for s in values):raise ValueError('Mating STEP needs 1..2000 valid positive solids; none may be silently omitted')
    return values
def overlaps(a,b,tolerance=1e-6):
    return all(a1>=b0-tolerance and b1>=a0-tolerance for a0,a1,b0,b1 in zip(a['min'],a['max'],b['min'],b['max']))

def evaluate(root,export=False):
    import cadquery as cq
    binding=json.loads((root/'binding.json').read_text(encoding='utf8'));plan=json.loads((root/'plan.json').read_text(encoding='utf8'));design=binding['design'];dep=binding['pcbDependency']
    from mechanical_inputs import validate_references
    if any(Path(i['evidencePath']).suffix!='.json' for i in plan['interfaces']):raise ValueError('Insertion evidence must be frozen JSON')
    validate_references(dict(**plan,evidence_files=[i['evidencePath'] for i in plan['interfaces']]),binding['inputFiles'])
    if any(sha(root/name)!=h for name,h in binding['inputFiles'].items()):raise ValueError('Frozen mating source changed')
    for name,h in binding['boundFiles'].items():
        path=(root/'bound'/name).resolve()
        if not path.is_relative_to((root/'bound').resolve()) or sha(path)!=h:raise ValueError('Frozen mechanical package content changed')
    static={name:cq.importers.importStep(str(root/'bound/outputs'/(name+'.step'))).val() for name in ['base','lid']}
    board=design['board'];source=root/'bound/outputs'/board['step'];solids=cq.importers.importStep(str(source)).solids().vals();offset=cq.Vector(-board['origin'][0],-board['origin'][1],design.get('floor',2.5)+design.get('standoff_height',5)-board['origin'][2]);solids=[s.translate(offset) for s in solids]
    if len(solids)!=sum(len(v) for v in dep['partSolidIndices'].values()):raise ValueError('Frozen PCBA solid count no longer matches traced parts')
    for part in dep['geometryParts']:static[part['id']]=cq.Compound.makeCompound([solids[i] for i in dep['partSolidIndices'][part['id']]])
    boxes={k:bounds(s) for k,s in static.items()};by_fp={p['footprintId']:p for p in dep['geometryParts'] if p['footprintId']};rows=[];moving={};vectors={};labels={};findings=[]
    obstacle_names={'base':'外壳底座','lid':'上盖',**{p['id']:p['reference'] or 'PCB基体' for p in dep['geometryParts']}}
    for item in plan['interfaces']:
        request=InsertionInterface.model_validate(item).model_dump(mode='json');id=request['id'];normal=NORMALS[request['face']];target=by_fp[request['footprintId']]['id'];zone=box_shape(request['matingZone']);zb=bounds(zone);tb=boxes[target]
        path=root/request['evidencePath'];evidence=json.loads(path.read_text(encoding='utf8'))
        if evidence.get('units')!='mm' or evidence.get('kind')!='insertion-interface' or evidence.get('interface')!={k:v for k,v in request.items() if k!='evidencePath'}:raise ValueError('Frozen insertion evidence must exactly match explicit interface parameters')
        axis=rotate(cq.Vertex.makeVertex(1,0,0),request['rotationDegrees']).Center()
        direction_ok=all(abs(a-b)<1e-6 for a,b in zip(axis.toTuple(),normal))
        zone_inside=all(a>=b-.002 and c<=d+.002 for a,b,c,d in zip(zb['min'],tb['min'],zb['max'],tb['max']))
        datum=request['datumCADmm'];datum_ok=all(lo-.002<=v<=hi+.002 for lo,v,hi in zip(tb['min'],datum,tb['max']))
        if not direction_ok or not zone_inside or not datum_ok:raise ValueError('Explicit mating direction/datum/zone inconsistent with native target bounds; never infer them from the bounds')
        origin=rotate(cq.Vertex.makeVertex(*request['datumLocalMm']),request['rotationDegrees']).Center();shift=cq.Vector(*datum)-origin;travel=[v*request['travel'] for v in normal];body_rows=[];reach=False;withdrawn=True
        target_remainder=static[target].cut(zone)
        axis_index=next(i for i,v in enumerate(normal) if v);sign=normal[axis_index]
        # Complete withdrawal must clear the actual case AND the protruding
        # connector, not merely a nominal wall parameter or one sampled pose.
        side='max' if sign>0 else 'min';ends=[boxes[n][side][axis_index] for n in ['base','lid',target]]
        face_plane=(max if sign>0 else min)(ends)
        for body in request['bodies']:
            key=id+'__'+body['id']
            if key in moving:raise ValueError('Moving body scene identities collide; use unambiguous interface/body IDs')
            parts=[rotate(s,request['rotationDegrees']).translate(shift) for s in raw_shapes(body['geometry'],root)];shape=cq.Compound.makeCompound(parts);moving[key]=shape;vectors[key]=travel;labels[key]=('工程包络 · ' if request['basis']=='engineering-envelope' else '声明配对件CAD · ')+key
            selected_findings=[];sweeps=[]
            if body['role']=='engaging':reach |= shape.intersect(zone).Volume()>1e-6
            for index,solid in enumerate(parts):
                b=bounds(solid);out_lo=[x+t for x,t in zip(b['min'],travel)];out_hi=[x+t for x,t in zip(b['max'],travel)]
                end_inside=(out_lo[axis_index]<face_plane+request['clearance']) if sign>0 else (out_hi[axis_index]>face_plane-request['clearance'])
                withdrawn &= not end_inside
                lo=[min(a,a+t)-request['clearance'] for a,t in zip(b['min'],travel)];hi=[max(a,a+t)+request['clearance'] for a,t in zip(b['max'],travel)];swept=box_shape(dict(center=[(a+c)/2 for a,c in zip(lo,hi)],size=[c-a for a,c in zip(lo,hi)]));swept_bounds=dict(min=lo,max=hi);sweeps.append(swept_bounds)
                for name,fixed in static.items():
                    if not overlaps(swept_bounds,boxes[name]):continue
                    allowed=name==target and body['role']=='engaging';obstacle=target_remainder if allowed else fixed
                    if not obstacle.Solids():continue
                    intersection=swept.intersect(obstacle).Volume();distance=swept.distance(obstacle)
                    if intersection>1e-6 or distance<1e-6:
                        finding=dict(interfaceId=id,bodyId=body['id'],solidIndex=index,obstacle=name,obstacleDisplayName=obstacle_names[name],allowedContactRegionUsed=allowed,intersectionMm3=intersection,minimumDistanceMm=distance)
                        selected_findings.append(finding);findings.append(finding)
            body_rows.append(dict(id=body['id'],role=body['role'],sourceKind=body['geometry']['kind'],solidCount=len(parts),seatedBoundsCADmm=bounds(shape),sweptBoxes=sweeps,collisionCount=len(selected_findings),passed=not selected_findings))
        passed=reach and withdrawn and all(b['passed'] for b in body_rows)
        rows.append(dict(id=id,footprintId=request['footprintId'],basis=request['basis'],direction=list(normal),travelMm=request['travel'],clearanceMm=request['clearance'],matingDatumDeclared=True,seatedBodyReachesDeclaredZone=bool(reach),withdrawalOutsideCaseAndConnector=bool(withdrawn),bodies=body_rows,passed=bool(passed)))
    result=dict(checksPassed=all(r['passed'] for r in rows),interfaces=rows,findings=findings,continuousPolicy='Per-solid exact-AABB swept over the full fixed-orientation straight translation; boxes expanded by stated L-infinity clearance. Conservative for STEP shapes, exact sweep envelope for axis-aligned boxes. Not sampled motion.',engineeringEnvelopeUsed=any(r['basis']=='engineering-envelope' for r in rows),compatibilityCertified=False,procurementToleranceVerified=False,manufacturingApproved=False,hardwareVerified=False,scope='Nominal continuous conservative clearance against full frozen enclosure and traced PCBA. Only engaging bodies may contact the selected connector inside the declared mating zone. Datum interpretation, mating forces, flexible cables, hand/tool access and real fit remain separate.')
    if export:
        from product_ide_3d.cad import Model
        from product_ide_3d.glb import compact_glb
        from mechanical_worker import view_evidence
        from types import SimpleNamespace
        scene={'base':static['base'],'lid':static['lid'],'pcb':cq.Compound.makeCompound(solids),**moving};assembly=cq.Assembly(name='insertion-diagnostic')
        for name,s in scene.items():assembly.add(s,name=name,color=cq.Color(*((.85,.55,.16) if name in moving else (.12,.5,.28) if name=='pcb' else (.22,.4,.6))))
        assembly.save(str(root/'assembly.step'));assembly.save(str(root/'assembly.glb'));compact_glb(root/'assembly.glb');view=view_evidence(SimpleNamespace(name='insertion-diagnostic'),Model(parts=scene),root,exact_box)
        for part in view['parts']:
            part['explodeVectorCADmm']=vectors.get(part['id'],[0,0,0]);part['displayName']=labels.get(part['id'],part['id'])
        view.update(explosionViewOnly=False,linearMotionView=dict(minimum=0,maximum=1,startLabel='就位',endLabel='拔出',viewOnly=True),scope='Explicit seated geometry plus declared outward travel; moving the viewer does not run or prove the clearance check.')
        (root/'assembly-view.json').write_text(json.dumps(view,ensure_ascii=False,indent=2),encoding='utf8');result['view']=view
        (root/'insertion-native.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    return result

def verify(root):
    recorded=json.loads((root/'insertion-native.json').read_text(encoding='utf8'));replayed=evaluate(root)
    if {k:v for k,v in recorded.items() if k!='view'}!=replayed:raise ValueError('Independent insertion geometry/clearance replay disagrees')
    if sha(root/'assembly.glb')!=recorded['view']['glbSha256'] or sha(root/'assembly.step')!=recorded['view']['stepSha256']:raise ValueError('Insertion diagnostic output changed')
    return dict(replayMatched=True,checksPassed=replayed['checksPassed'])
