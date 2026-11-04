"""Native proof of KiCad STEP substrate, drill voids and selected components."""
import hashlib,json
from pathlib import Path

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def exact_box(shape):
    import cadquery as cq
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box=Bnd_Box();BRepBndLib.AddOptimal_s(shape.wrapped,box,False,False)
    return cq.BoundBox(box)

def bounds(shape):
    b=exact_box(shape);return dict(min=[b.xmin,b.ymin,b.zmin],max=[b.xmax,b.ymax,b.zmax])


def colored_step(path, expected):
    """Keep STEP assembly/face colors while independently retaining solid proof."""
    import cadquery as cq
    renamed=[]
    try:assembly=cq.Assembly.load(str(path),importType='STEP')
    except ValueError as error:
        duplicate=str(error).startswith('Unique name is required.')
        if str(error)!='Step file does not contain an assembly' and not duplicate:raise
        # The pinned importer requires an assembly root. Read standalone free
        # shapes and face colors directly, without modifying the XCAF graph.
        from cadquery.occ_impl.importers.assembly import _get_ref_color,_get_shape_color
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.XCAFDoc import XCAFDoc_DocumentTool
        from OCP.TDF import TDF_LabelSequence
        reader=STEPCAFControl_Reader();reader.SetColorMode(True);reader.SetNameMode(True)
        if reader.ReadFile(str(path))!=IFSelect_RetDone:raise ValueError('Cannot read colored standalone STEP')
        doc=TDocStd_Document(TCollection_ExtendedString('XmXCAF'))
        if not reader.Transfer(doc):raise ValueError('Cannot transfer colored standalone STEP')
        tool=XCAFDoc_DocumentTool.ShapeTool_s(doc.Main());labels=TDF_LabelSequence();tool.GetFreeShapes(labels)
        if not labels.Length():raise ValueError('Standalone STEP has no native shapes')
        if duplicate:
            # STEP component names need not be unique. CadQuery's importer
            # requires unique sibling names; disambiguate the in-memory labels
            # while retaining the source file, locations, colors and geometry.
            from cadquery.occ_impl.importers.assembly import _get_name,_importDoc
            from OCP.TDataStd import TDataStd_Name
            from OCP.TDF import TDF_Label,TDF_Tool
            from OCP.TCollection import TCollection_AsciiString
            visited=set()
            def rename_components(label):
                label_entry=TCollection_AsciiString();TDF_Tool.Entry_s(label,label_entry);entry=label_entry.ToCString()
                if entry in visited:return
                visited.add(entry)
                components=TDF_LabelSequence();tool.GetComponents_s(label,components)
                seen=set()
                for index,component in enumerate(components):
                    original=_get_name(component);name=original
                    if name in seen:
                        suffix='__ff_'+str(index)
                        name=original[:-5]+suffix+'_part' if original.endswith('_part') else original+suffix
                        while name in seen:name+='_'
                        TDataStd_Name.Set_s(component,TCollection_ExtendedString(name))
                        renamed.append(dict(originalName=original,importName=name))
                    seen.add(name)
                    if tool.IsReference_s(component):
                        reference=TDF_Label();tool.GetReferredShape_s(component,reference)
                        if tool.IsAssembly_s(reference):rename_components(reference)
            for label in labels:rename_components(label)
            assembly=cq.Assembly();_importDoc(doc,assembly)
        else:
            assembly=None
        if assembly is not None:labels=[]
        else:assembly=cq.Assembly(name='standalone-step')
        colors=XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
        for i,label in enumerate(labels):
            shape=cq.Shape.cast(tool.GetShape_s(label));part=cq.Assembly(shape,name='shape_'+str(i),color=_get_ref_color(label)or _get_shape_color(shape.wrapped,colors))
            sublabels=TDF_LabelSequence();tool.GetSubShapes_s(label,sublabels)
            for sublabel in sublabels:
                subshape=cq.Shape.cast(tool.GetShape_s(sublabel));color=_get_ref_color(sublabel)or _get_shape_color(subshape.wrapped,colors)
                if color:part.addSubshape(subshape,color=color)
            for face in shape.Faces():
                color=_get_shape_color(face.wrapped,colors)
                if color:part.addSubshape(face,color=color)
            assembly.add(part)
    actual=assembly.toCompound().Solids();original=expected.Solids()
    def signature(solid):
        b=bounds(solid)
        return [solid.Volume(),*b['min'],*b['max']]
    a=sorted(signature(s) for s in actual);b=sorted(signature(s) for s in original)
    if len(a)!=len(b) or any(abs(x-y)>max(.00001,abs(y)*1e-7) for left,right in zip(a,b) for x,y in zip(left,right)):
        raise ValueError('Color-aware STEP import changed source solid geometry')
    colored=sum(len(n._subshape_colors) for n in assembly.objects.values())
    inherited=sum(n.color is not None for n in assembly.objects.values())
    # Uncolored models retain an explicit neutral fallback, without replacing
    # any colored faces or assembly-node colors from the source document.
    if not colored and not inherited:assembly.color=cq.Color(.7,.7,.74)
    return assembly,dict(sourceFaceColors=colored,sourceNodeColors=inherited,neutralFallback=not(colored or inherited),disambiguatedNames=renamed)


