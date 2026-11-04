"""Versioned display-only assembly groups; independent of output list order."""
import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]
AGENT_INSTRUCTIONS = (
    '机械爆炸视图按装配关系分组，不按零件列表序号分配位移。'
    'feature_model或assembly可声明explosion.fixed_parts及groups；每组指定id、parts、CAD方向direction和1倍位移distance_mm。'
    '同组按键/支撑柱保持相同位移；贴附标识与所属壳体同组；PCB及全部板载元件仍为一个PCBA。'
    '按实际装配方向安排分层，后盖向后、侧拨杆向侧面，使用紧凑距离；不要让全部零件向上飞散。'
    '紧固连接可声明receiver_host_part，最终实体核对该承压/防转成员，并将螺母相对其成员的爆炸方向与receiver_entry_offset比较；读取explosionDirectionReview，方向矛盾须复核。'
    '未声明的零件按实际几何高度分层作为查看默认，不能推断真实拆卸顺序或运动可行性；普通建模不要求额外制造证明。'
)


class ExplosionGroup(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: Name
    parts: list[Name] = Field(min_length=1)
    direction: tuple[float, float, float] = Field(default=(0, 0, 1), description='CAD XYZ方向，自动归一化；同组零件使用相同位移。')
    distance_mm: float = Field(gt=0, description='滑块1倍时的位移mm；用于查看，不声明实际可拆卸。')

    @model_validator(mode='after')
    def nonzero_direction(self):
        if math.hypot(*self.direction) < 1e-12:
            raise ValueError('Explosion direction must be nonzero')
        return self


class ExplosionPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    fixed_parts: list[Name] = Field(default_factory=list)
    groups: list[ExplosionGroup] = Field(default_factory=list)

    def validate_parts(self, names):
        known = set(names); used = set(); group_ids = set()
        for part in self.fixed_parts:
            if part not in known or part in used:
                raise ValueError('Unknown or repeated explosion part: ' + part)
            used.add(part)
        for group in self.groups:
            if group.id in group_ids:
                raise ValueError('Duplicate explosion group: ' + group.id)
            group_ids.add(group.id)
            for part in group.parts:
                if part not in known or part in used:
                    raise ValueError('Unknown or repeated explosion part: ' + part)
                used.add(part)


def review_joint_directions(design):
    """Compare explicit receiver/host displacements; never infer disassembly."""
    if hasattr(design,'model_dump'):design=design.model_dump()
    plan=design.get('explosion') or {};engineering=design.get('engineering_plan') or {}
    vectors={p:[0.,0.,0.] for p in plan.get('fixed_parts',[])}
    for group in plan.get('groups',[]):
        direction=group.get('direction',[0,0,1]);length=math.hypot(*direction)
        if length<1e-12:raise ValueError('Explosion direction must be nonzero')
        vector=[v/length*group['distance_mm'] for v in direction]
        for p in group['parts']:vectors[p]=vector
    findings=[];rows=[];uncovered=[]
    for joint in engineering.get('joints',[]):
        if joint.get('method') not in {'captive_nut','set_screw_nut'}:continue
        host=joint.get('receiver_host_part');receiver=joint.get('receiver_part');entry=joint.get('receiver_entry_offset')
        if not host or not entry or host not in vectors or receiver not in vectors:
            uncovered.append(dict(jointId=joint['id'],reason='explicit-host-entry-or-group-missing'));continue
        relative=[a-b for a,b in zip(vectors[receiver],vectors[host])];length=math.hypot(*relative)
        if length<1e-6:
            uncovered.append(dict(jointId=joint['id'],reason='receiver-moves-with-host-no-removal-declared'));continue
        entry_length=math.hypot(*entry)
        if entry_length<1e-12:raise ValueError('Nut entry offset must be nonzero')
        alignment=sum(a*b for a,b in zip(relative,entry))/(length*entry_length)
        row=dict(jointId=joint['id'],receiverPart=receiver,hostPart=host,relativeVectorCADmm=relative,entryOffsetCADmm=list(entry),alignment=round(alignment,9),needsReview=alignment<1-1e-6)
        rows.append(row)
        if row['needsReview']:findings.append(row)
    return dict(policy='joint-receiver-direction-v1',comparedCount=len(rows),findings=findings,uncovered=uncovered,
                status='needs-review' if findings else 'uncovered' if uncovered else 'compared' if rows else 'unplanned',
                scope='Declared receiver displacement relative to its explicit, native-checked host compared with entry offset. Advisory only; no collision-free disassembly, sequence or load proof.')


def apply_explosion(parts, plan=None):
    """Set shared vectors from declared groups, with a geometric layer fallback.

    Bounds are measured CAD geometry, not part names, list indices or mass
    centroids. Display separation does not prove a collision-free removal path.
    """
    plan = ExplosionPlan.model_validate(plan or {})
    plan.validate_parts(p['id'] for p in parts)
    by_id = {p['id']: p for p in parts}; assigned = set(); groups = []

    def assign(group_id, members, vector, source):
        vector = [round(v, 6) for v in vector]
        for name in members:
            by_id[name]['explodeVectorCADmm'] = vector.copy()
            by_id[name]['explodeGroup'] = group_id
            assigned.add(name)
        groups.append(dict(id=group_id, parts=sorted(members), vectorCADmm=vector, source=source))

    if plan.fixed_parts:
        assign('fixed:', plan.fixed_parts, [0, 0, 0], 'declared-fixed')
    for group in plan.groups:
        length = math.hypot(*group.direction)
        assign(group.id, group.parts, [v / length * group.distance_mm for v in group.direction], 'declared')
    remaining = [p for p in parts if p['id'] not in assigned]
    if remaining:
        bottom = min(p['boundsCADmm']['min'][2] for p in remaining)
        top = max(p['boundsCADmm']['max'][2] for p in remaining)
        spans = [max(p['boundsCADmm']['max'][i] for p in parts) - min(p['boundsCADmm']['min'][i] for p in parts) for i in range(3)]
        gap = max(3., min(20., min((s for s in spans if s > 1e-6), default=10.) * .35))
        tolerance = max(.25, min(2., (top - bottom) * .05))
        center = lambda p: (p['boundsCADmm']['min'][2] + p['boundsCADmm']['max'][2]) / 2
        layers = []
        for part in sorted(remaining, key=lambda p: (center(p), p['id'])):
            if not layers or center(part) - center(layers[-1][0]) > tolerance:
                layers.append([])
            layers[-1].append(part)
        previous_top = max((by_id[n]['boundsCADmm']['max'][2] for n in plan.fixed_parts), default=None)
        previous_distance = 0.
        for index, layer in enumerate(layers):
            low = min(p['boundsCADmm']['min'][2] for p in layer)
            high = max(p['boundsCADmm']['max'][2] for p in layer)
            distance = 0 if previous_top is None else max(previous_distance + gap, previous_top + gap - low)
            assign('geometry:' + str(index), [p['id'] for p in layer], [0, 0, distance], 'geometry-layer')
            previous_top = high + distance
            previous_distance = distance
    return dict(policy='assembly-groups-v1', groups=groups, fallback='measured-z-layers', viewOnly=True,
        scope='Display separation preserves each group pose; not disassembly sequence or collision-free travel proof.')
