"""General nominal assemblies without invented motor or torque requirements."""
import hashlib
import itertools
import json
from pathlib import Path
from typing import Annotated,Literal
from pydantic import Field,model_validator
from product_ide_3d.spec import Record
from product_ide_3d.assembly_spec import AxialPart
from mechanical_explosion import ExplosionPlan
from mechanical_engineering import EngineeringPlan,inspect_plan
from mechanical_presentation import apply_screw_style
from mechanical_appearance import AppearancePlan
from mechanical_integrity import SolidIntent, apply_integrity, inspect_integrity

class GeneralPart(AxialPart, SolidIntent):
    role: Literal['designed','machined','purchased','fastener']='designed'
    display_name: str | None = Field(default=None,max_length=120)


class GeneralAssembly(Record):
    schema_version: Literal[1]=1
    kind: Literal['assembly']='assembly'
    name: str=Field(default='mechanical-assembly',pattern=r'^[A-Za-z0-9_-]{1,64}$')
    parts: list[GeneralPart]=Field(min_length=1,max_length=128)
    screw_color: tuple[Annotated[float,Field(ge=0,le=1)],Annotated[float,Field(ge=0,le=1)],Annotated[float,Field(ge=0,le=1)]] | None = None
    evidence_files: list[str]=Field(default_factory=list,max_length=80)
    assumptions: list[str]=Field(default_factory=list,max_length=40)
    notes: list[str]=Field(default_factory=list,max_length=40)
    explosion: ExplosionPlan | None = Field(default=None,description='查看用固定件、分组、CAD方向与1倍距离；不证明拆卸可行性。')
    engineering_plan: EngineeringPlan | None = None
    appearance: AppearancePlan | None = None

    @model_validator(mode='after')
    def unique_explicit_parts(self):
        if len({p.name for p in self.parts})!=len(self.parts):raise ValueError('Part names must be unique')
        if any(p.name in {'assembly','exploded',self.name} for p in self.parts):raise ValueError('Part names cannot collide with assembly output names')
        if any(p.mirror_of for p in self.parts):raise ValueError('General assembly requires explicit part geometry; mirror aliases use the specialized rotary_assembly schema')
        if self.explosion is not None:self.explosion.validate_parts(p.name for p in self.parts)
        if self.engineering_plan is not None:self.engineering_plan.validate_parts(p.name for p in self.parts)
        from mechanical_engineering import registry
        apply_screw_style(self,registry()['hardware'])
        return self

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def step_roundtrip(path,shape,*,imported=None):
    import cadquery as cq
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    if imported is None:imported=cq.importers.importStep(str(path)).val()
    before,after=shape.BoundingBox(),imported.BoundingBox()
    bounds=['xmin','ymin','zmin','xmax','ymax','zmax']
    # Adaptive integration is essential for trimmed lofts. Default fixed-order
    # quadrature can give different masses after an otherwise exact STEP replay.
    def properties(s):
        p=GProp_GProps();BRepGProp.VolumeProperties_s(s.wrapped,p,1e-10)
        return p.Mass(),cq.Vector(p.CentreOfMass())
    volume,center=properties(shape);exported_volume,exported_center=properties(imported)
    return imported.isValid() and len(imported.Solids())==len(shape.Solids()) and abs(exported_volume-volume)<=max(.001,volume*1e-6) and all(abs(getattr(before,k)-getattr(after,k))<=.001 for k in bounds) and (exported_center-center).Length<=.001

def build(design,root):
    from product_ide_3d.assembly_cad import build_assembly_parts,bounds_overlap,_aabb_distance_lower_bound
    from mechanical_collision import intersection_volume
    model=build_assembly_parts(design,root);pairs=[]
    geometry={}
    for name,shape in model.parts.items():
        box=shape.BoundingBox();solids=shape.Solids()
        geometry[name]=(box,[(s,box if len(solids)==1 else s.BoundingBox()) for s in solids])
    model._collision_geometry=geometry
    for (a,first),(b,second) in itertools.combinations(model.parts.items(),2):
        box_a,leaves_a=geometry[a];box_b,leaves_b=geometry[b]
        candidate=bounds_overlap(box_a,box_b);lower=_aabb_distance_lower_bound(box_a,box_b)
        overlap=intersection_volume(first,second,first_leaves=leaves_a,second_leaves=leaves_b) if candidate else 0
        pairs.append(dict(partA=a,partB=b,intersectionMm3=overlap,intersectionEvaluation='exact' if candidate else 'bounds',
            minimumDistanceMm=first.distance(second) if lower<=2 else None,aabbDistanceLowerBoundMm=lower))
    model.checks['assembly_no_interference']=all(r['intersectionMm3']<1e-5 for r in pairs)
    model.measures['pairwise_interference']=pairs
    model.measures['engineering']=inspect_plan(design,model)
    from mechanical_explosion import review_joint_directions
    review=review_joint_directions(design);model.measures['explosionDirectionReview']=review
    model.warnings.extend('Explosion direction needs review: '+r['jointId'] for r in review['findings'])
    if design.engineering_plan is not None:model.checks['engineering_declared_geometry']=model.measures['engineering']['checksPassed']
    model.warnings.append('General nominal assembly: CNC features, physical tolerance/load/thermal and disassembly qualification are not inferred.')
    apply_integrity(design,model)
    return model