def preview_materials(path):
    """STEP provides colors, not measured optical materials. Avoid glTF's
    implicit metallic=1, which makes plastic and ceramic look metallic."""
    import struct
    from product_ide_3d.glb import read_glb
    document,binary=read_glb(path)
    for material in document.get('materials',[]):
        pbr=material.setdefault('pbrMetallicRoughness',{})
        pbr.update(metallicFactor=0,roughnessFactor=.5)
    document.setdefault('asset',{}).setdefault('extras',{})['formforgeAppearance']='STEP source colors; neutral nonmetal roughness 0.5; no measured optical material claim'
    text=json.dumps(document,separators=(',',':')).encode();text+=b' '*((-len(text))%4)
    data=bytes(binary);data+=b'\0'*((-len(data))%4)
    path.write_bytes(struct.pack('<III',0x46546C67,2,28+len(text)+len(data))+struct.pack('<II',len(text),0x4E4F534A)+text+struct.pack('<II',len(data),0x004E4942)+data)


def top_access_context(raw,root):
    """Measure only explicitly traced top passages outside the interior envelope.

    The original declared board.top_height remains in the recipe. This context
    only changes the coarse cavity/lip preflight; all solids still participate
    in exact shell collision and the per-part aperture/clearance checks.
    """
    path=root/'source-provenance.json'
    if raw.get('kind')!='enclosure' or not path.is_file():return {}
    dep=json.loads(path.read_text(encoding='utf8')).get('pcbDependency') or {}
    if not dep.get('geometryIncluded'):return {}
    openings=raw.get('openings',[])
    selected={p['footprintId'] for p in dep.get('passages',[]) if 0<=p['openingIndex']<len(openings) and openings[p['openingIndex']].get('face')=='top' and openings[p['openingIndex']].get('part')=='lid'}
    if not selected:return {}
    import cadquery as cq
    board=raw['board'];source=root/board['step']
    if sha(source)!=board['step_sha256']:raise ValueError('Top access PCBA STEP hash changed')
    solids=cq.importers.importStep(str(source)).solids().vals()
    known={p['footprintId'] for p in dep['geometryParts'] if p['footprintId']}
    if not selected<=known:raise ValueError('Top access references an unselected component')
    indices=[i for p in dep['geometryParts'] if p['footprintId'] not in selected for i in dep['partSolidIndices'][p['id']]]
    if not indices:raise ValueError('Top access cannot exempt every traced solid including the substrate')
    height=max(0,max(exact_box(solids[i]).zmax for i in indices)-board['origin'][2]-board['thickness'])
    return dict(topAccess=dict(enclosedTopHeightMm=height,footprintIds=sorted(selected),
        scope='Only named top-window parts excluded from coarse cavity height; exact aperture, all-solid shell collision and non-passage clearances remain required.'))

