"""Isolated data-only CAD worker; import path is the frozen local bundle."""
import importlib.metadata
import json
import math
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from mechanical_inputs import KINDS,validate_references
from mechanical_explosion import apply_explosion

def sha(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()

def model_type(kind):
    if kind not in KINDS:raise ValueError('Unsupported mechanical kind')
    if kind=='feature_model':
        from mechanical_features import FeatureDesign
        return FeatureDesign
    if kind=='assembly':
        from mechanical_assembly import GeneralAssembly
        return GeneralAssembly
    if kind=='rotary_assembly':
        from product_ide_3d.assembly_spec import AssemblyDesign
        return AssemblyDesign
    if kind=='articulated_arm':
        from product_ide_3d.arm_spec import ArmDesign
        return ArmDesign
    from product_ide_3d.spec import Design
    from pydantic import Field,ValidationInfo,model_validator
    class FormForgeDesign(Design):
        evidence_files: list[str] = Field(default_factory=list,max_length=80)
        @model_validator(mode='after')
        def engineering_constraints(self,info:ValidationInfo):
            top=(info.context or {}).get('topAccess')
            if not top or not self.board:return super().engineering_constraints()
            declared=self.board.top_height
            try:
                self.board.top_height=top['enclosedTopHeightMm']
                return super().engineering_constraints()
            finally:self.board.top_height=declared
    return FormForgeDesign

def view_evidence(design,model,root,bounding_box=None):
    from product_ide_3d.glb import read_glb,scene_coordinate_frame
    document,_=read_glb(root/'assembly.glb');frame=scene_coordinate_frame(document)
    if not math.isfinite(frame['linear_determinant']) or frame['linear_determinant']<=0:raise ValueError('CAD/GLB frame must preserve handedness')
    shapes=dict(model.parts)
    if getattr(model,'board',None) is not None:shapes['pcb']=model.board
    from mechanical_presentation import part_presentations
    from mechanical_engineering import registry
    # Geometry and insertion diagnostics use a name-only scene descriptor.
    # Only authored design schemas carry editable part/hardware presentation.
    design_data=design.model_dump() if hasattr(design,'model_dump') else {}
    presentation={p['id']:p for p in part_presentations(design_data,registry()['hardware'])}
    parts=[];node_ids=[]
    for name,shape in shapes.items():
        if hasattr(shape,'val'):shape=shape.val()
        box=bounding_box(shape) if bounding_box else shape.BoundingBox()
        matches=[i for i,n in enumerate(document['nodes']) if n.get('name','').split('/')[-1]==name]
        if len(matches)!=1:raise ValueError('GLB part name is missing or ambiguous: '+name+' '+str(matches))
        index=matches[0];node_ids.append(index);center=shape.Center()
        parts.append(dict(id=name,nodeIndex=index,nodeName=document['nodes'][index]['name'],volumeMm3=shape.Volume(),solidCount=len(shape.Solids()),
            boundsCADmm=dict(min=[box.xmin,box.ymin,box.zmin],max=[box.xmax,box.ymax,box.zmax]),centroidCADmm=[center.x,center.y,center.z]))
        if name in {'pcb','pcba'}:parts[-1]['displayName']='电路板组件（PCB＋元器件）'
        elif name=='display_module':parts[-1]['displayName']='显示屏总成'
        if name in presentation and (presentation[name].get('colorGroup') or presentation[name]['displayName']!=name):
            parts[-1].update({k:v for k,v in presentation[name].items() if k!='id'})
    def descendants(index):
        return {child for c in document['nodes'][index].get('children',[]) for child in [c,*descendants(c)]}
    if any((set(node_ids)-{i})&descendants(i) for i in node_ids):raise ValueError('Explodable part nodes must not contain each other')
    plan=getattr(design,'explosion',None)
    explosion=apply_explosion(parts,plan.model_dump() if plan is not None else None)
    result=dict(schemaVersion=1,units='mm',cadFrame='right-handed XYZ, +Z up',coordinateFrame=frame,parts=parts,glbSha256=sha(root/'assembly.glb'),stepSha256=sha(root/'assembly.step'),
        explosion=explosion,explosionViewOnly=True,scope='Measured native solids and actual GLB node identities; grouped CAD-space separation, not disassembly or physical clearance proof')
    # Reject non-finite geometry evidence, even if the exporter emitted a file.
    json.dumps(result,allow_nan=False)
    (root/'assembly-view.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8');return result


def pcba_view(view,root,shape):
    """One user-visible PCBA; component rows remain internal geometric evidence."""
    from product_ide_3d.glb import read_glb
    document,_=read_glb(root/'assembly.glb');roots=document['scenes'][document.get('scene',0)]['nodes']
    if len(roots)!=1:raise ValueError('PCBA must have one assembly root')
    index=roots[0];box=shape.BoundingBox();center=shape.Center()
    view['componentParts']=view['parts']
    view['parts']=[dict(id='pcba',displayName='电路板组件（PCB＋元器件）',nodeIndex=index,nodeName=document['nodes'][index].get('name','pcba'),
        volumeMm3=shape.Volume(),solidCount=len(shape.Solids()),boundsCADmm=dict(min=[box.xmin,box.ymin,box.zmin],max=[box.xmax,box.ymax,box.zmax]),
        centroidCADmm=[center.x,center.y,center.z],explodeVectorCADmm=[0,0,0])]
    view['partPolicy']='pcba-atomic-v1'
    view['explosion']=apply_explosion(view['parts'],dict(fixed_parts=['pcba']))
    (root/'assembly-view.json').write_text(json.dumps(view,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    return view

def main(params):
    action=params['action']
    if action in ('insertion_check','insertion_verify'):
        from mechanical_motion import evaluate,verify
        return evaluate(Path(params['root']),export=True) if action=='insertion_check' else verify(Path(params['root']))
    if action=='pcb_geometry_verify':
        from mechanical_pcb import verify_geometry
        return verify_geometry(params)
    if action=='probe':
        import cadquery
        import OCP
        return dict(python=sys.version,pythonExecutable=sys.executable,versions={n:importlib.metadata.version(n) for n in ['cadquery','cadquery-ocp','pydantic','ezdxf','reportlab','numpy']})
    if action=='schema':
        schema=model_type(params['kind']).model_json_schema();schema['properties']['kind']={'const':params['kind'],'type':'string','default':params['kind']}
        return dict(kind=params['kind'],schema=schema,units='mm',cadFrame='right-handed XYZ, +Z up',arbitraryPythonAccepted=False)
    root=Path(params['root']);raw=json.loads((root/'design.json').read_text(encoding='utf8'));kind=raw.get('kind')
    # Read no undeclared geometry while deriving the controlled top-access
    # exception. The original source height is preserved in normalizedDesign.
    validate_references(raw,params.get('files',{}))
    from mechanical_pcb import top_access_context
    context=top_access_context(raw,root)
    design=model_type(kind).model_validate(raw,context=context)
    if any(p.name in {'assembly','exploded',design.name} for p in getattr(design,'parts',[])):raise ValueError('Part names collide with assembly output names')
    validate_references(design.model_dump(),params.get('files',{}))
    if action=='build':
        if kind=='feature_model':
            from mechanical_features import build
            model=build(design,root)
        elif kind=='assembly':
            from mechanical_assembly import build
            model=build(design,root)
        elif kind=='rotary_assembly':
            from product_ide_3d.assembly_cad import build_assembly
            model=build_assembly(design,root,preflight=True)
        elif kind=='articulated_arm':
            from product_ide_3d.arm_cad import build_arm
            model=build_arm(design,root)
        else:
            from product_ide_3d.cad import build_model
            model=build_model(design,root)
            if kind=='enclosure':
                from mechanical_pcb import apply_enclosure_binding
                apply_enclosure_binding(design,model,root)
                if context.get('topAccess'):model.measures['controlled_top_access_height']=context['topAccess']
        normalized=design.model_dump()
        if not normalized.get('evidence_files'):normalized.pop('evidence_files',None)
        (root/'design.json').write_text(json.dumps(normalized,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
        if kind in ('assembly','feature_model'):
            from mechanical_assembly import export
            report=export(design,model,root)
        else:
            from product_ide_3d.delivery import export_delivery
            report=export_delivery(design,model,root)
        from mechanical_appearance import apply_appearance
        appearance=apply_appearance(design,root)
        if kind=='feature_model':
            from mechanical_pcb import exact_box
            view=view_evidence(design,model,root,bounding_box=exact_box)
        else:
            view=view_evidence(design,model,root)
        return dict(report=report,view=view,normalizedDesign=normalized,appearance=appearance)
    if action=='verify':
        if kind=='feature_model':
            from mechanical_features import verify
            from mechanical_appearance import verify_appearance
            return verify_appearance(design,root,verify(design,root))
        if kind in ('enclosure','panel','u_bracket') and raw.get('evidence_files'):
            from mechanical_pcb import verify_bound_enclosure
            return verify_bound_enclosure(design,root)
        if kind=='enclosure' and json.loads((root/'source-provenance.json').read_text(encoding='utf8')).get('pcbDependency',{}):
            dependency=json.loads((root/'source-provenance.json').read_text(encoding='utf8'))['pcbDependency']
            if dependency.get('geometryIncluded'):
                from mechanical_pcb import verify_bound_enclosure
                return verify_bound_enclosure(design,root)
        if kind=='assembly':
            from mechanical_assembly import verify
            from mechanical_appearance import verify_appearance
            return verify_appearance(design,root,verify(design,root))
        from product_ide_3d.delivery import verify
        return verify(root)
    raise ValueError('Unsupported mechanical action')

if __name__=='__main__':
    result=main(json.loads(Path(sys.argv[1]).read_text(encoding='utf8')))
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
