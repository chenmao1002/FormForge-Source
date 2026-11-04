"""Output solid intent and connectivity; assemblies are not fused into parts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

POLICY = dict(version='part-integrity-v1', kinds=['feature_model','assembly'],
    authoredDefault='single', importedRigidStepDefault='source-assembly',
    fields=['solid_policy','assembly_reason'],
    verification='Native final output and independent exported STEP solid count',
    limits='Single valid solid is not minimum wall thickness, strength, tolerance or manufacturing approval.')
AGENT_INSTRUCTIONS = '\n机械输出先读 mechanical_schema.partIntegrityPolicy：原生构造的一体零件默认必须恰好一个有效实体；多实体会拒绝采纳。parts[].solid_policy=single显式声明壳体等一体件；确属总成或参考分组时用assembly并填写assembly_reason，不能为绕过断裂检查声明总成。直接STEP及其刚性变换缺省保留来源总成；导入的一体制作件须显式single，engineering_plan制作件强制single。检查measures.partIntegrity及独立STEP连通性；单实体不代替连接壁厚、PCB净空和装配验收。\n'

class SolidIntent(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    solid_policy: Literal['auto','single','assembly'] = Field(default='auto',description='auto: 构造件必须单实体，直接STEP/刚性变换保留来源总成；一体壳体明确single。')
    assembly_reason: str = Field(default='',max_length=1500,description='显式assembly须说明真实总成/参考分组依据，不得用于掩盖一体零件断裂。')

    @model_validator(mode='after')
    def assembly_basis(self):
        if self.solid_policy=='assembly' and not self.assembly_reason.strip():
            raise ValueError('assembly solid_policy requires assembly_reason')
        return self

def inspect_integrity(design, parts):
    features={f.id:f for f in getattr(design,'features',[])}
    fabricated=set(getattr(getattr(design,'engineering_plan',None),'fabricated_parts',[]))
    rows=[]
    for part in design.parts:
        shape=parts[part.name];count=len(shape.Solids());import_path=getattr(part,'step',None)
        if features:
            feature=features[part.feature]
            while feature.op=='transform':feature=features[feature.source]
            import_path=feature.step if feature.op=='step' else None
        policy=part.solid_policy
        if part.name in fabricated:
            effective='single';basis='engineering-fabricated-part'
        elif policy=='assembly':
            effective='assembly';basis=part.assembly_reason
        elif policy=='auto' and import_path:
            effective='assembly';basis='Frozen STEP source assembly: '+import_path
        else:
            effective='single';basis='explicit-single' if policy=='single' else 'authored-part-default'
        passed=shape.isValid() and count>0 and (effective=='assembly' or count==1)
        row=dict(part=part.name,declaredPolicy=policy,effectivePolicy=effective,basis=basis,solidCount=count,passed=bool(passed))
        if not passed:
            row['finding']='One-piece output must contain exactly one valid solid'
            row['solidBoundsCADmm']=[dict(min=[b.xmin,b.ymin,b.zmin],max=[b.xmax,b.ymax,b.zmax]) for b in [s.BoundingBox() for s in shape.Solids()[:16]]]
        rows.append(row)
    return dict(policy='part-integrity-v1',checksPassed=all(r['passed'] for r in rows),parts=rows,
                singlePartCount=sum(r['effectivePolicy']=='single' for r in rows),assemblyCount=sum(r['effectivePolicy']=='assembly' for r in rows),
                failures=[r['part'] for r in rows if not r['passed']],scope=POLICY['limits'])

def apply_integrity(design, model):
    report=inspect_integrity(design,model.parts)
    model.measures['partIntegrity']=report
    model.checks['output_part_integrity']=report['checksPassed']
    model.warnings.extend('Disconnected one-piece output: '+p for p in report['failures'])
    return report