def verify_geometry(params):
    import cadquery as cq
    from product_ide_3d.cad import Model
    from product_ide_3d.glb import compact_glb
    from mechanical_worker import view_evidence,pcba_view
    from types import SimpleNamespace
    root=Path(params['root']);manifest=params['geometry'];shapes={};checks={};appearance={};colored={}
    for part in manifest['parts']:
        path=root/part['file']
        if sha(path)!=part['sha256']:raise ValueError('STEP input hash changed')
        solids=cq.importers.importStep(str(path)).solids().vals()
        checks[part['id']+'_valid_solids']=bool(solids) and all(s.isValid() and s.Volume()>0 for s in solids)
        if not checks[part['id']+'_valid_solids']:raise ValueError('Invalid STEP solids: '+part['id'])
        shapes[part['id']]=cq.Compound.makeCompound(solids)
        if part['id']!='pcb':
            colored[part['id']],appearance[part['id']]=colored_step(path,shapes[part['id']])
    board=shapes['pcb'];box=exact_box(board);outline=manifest['outline'];thickness=manifest['thickness']
    expected=[outline['xmin'],-outline['ymax'],0,outline['xmax'],-outline['ymin'],thickness]
    actual=[box.xmin,box.ymin,box.zmin,box.xmax,box.ymax,box.zmax]
    checks['substrate_bbox_and_cli_frame']=len(board.Solids())==1 and all(abs(a-b)<=.002 for a,b in zip(actual,expected))
    # Compare the actual outer wire independently of internal drill wires;
    # a bounding box alone cannot prove that corner material was removed.
    top=max((f for f in board.Faces() if f.normalAt().z>.999),key=lambda f:f.Center().z)
    outer=cq.Face.makeFromWires(top.outerWire());w=outline['xmax']-outline['xmin'];h=outline['ymax']-outline['ymin']
    corner_radius=outline.get('cornerRadius',0)
    from board_outline import edges
    frame=edges(dict(origin=[outline['xmin'],outline['ymin']],width=w,height=h,cornerRadius=corner_radius))
    reference=cq.Workplane('XY').moveTo(frame[0]['start'][0],-frame[0]['start'][1])
    for edge in frame:
        end=(edge['end'][0],-edge['end'][1])
        reference=reference.threePointArc((edge['mid'][0],-edge['mid'][1]),end) if edge['kind']=='arc' else reference.lineTo(*end)
    reference=reference.close().extrude(thickness).faces('>Z').val()
    mismatch=outer.cut(reference).Area()+reference.cut(outer).Area()
    checks['substrate_outer_profile']=mismatch<.002
    drills=[]
    for h in manifest['holes']:
        x,y=h['at'];radius=min(h['drill'])/2
        # Prove the centre and four interior radial points are void through the
        # substrate. This is a finite probe, not complete wall/diameter metrology.
        probes=[(0,0),(radius*.7,0),(-radius*.7,0),(0,radius*.7),(0,-radius*.7)]
        passed=outline['xmin']<x<outline['xmax'] and outline['ymin']<y<outline['ymax'] and all(not board.isInside(cq.Vector(x+dx,-y+dy,z),1e-6) for dx,dy in probes for z in [thickness*.1,thickness*.5,thickness*.9])
        drills.append(dict(id=h['id'],cadAt=[x,-y],passed=passed,probeCount=15))
    checks['declared_drill_void_probes']=all(h['passed'] for h in drills)
    assembly=cq.Assembly(name='pcba')
    for name,shape in shapes.items():
        if name=='pcb':assembly.add(shape,name=name,color=cq.Color(.15,.5,.3))
        else:assembly.add(colored[name],name=name)
    assembly.save(str(root/'assembly.step'));assembly.save(str(root/'assembly.glb'));compact_glb(root/'assembly.glb');preview_materials(root/'assembly.glb')
    model=Model(parts=shapes);view=view_evidence(SimpleNamespace(name='pcb-geometry'),model,root,exact_box)
    names={p['id']:p['reference'] or 'PCB 板体' for p in manifest['parts']}
    for part in view['parts']:part['displayName']=names[part['id']]
    view=pcba_view(view,root,cq.Compound.makeCompound(list(shapes.values())))
    reread=cq.importers.importStep(str(root/'assembly.step')).solids().vals()
    # The STEP solids are immutable here. OCC volume/bounds queries dominate
    # large PCBAs when repeated for every source/candidate pair.
    reread_metrics=[(bounds(s),s.Volume()) for s in reread]
    indices=[i for i,(b,_) in enumerate(reread_metrics) if all(abs(a-b)<=.002 for a,b in zip(b['min']+b['max'],expected))]
    checks['exported_assembly_substrate_identified']=len(indices)==1
    part_indices={};remaining=set(range(len(reread)))
    for name,shape in shapes.items():
        assigned=[]
        for solid in shape.Solids():
            b=bounds(solid);flat=b['min']+b['max'];volume=solid.Volume();matches=[]
            for i in remaining:
                ob,other_volume=reread_metrics[i]
                if abs(volume-other_volume)<=max(.001,volume*1e-5) and all(abs(a-b)<=.003 for a,b in zip(flat,ob['min']+ob['max'])):matches.append(i)
            if not matches:raise ValueError('Cannot trace source part to exported assembly solid: '+name)
            i=min(matches);remaining.remove(i);assigned.append(i)
        part_indices[name]=assigned
    checks['all_assembly_solids_traced']=not remaining
    result=dict(checksPassed=all(checks.values()),checks=checks,view=view,substrateSolidIndex=indices[0] if len(indices)==1 else None,
        substrate=dict(boundsCADmm=bounds(board),solidCount=len(board.Solids()),volumeMm3=board.Volume(),centerUndersideCADmm=[(box.xmin+box.xmax)/2,(box.ymin+box.ymax)/2,box.zmin],length=box.xlen,width=box.ylen,thickness=box.zlen),
        partSolidIndices=part_indices,drillVoidChecks=drills,outlineProof=dict(cornerRadius=corner_radius,symmetricDifferenceMm2=mismatch),appearance=dict(policy='step-source-colors-neutral-pbr-v1',parts=appearance,opticalMaterialsVerified=False),scope='Native valid solids, analytic rectangular/rounded outer profile and finite through-drill void probes. User view is one PCBA; component geometry remains traced internally. Not full hole diameter/slot metrology, fit, manufacture or hardware proof.')
    (root/'geometry-native-proof.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8');return result