def export(design,model,root):
    import cadquery as cq
    import csv
    from time import perf_counter
    started=perf_counter();timings={}
    from product_ide_3d.glb import compact_glb
    from mechanical_presentation import part_presentations
    from mechanical_engineering import registry
    screws={p['id'] for p in part_presentations(design.model_dump(),registry()['hardware']) if p.get('colorGroup')=='screws'}
    assembly=cq.Assembly(name=design.name)
    for i,(name,shape) in enumerate(model.parts.items()):
        cq.exporters.export(shape,str(root/(name+'.step')));cq.exporters.export(shape,str(root/(name+'.stl')),tolerance=.05,angularTolerance=.1)
        color=model.metadata[name].color or ((.15,.55,.65) if i%2==0 else (.8,.67,.3))
        appearance=None if name in screws else getattr(model,'appearances',{}).get(name)
        if appearance is not None:assembly.add(appearance,name=name)
        else:assembly.add(shape,name=name,color=cq.Color(*color))
    timings['partExportsSeconds']=perf_counter()-started;stage=perf_counter()
    assembly.save(str(root/'assembly.step'))
    timings['assemblyStepSeconds']=perf_counter()-stage;stage=perf_counter()
    assembly.save(str(root/'assembly.glb'))
    timings['assemblyGlbSeconds']=perf_counter()-stage;stage=perf_counter()
    compact_glb(root/'assembly.glb')
    timings['glbCompactionSeconds']=perf_counter()-stage
    model.measures.setdefault('performance',{})['export']=timings
    with (root/'hardware-bom.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.writer(stream);writer.writerow(['catalogId','kind','thread','length_mm','quantity','material','source'])
        entries=model.measures.get('hardwareBom',[])
        for key in sorted({r['partId'] for r in entries}):
            row=next(r for r in entries if r['partId']==key)
            writer.writerow([key,row['kind'],row['thread'],row.get('length',''),sum(r['partId']==key for r in entries),row['material'],row['source']])
    report=dict(schema_version=1,kind=design.kind,cad_verified=all(model.checks.values()),manufacturing_ready=False,checks=model.checks,measures_mm=model.measures,
        warnings=model.warnings,artifact_hashes={p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob('*')) if p.is_file()})
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8');return report

def verify(design,root,builder=build):
    import cadquery as cq
    from time import perf_counter
    started=perf_counter();timings={}
    recorded=json.loads((root/'report.json').read_text(encoding='utf8'))
    for name,value in recorded['artifact_hashes'].items():
        target=(root/name).resolve()
        if not target.is_relative_to(root.resolve()) or sha(target)!=value:raise ValueError('General assembly artifact changed: '+name)
    timings['artifactHashesSeconds']=perf_counter()-started;stage=perf_counter()
    rebuilt=builder(design,root);checks=dict(rebuilt.checks)
    timings['independentRebuildSeconds']=perf_counter()-stage;stage=perf_counter()
    exported_parts={}
    for name,shape in rebuilt.parts.items():
        exported_parts[name]=cq.importers.importStep(str(root/(name+'.step'))).val()
        checks[name+'_step_roundtrip']=step_roundtrip(root/(name+'.step'),shape,imported=exported_parts[name])
    exported_integrity=inspect_integrity(design,exported_parts)
    checks['exported_output_part_integrity']=exported_integrity['checksPassed']
    timings['partStepReadbackSeconds']=perf_counter()-stage;stage=perf_counter()
    checks['assembly_step_roundtrip']=step_roundtrip(root/'assembly.step',cq.Compound.makeCompound(list(rebuilt.parts.values())))
    timings['assemblyStepReadbackSeconds']=perf_counter()-stage
    checks['recorded_checks_match']=rebuilt.checks==recorded['checks']
    timings['verifySeconds']=perf_counter()-started
    return dict(passed=all(checks.values()),checks=checks,partIntegrity=exported_integrity,artifact_count=len(recorded['artifact_hashes']),performance=timings)