def apply_enclosure_binding(design,model,root):
    """Allow only explicitly traced connector passages; keep exact collisions."""
    import math
    data=json.loads((root/'source-provenance.json').read_text(encoding='utf8'));dep=data.get('pcbDependency') or {}
    if not dep.get('geometryIncluded'):return False
    apply_retention(design,model,root,dep)
    passages=dep.get('passages',[])
    if not passages:return True
    by_id={p['footprintId']:p for p in dep['geometryParts'] if p['footprintId']};mapped={p['footprintId']:p['openingIndex'] for p in passages}
    if len(mapped)!=len(passages):raise ValueError('Connector passage footprint repeated')
    solids=model.board.Solids();side_ok=True;vertical_ok=True;rows=[];ztop=design.floor+design.height-1.6
    def merged_box(indices):
        from cadquery import Compound
        return exact_box(Compound.makeCompound([solids[i] for i in indices]))
    for part in dep['geometryParts']:
        box=merged_box(dep['partSolidIndices'][part['id']]);fid=part['footprintId'];opening=mapped.get(fid)
        vertical=min(box.zmin-design.floor,ztop-box.zmax)>=design.board_clearance-.002
        side=min(box.xmin+design.length/2,design.length/2-box.xmax,box.ymin+design.width/2,design.width/2-box.ymax)>=design.board_clearance-.002
        if opening is None:side_ok &= side;vertical_ok &= vertical;continue
        if fid not in by_id or not 0<=opening<len(design.openings):raise ValueError('Passage source or opening missing')
        o=design.openings[opening]
        if o.face not in ('left','right','front','back','top'):raise ValueError('PCB component passages require a side or top opening')
        if o.face in ('left','right'):
            plane=(-1 if o.face=='left' else 1)*design.length/2;cross=box.xmin<=plane<=box.xmax;u0,u1=box.ymin,box.ymax
            opposite=(box.xmax<=design.length/2-design.board_clearance+.002) if o.face=='left' else (box.xmin>=-design.length/2+design.board_clearance-.002)
            v0,v1=box.zmin,box.zmax
        elif o.face in ('front','back'):
            plane=(-1 if o.face=='front' else 1)*design.width/2;cross=box.ymin<=plane<=box.ymax;u0,u1=box.xmin,box.xmax
            opposite=(box.ymax<=design.width/2-design.board_clearance+.002) if o.face=='front' else (box.ymin>=-design.width/2+design.board_clearance-.002)
            v0,v1=box.zmin,box.zmax
        else:
            # A top access window can serve a recessed connector. It need not
            # cross the lid: the separate mating sweep must prove reach/access.
            cross=box.zmin<=design.floor+design.height<=box.zmax
            u0,u1=box.xmin,box.xmax;v0,v1=box.ymin,box.ymax;opposite=side
            # Only a named top access part may extend above the cavity/lip.
            # Its lower clearance and actual window/shell checks stay active.
            vertical=box.zmin-design.floor>=design.board_clearance-.002
        vertical_ok &= vertical
        # Conservative projection of the entire traced component plus explicit
        # clearance must fit the rounded aperture, not just its centre.
        w,h=o.width+2*o.clearance,o.height+2*o.clearance;r=o.radius
        def inside(u,v):
            a,b=abs(u-o.u),abs(v-o.v)
            return a<=w/2+.002 and b<=h/2+.002 and math.hypot(max(a-(w/2-r),0),max(b-(h/2-r),0))<=r+.002
        gap=next(p['clearance'] for p in passages if p['footprintId']==fid)
        aperture=all(inside(u,v) for u in [u0-gap,u1+gap] for v in [v0-gap,v1+gap])
        passed=bool((cross or o.face=='top') and aperture and opposite and vertical);side_ok &= passed
        rows.append(dict(footprintId=fid,reference=part['reference'],openingIndex=opening,face=o.face,componentCrossesWall=cross,accessWindow=o.face=='top',upperHeightExempt=o.face=='top',matingAccessVerified=False,entireProjectionFitsRoundedOpening=aperture,explicitClearance=gap,verticalGapPassed=vertical,passed=passed))
    if set(mapped)-set(by_id):raise ValueError('Passage references an unselected component')
    model.measures['strict_imported_assembly_side_gap']=model.measures['pcb_min_side_gap'];model.measures['controlled_pcb_passages']=rows
    model.checks.update(pcb_side_gap=bool(side_ok),pcb_vertical_gap=bool(vertical_ok),pcb_controlled_passages=all(p['passed'] for p in rows))
    model.warnings.append('Explicit selected connector side passages/top access windows use conservative projection and exact shell collision checks. A recessed top connector is allowed; mating access still requires a separate explicit insertion check. Omitted components, procurement tolerances and physical retention strength remain unverified.')
    return True


def apply_retention(design,model,root,dep):
    """Nominal supports/fastener space. Thread engagement is not a strength proof."""
    import cadquery as cq
    mounts=dep.get('mounts',[])
    if not mounts:return
    fastener=dep['fastener'];evidence=root/fastener['evidencePath']
    record=json.loads(evidence.read_text(encoding='utf8'))
    if sha(evidence)!=dep['fastenerEvidenceSha256'] or record['parameters']!={k:v for k,v in fastener.items() if k!='evidencePath'}:raise ValueError('Frozen nominal fastener envelope changed')
    solids=model.board.Solids();substrate=solids[design.board.substrate_solid_index];others=[s for i,s in enumerate(solids) if i!=design.board.substrate_solid_index]
    bottom=design.floor+design.standoff_height;top=bottom+design.board.thickness;tip=top-fastener['underHeadLength'];rows=[]
    def cylinder(x,y,r,z0,z1):return cq.Solid.makeCylinder(r,z1-z0,cq.Vector(x,y,z0))
    def volume(a,b):
        x,y=exact_box(a),exact_box(b)
        if any(a1<=b0+1e-7 or b1<=a0+1e-7 for a0,a1,b0,b1 in [(x.xmin,x.xmax,y.xmin,y.xmax),(x.ymin,x.ymax,y.ymin,y.ymax),(x.zmin,x.zmax,y.zmin,y.zmax)]):return 0.0
        return a.intersect(b).Volume()
    posts=[f for f in model.features if f['type']=='pcb_standoff']
    intrusion=sum(volume(model.parts['base'],s)+volume(model.parts['lid'],s) for s in solids)
    for feature in mounts:
        hole=feature['localHole'];x,y,d=hole['x'],hole['y'],hole['diameter'];post=next((p for p in posts if abs(p['x']-x)<1e-6 and abs(p['y']-y)<1e-6),None)
        if post is None:raise ValueError('Declared PCB support was not built')
        r0=d/2;r1=post['post_diameter']/2;depth=.04
        def annulus(z):return cylinder(x,y,r1,z,z+depth).cut(cylinder(x,y,r0,z-.001,z+depth+.001))
        lower=annulus(bottom-depth);upper=annulus(bottom)
        support_contact=volume(model.parts['base'],lower)>=lower.Volume()*.999 and volume(substrate,upper)>=upper.Volume()*.999
        bearing_radius=fastener['headDiameter']/2
        bearing=cylinder(x,y,bearing_radius,top-depth,top).cut(cylinder(x,y,r0,top-depth-.001,top+.001)) if bearing_radius>r0 else None
        head_bearing=bool(bearing) and volume(substrate,bearing)>=bearing.Volume()*.999
        shaft=cylinder(x,y,fastener['shaftDiameter']/2,tip,top)
        head_space=cylinder(x,y,bearing_radius+fastener['headSpaceClearance'],top,top+fastener['headHeight']+fastener['headSpaceClearance'])
        screw_intrusion=sum(volume(shaft,s)+volume(head_space,s) for s in others)+sum(volume(head_space,s) for s in model.parts.values())
        radial=(d-fastener['shaftDiameter'])/2;engagement=bottom-tip
        dimensions=abs(design.screw_diameter-fastener['shaftDiameter'])<1e-6 and fastener['shaftDiameter']>post['pilot_diameter'] and bearing_radius>r0 and radial>=fastener['minRadialClearance']-1e-6 and engagement>=fastener['minEngagement']-1e-6 and tip-design.floor>=fastener['minTipClearance']-1e-6
        passed=bool(support_contact and head_bearing and dimensions and intrusion<1e-5 and screw_intrusion<1e-5)
        rows.append(dict(featureId=feature['id'],reference=feature['reference'],cadHole=hole,supportContactPassed=bool(support_contact),headBearingPassed=bool(head_bearing),dimensionsPassed=bool(dimensions),radialClearanceMm=radial,nominalEngagementMm=engagement,tipFloorClearanceMm=tip-design.floor,pcbaShellCollisionMm3=intrusion,fastenerSpaceCollisionMm3=screw_intrusion,passed=passed))
    model.measures['pcb_retention']=dict(scope='Nominal planar support contact and engineering fastener clearance envelope; no screw vendor tolerance, thread/strength or assembly qualification.',fastener=fastener,rows=rows)
    model.checks['pcb_nominal_retention']=len(rows)==len(mounts) and all(r['passed'] for r in rows)
    model.warnings.append('PCB supports and fastener space use a declared nominal engineering envelope. Fasteners are not exported as purchased parts; thread strength, assembly sequence and procurement tolerances remain unverified.')


def verify_bound_enclosure(design,root):
    import cadquery as cq
    from product_ide_3d.cad import build_model
    recorded=json.loads((root/'report.json').read_text(encoding='utf8'))
    for name,value in recorded['artifact_hashes'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path)!=value:raise ValueError('Bound enclosure output changed: '+name)
    model=build_model(design,root);apply_enclosure_binding(design,model,root);checks=dict(model.checks)
    for name,shape in model.parts.items():
        imported=cq.importers.importStep(str(root/(name+'.step'))).val();a,b=bounds(shape),bounds(imported)
        checks[name+'_step_roundtrip']=imported.isValid() and len(imported.Solids())==len(shape.Solids()) and abs(imported.Volume()-shape.Volume())<=max(.001,shape.Volume()*1e-6) and all(abs(a-b)<=.002 for a,b in zip(a['min']+a['max'],b['min']+b['max']))
    imported=cq.importers.importStep(str(root/'assembly.step')).val();expected=list(model.parts.values())+([model.board] if model.board else [])
    compound=cq.Compound.makeCompound(expected);a,b=bounds(imported),bounds(compound)
    checks['assembly_step_roundtrip']=imported.isValid() and len(imported.Solids())==sum(len(s.Solids()) for s in expected) and abs(imported.Volume()-sum(s.Volume() for s in expected))<=max(.001,imported.Volume()*1e-6) and all(abs(a-b)<=.003 for a,b in zip(a['min']+a['max'],b['min']+b['max'])) and (imported.Center()-compound.Center()).Length<=.003
    checks['recorded_checks_match']=model.checks==recorded['checks']
    return dict(passed=all(checks.values()),checks=checks,artifact_count=len(recorded['artifact_hashes']))
