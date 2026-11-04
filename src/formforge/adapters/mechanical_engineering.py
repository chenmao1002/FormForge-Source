"""Manufacturing intent and measured critical interfaces, never production approval."""
import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator,AliasChoices

DEFAULT_PROFILE='jlc-sla-9000he-20261025-v2'
AGENT_INSTRUCTIONS=(
    '机械实物设计须先确定工艺、材料、表面处理、配合类型和固定方式，首选嘉立创3D打印规范，不能只凭无干涉声称可打印或可装配。'
    '先读mechanical_schema的manufacturingPlanning，按适用process profile设计；profile按版本保存，可扩展其他厂商和CNC，不能套用SLA间隙。'
    'feature_model/assembly用engineering_plan声明profile_id、process、material、finish、制作/购买/参考件、关键壁厚截面、配合与紧固连接。'
    '概念造型可不填，但报告应为unplanned，不能提升为准备制造。原生螺孔须在最终所有布尔操作后检查实际螺钉通路、孔口、头部和工具空间、咬合长度及接收件。'
    '螺钉从标准件库选择或明确来源，新增standard_part输出购买件及BOM；只预留光孔不等于已确定自攻、攻牙或嵌件。'
    'SLA树脂不得默认使用热熔嵌件或小尺寸打印螺纹；M2可用机械螺钉配防转螺母并明确装入/防脱方案，不能声称嘉立创支持M2镶嵌。'
    '滑入/夹持装配须声明装入顺序和防脱限位；engineering_plan.retentions用指定方向及位移检查真实阻挡件，不能只凭静态无干涉认定零件不会脱出。该有限位姿检查不代替连续运动、转动或承载验证。'
    '报告分别列CAD、已声明工程检查和未覆盖项；关键局部探针不是全表面壁厚扫描，不把公差、收缩及喷涂影响视为已验证。'
    '打印壁厚与零件整体最小尺寸分别核对；读取工程报告buildSizes，不能用壁厚通过代替供应商实际材料/尺寸报价资格。'
    '线端用engineering_plan.wire_endpoints声明。未知端子保留pending_measurement和原因，不阻塞独立结构验收；declared必须给wire_part、terminal_part及装配坐标point，原生检查最终实体接触，失败阻止采纳。参考件不得声明真实连接。接触不证明针号、导电、绝缘、压接或抗拉，报告不授予完整线束制造放行。'
    '壁厚失败先读取failureCode；probe-endpoint-inside或multiple-intervals需复核探针和最终几何，不直接判定断裂或只改阈值。'
)


def registry_root():
    frozen=Path(__file__).parent/'profiles'
    return frozen if frozen.is_dir() else Path(__file__).resolve().parents[3]/'standards/mechanical'


def registry(root=None):
    profiles={};hardware={}
    for path in sorted((root or registry_root()).glob('*.json')):
        raw=path.read_bytes();data=json.loads(raw)
        if data['kind']=='process-profile':
            if data['id'] in profiles:raise ValueError('Duplicate process profile id: '+data['id'])
            profiles[data['id']]={**data,'sha256':hashlib.sha256(raw).hexdigest()}
        elif data['kind']=='hardware-catalog':
            if set(hardware)&set(data['parts']):raise ValueError('Duplicate standard part id')
            hardware.update({key:{**row,'catalogSha256':hashlib.sha256(raw).hexdigest()} for key,row in data['parts'].items()})
    return dict(defaultProfile=DEFAULT_PROFILE,profiles=profiles,hardware=hardware)


class Record(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class WallProbe(Record):
    id: str
    part: str
    start: tuple[float,float,float]
    end: tuple[float,float,float]
    category: Literal['wall','feature']='wall'
    reason: str=Field(min_length=1)


class Fit(Record):
    id: str
    part_a: str
    part_b: str
    kind: Literal['static','moving','clamp','adhesive']
    region_min: tuple[float,float,float] | None=None
    region_max: tuple[float,float,float] | None=None
    reason: str=Field(min_length=1)
    tolerance_budget_mm: float | None=Field(default=None,ge=0,description='该配合尺寸链明确分配的最坏间隙损失；不能把经验装配间隙当成尺寸公差。')
    tolerance_basis: str=''
    target_gap_mm: float | None=Field(default=None,ge=0)

    @model_validator(mode='after')
    def region(self):
        if (self.region_min is None)!=(self.region_max is None):raise ValueError('Fit crop requires both region_min and region_max')
        if self.region_min is not None and any(b<=a for a,b in zip(self.region_min,self.region_max)):raise ValueError('Fit crop must have positive spans')
        if self.tolerance_budget_mm is not None and not self.tolerance_basis.strip():raise ValueError('Declared tolerance budget requires its source/basis')
        if self.kind=='adhesive' and self.target_gap_mm is None:raise ValueError('Adhesive fit requires its intended bondline thickness')
        return self


class Joint(Record):
    id: str
    method: Literal['captive_nut','set_screw_nut','self_tapping','insert','tapped','unresolved']
    screw_id: str
    screw_part: str
    receiver_part: str | None=None
    receiver_id: str | None=None
    members: list[str]=Field(min_length=1)
    bearing: tuple[float,float,float]
    axis: tuple[float,float,float]=(0,0,1)
    receiver_start_mm: float=Field(gt=0)
    engagement_mm: float=Field(gt=0)
    path_clearance_mm: float=Field(default=.2,ge=0)
    tool_radius_mm: float=Field(default=1.5,gt=0)
    tool_travel_mm: float=Field(default=10,gt=0)
    tool_removed_parts: list[str]=Field(default_factory=list,description='Explicit non-member parts removed before tool access; all final collision, shaft and tip checks still include them.')
    tool_access_reason: str=''
    receiver_entry_offset: tuple[float,float,float] | None=None
    receiver_host_part: str | None=Field(default=None,description='螺母承压/防转所在的真实成员件；用于最终实体核对及相对爆炸方向复核，不凭名称推断。')
    receiver_clock_degrees: float=0
    pressure_part: str | None=None
    pressure_point: tuple[float,float,float] | None=None
    capture_displacement_mm: float=Field(default=1,gt=0,le=10,description='Finite headless hardware-group escape samples in six world directions; not continuous escape or load strength certification.')
    assembly: str=Field(min_length=1)

    @model_validator(mode='after')
    def direction(self):
        if math.hypot(*self.axis)<1e-9:raise ValueError('Joint axis must be nonzero')
        if self.method=='set_screw_nut':
            if not all([self.receiver_id,self.receiver_part,self.receiver_host_part,self.pressure_part]) or self.pressure_point is None or self.receiver_entry_offset is None:
                raise ValueError('Set screw requires a nut/host, entry offset, physical pressure part and contact point')
        elif self.pressure_part is not None or self.pressure_point is not None or self.receiver_clock_degrees!=0:
            raise ValueError('Pressure contact and nut clocking are currently supported only for set_screw_nut')
        return self


class RetentionStop(Record):
    id: str
    direction: tuple[float,float,float]
    distance_mm: float=Field(gt=0,le=100)

    @model_validator(mode='after')
    def nonzero(self):
        if math.hypot(*self.direction)<1e-9:raise ValueError('Retention direction must be nonzero')
        return self


class Retention(Record):
    id: str
    part: str
    keepers: list[str]=Field(min_length=1)
    stops: list[RetentionStop]=Field(min_length=1)
    assembly: str=Field(min_length=1)


class WireEndpoint(Record):
    id: str
    wire_part: str
    terminal_part: str | None = None
    point: tuple[float,float,float] | None = None
    state: Literal['declared','pending_measurement']='pending_measurement'
    reason: str=Field(min_length=1)
    tolerance_mm: float=Field(default=.01,ge=0,le=.1)

    @model_validator(mode='after')
    def declared_target(self):
        if self.state=='declared' and (self.terminal_part is None or self.point is None):
            raise ValueError('Declared endpoint requires a terminal part and assembly-space contact point')
        return self


class EngineeringPlan(Record):
    profile_id: str
    profile_sha256: str=Field(pattern=r'^[a-f0-9]{64}$',description='从mechanical_schema制造规划取得的标准版本摘要，拒绝把旧标准静默换成新标准。')
    process: str
    material: str
    finish: Literal['unpainted','painted','other']
    selection_reason: str=Field(min_length=1)
    fabricated_parts: list[str]=Field(min_length=1,validation_alias=AliasChoices('fabricated_parts','printed_parts'),description='实际制作件；不将字段限定为3D打印，后续CNC等沿用此角色。')
    purchased_parts: list[str]=Field(default_factory=list)
    reference_parts: list[str]=Field(default_factory=list)
    wall_probes: list[WallProbe]=Field(default_factory=list)
    fits: list[Fit]=Field(default_factory=list)
    joints: list[Joint]=Field(default_factory=list)
    retentions: list[Retention]=Field(default_factory=list)
    wire_endpoints: list[WireEndpoint]=Field(default_factory=list)
    reviews: list[str]=Field(default_factory=list)

    def validate_parts(self,names):
        known=set(names);all_parts=self.fabricated_parts+self.purchased_parts+self.reference_parts
        if len({p.id for p in self.wire_endpoints})!=len(self.wire_endpoints):raise ValueError('Duplicate wire endpoint id')
        for p in self.wire_endpoints:
            if p.wire_part not in known or (p.terminal_part is not None and p.terminal_part not in known):raise ValueError('Wire endpoint references unknown part')
            if p.wire_part==p.terminal_part:raise ValueError('Wire and terminal must be distinct parts')
            if p.state=='declared' and (p.wire_part in self.reference_parts or p.terminal_part in self.reference_parts):raise ValueError('Reference geometry cannot claim a connected endpoint')
        if len(all_parts)!=len(set(all_parts)) or set(all_parts)!=known:raise ValueError('Engineering plan must classify every output exactly once')
        for row in self.wall_probes:
            if row.part not in self.fabricated_parts:raise ValueError('Wall probe must reference a fabricated part')
        for row in self.fits:
            if row.part_a not in known or row.part_b not in known or row.part_a==row.part_b:raise ValueError('Fit references unknown or identical parts')
        for row in self.joints:
            if row.screw_part not in self.purchased_parts or any(n not in known or n in self.reference_parts for n in row.members):raise ValueError('Joint requires a purchased screw and physical members')
            if len(row.members)!=len(set(row.members)):raise ValueError('Joint members must be unique')
            if row.receiver_part and row.receiver_part not in self.purchased_parts:raise ValueError('Joint receiver must be a purchased part')
            if row.receiver_host_part and (row.receiver_host_part not in row.members or not row.receiver_part or row.method not in {'captive_nut','set_screw_nut'}):
                raise ValueError('Receiver host must be an explicit physical joint member with a captive nut')
            if row.method=='set_screw_nut' and (row.pressure_part not in row.members or row.pressure_part in {row.screw_part,row.receiver_part,row.receiver_host_part}):
                raise ValueError('Pressure part must be a distinct physical joint member')
            if row.tool_removed_parts:
                if not row.tool_access_reason.strip():raise ValueError('Staged tool access requires its assembly reason')
                if len(set(row.tool_removed_parts))!=len(row.tool_removed_parts) or any(n not in known or n in row.members or n in {row.screw_part,row.receiver_part} or n in self.reference_parts for n in row.tool_removed_parts):
                    raise ValueError('Tool removal must name distinct physical non-member parts')
        for row in self.retentions:
            if row.part not in known or row.part in self.reference_parts:raise ValueError('Retention requires a physical retained part')
            if len(set(row.keepers))!=len(row.keepers) or any(n not in known or n==row.part or n in self.reference_parts for n in row.keepers):raise ValueError('Retention keepers must be distinct physical parts')
            if len({s.id for s in row.stops})!=len(row.stops):raise ValueError('Retention stop ids must be unique')


def standard_shape(part_id):
    import cadquery as cq
    row=registry()['hardware'].get(part_id)
    if row is None:raise ValueError('Unknown source-backed standard part: '+part_id)
    if row['kind']=='screw':
        if row.get('headless'):
            length, chamfer = row['length'], row['pointChamferLength']
            if not 0<chamfer<length or not 0<row['flatPointDiameter']<=row['diameter'] or not 0<row['driveDepth']<length-chamfer:
                raise ValueError('Invalid flat-point set screw catalog geometry: '+part_id)
            body=cq.Solid.makeCylinder(row['diameter']/2,length-chamfer).fuse(cq.Solid.makeCone(row['diameter']/2,row['flatPointDiameter']/2,chamfer,cq.Vector(0,0,length-chamfer)))
            socket=cq.Workplane('XY').polygon(6,2*row['driveSize']/math.sqrt(3)).extrude(row['driveDepth']+.001).val().translate((0,0,-.001))
            return body.cut(socket)
        # Under-head datum Z=0; +Z is insertion direction. No fake printed thread.
        body=cq.Solid.makeCylinder(row['diameter']/2,row['length']).fuse(cq.Solid.makeCylinder(row['headDiameter']/2,row['headHeight'],cq.Vector(0,0,-row['headHeight'])))
        if 'shoulderDiameter' in row or 'shoulderLength' in row:
            diameter,length=row.get('shoulderDiameter',0),row.get('shoulderLength',0)
            if not diameter>row['diameter'] or not 0<length<row['length']:
                raise ValueError('Shoulder screw requires a larger shoulder and a separate threaded length: '+part_id)
            body=body.fuse(cq.Solid.makeCylinder(diameter/2,length))
        socket=cq.Workplane('XY').polygon(6,2*row['driveSize']/math.sqrt(3)).extrude(row['driveDepth']+.001).val().translate((0,0,-row['headHeight']-.001))
        return body.cut(socket)
    radius=row['acrossFlats']/math.sqrt(3)
    return cq.Workplane('XY').polygon(6,2*radius).extrude(row['height']).val().cut(cq.Solid.makeCylinder(row['diameter']/2,row['height']))


def merge_wall_spans(spans, tolerance=1e-6):
    merged=[]
    for low,high in sorted(sorted(span) for span in spans):
        if merged and low<=merged[-1][1]+tolerance:
            merged[-1][1]=max(merged[-1][1],high)
        else:merged.append([low,high])
    return merged


def inspect_plan(design,model):
    import cadquery as cq
    plan=getattr(design,'engineering_plan',None)
    if plan is None:return dict(status='unplanned',checksPassed=None,manufacturingApproved=False,scope='Concept CAD only; choose process/material/fixation before physical design.')
    plan.validate_parts(model.parts)
    catalog=registry();profile=catalog['profiles'].get(plan.profile_id)
    if profile is None:raise ValueError('Unsupported process profile: '+plan.profile_id)
    if plan.profile_sha256!=profile['sha256']:raise ValueError('Process profile hash changed; reread manufacturingPlanning')
    if plan.process!=profile['process'] or plan.material not in profile['materials']:raise ValueError('Process/material differs from the selected source-backed profile')
    shapes={n:(s.val() if hasattr(s,'val') else s) for n,s in model.parts.items()};walls=[];fits=[];joints=[];reviews=list(plan.reviews);build_sizes=[]
    from mechanical_collision import intersection_volume
    from product_ide_3d.assembly_cad import bounds_overlap
    # Fixed output geometry belongs only to this inspection. Distant parts
    # need no Common; an unfused PCBA requires exact leaf-intersection unions.
    geometry=dict(getattr(model,'_collision_geometry',{}))
    def part_geometry(name):
        if name not in geometry:
            shape=shapes[name];box=shape.BoundingBox();solids=shape.Solids()
            geometry[name]=(box,[(s,box if len(solids)==1 else s.BoundingBox()) for s in solids])
        return geometry[name]
    def volumes(tool,names):
        box=tool.BoundingBox();leaves=None;result={}
        for name in names:
            bounds,parts=part_geometry(name)
            if not bounds_overlap(box,bounds):result[name]=0.;continue
            if leaves is None:leaves=[(leaf,leaf.BoundingBox()) for leaf in tool.Solids()]
            result[name]=intersection_volume(tool,shapes[name],first_leaves=leaves,second_leaves=parts)
        return result
    minimum_side=profile.get('minimumPartBoundingSideMm')
    if minimum_side is not None:
        for name in plan.fabricated_parts:
            box=part_geometry(name)[0];sides=[box.xlen,box.ylen,box.zlen]
            build_sizes.append(dict(part=name,boundingSidesMm=sides,minimumSideMm=minimum_side,passed=min(sides)+1e-5>=minimum_side,
                scope='Necessary minimum AABB side only; not full material build-size qualification, optimized orientation or supplier acceptance.'))
    if plan.finish!='unpainted':reviews.append('Surface finish allowance requires separate dimensional review')
    for probe in plan.wall_probes:
        shape=shapes[probe.part];box=part_geometry(probe.part)[0];span=max(box.xlen,box.ylen,box.zlen)
        minimum=profile['featureWallMin'] if probe.category=='feature' else next((v for size,v in profile['wallByMaxPartSpan'] if span<=size+1e-5),None)
        if minimum is None:reviews.append('Wall guideline does not cover part size: '+probe.part)
        a,b=cq.Vector(*probe.start),cq.Vector(*probe.end)
        if (b-a).Length<1e-8:raise ValueError('Wall probe needs a nonzero segment')
        axis=(b-a).normalized()
        spans=[[(e.startPoint()-a).dot(axis),(e.endPoint()-a).dot(axis)] for e in shape.intersect(cq.Edge.makeLine(a,b)).Edges() if e.Length()>1e-6]
        intervals=[high-low for low,high in merge_wall_spans(spans)]
        inside=any(s.isInside(p,1e-6) for s in shape.Solids() for p in [a,b])
        measured=min(intervals) if intervals else None
        passed=not inside and len(intervals)==1 and minimum is not None and measured+1e-5>=minimum
        failure=('probe-endpoint-inside' if inside else 'no-intersection' if not intervals else 'multiple-intervals' if len(intervals)!=1 else 'profile-span-uncovered' if minimum is None else 'below-minimum' if not passed else None)
        walls.append(dict(id=probe.id,part=probe.part,measuredMm=measured,minimumMm=minimum,intervalsMm=intervals,endpointsOutside=not inside,passed=passed,failureCode=failure))
    missing=sorted(set(plan.fabricated_parts)-{p.part for p in plan.wall_probes})
    reviews.extend('Missing critical wall probe: '+n for n in missing)
    for fit in plan.fits:
        first,second=shapes[fit.part_a],shapes[fit.part_b]
        if fit.region_min is not None:
            tool=cq.Solid.makeBox(*(b-a for a,b in zip(fit.region_min,fit.region_max)),cq.Vector(*fit.region_min))
            first,second=first.intersect(tool),second.intersect(tool)
        valid=bool(first.Solids() and second.Solids())
        gap=first.distance(second) if valid else None
        overlap=first.intersect(second).Volume() if valid else None
        required=profile['movingGapMin'] if fit.kind=='moving' else profile['staticGapMin'] if fit.kind=='static' else 0
        worst=gap-fit.tolerance_budget_mm if valid and fit.tolerance_budget_mm is not None else None
        mating=valid and (fit.kind!='clamp' or gap<1e-4) and (fit.kind!='adhesive' or abs(gap-fit.target_gap_mm)<1e-4)
        fits.append(dict(id=fit.id,kind=fit.kind,measuredMm=gap,minimumMm=required,targetGapMm=fit.target_gap_mm,overlapMm3=overlap,toleranceBudgetMm=fit.tolerance_budget_mm,toleranceBasis=fit.tolerance_basis,worstCaseRemainingMm=worst,passed=mating and overlap<1e-5 and gap+1e-5>=required and (worst is None or worst>1e-5)))
    if any(f.kind in {'static','moving'} and f.tolerance_budget_mm is None for f in plan.fits):reviews.append('配合仅核对工艺名义间隙下限，尚未分配具体尺寸链公差；需配合试片或样机测量，不能保证所有尺寸误差下均可装配。')
    for joint in plan.joints:
        if joint.method=='set_screw_nut':
            from mechanical_set_screws import inspect_set_screw
            joints.append(inspect_set_screw(joint,shapes,catalog,profile,plan.reference_parts))
            continue
        screw=catalog['hardware'].get(joint.screw_id);receiver=catalog['hardware'].get(joint.receiver_id) if joint.receiver_id else None
        if not screw or screw['kind']!='screw':raise ValueError('Unknown screw: '+joint.screw_id)
        if screw.get('headless'):raise ValueError('Headless screws require set_screw_nut pressure-contact inspection')
        origin=cq.Vector(*joint.bearing);axis=cq.Vector(*joint.axis).normalized()
        def placed(part_id,point):
            shape=standard_shape(part_id);cross=cq.Vector(0,0,1).cross(axis)
            if cross.Length>1e-8:shape=shape.rotate((0,0,0),cross.toTuple(),math.degrees(math.acos(max(-1.,min(1.,axis.z)))))
            elif axis.z<0:shape=shape.rotate((0,0,0),(1,0,0),180)
            return shape.translate(point)
        def same_pose(actual,expected):
            a,b=actual.BoundingBox(),expected.BoundingBox()
            return abs(actual.Volume()-expected.Volume())<max(.001,expected.Volume()*1e-6) and (actual.Center()-expected.Center()).Length<.001 and all(abs(getattr(a,k)-getattr(b,k))<.001 for k in ['xmin','ymin','zmin','xmax','ymax','zmax'])
        pose=same_pose(shapes[joint.screw_part],placed(joint.screw_id,origin))
        if joint.receiver_part:pose=pose and same_pose(shapes[joint.receiver_part],placed(joint.receiver_id,origin+axis*joint.receiver_start_mm))
        length=joint.receiver_start_mm;radius=screw['diameter']/2+joint.path_clearance_mm
        path=cq.Solid.makeCylinder(radius,length-1e-4,origin+axis*1e-4,axis)
        obstacles={n:s for n,s in shapes.items() if n not in {joint.screw_part,joint.receiver_part,*plan.reference_parts}}
        blockers=[dict(part=n,volumeMm3=v) for n,v in volumes(path,obstacles).items()]
        # Drill/pocket must remain open after ALL booleans, including the floor.
        clear=all(r['volumeMm3']<1e-5 for r in blockers)
        # A shoulder screw clamps at its shoulder end while its head retains
        # the moving ear with axial play. Testing the floating head as a
        # bearing would reject a correct pivot or encourage clamping the ear.
        thread_start=screw.get('shoulderLength',0)
        seat_origin=origin+axis*thread_start
        seat_radius=screw.get('shoulderDiameter',screw['headDiameter'])/2
        seat=cq.Solid.makeCylinder(seat_radius,.05,seat_origin,axis).cut(cq.Solid.makeCylinder(radius,.05,seat_origin,axis))
        head_fraction=volumes(seat,[joint.members[0]])[joint.members[0]]/seat.Volume() if seat.Volume()>1e-5 else 0
        head_supported=head_fraction>=profile.get('headBearingWorkingFractionMin',.5)
        tool=cq.Solid.makeCylinder(joint.tool_radius_mm,joint.tool_travel_mm,origin-axis*screw['headHeight'],-axis)
        tool_blockers=[dict(part=n,volumeMm3=v) for n,v in volumes(tool,[n for n in obstacles if n not in joint.tool_removed_parts]).items()]
        engagement=max(0,min(screw['length']-max(length,thread_start),receiver.get('height',0) if receiver else joint.engagement_mm))
        matching=bool(receiver and receiver['thread']==screw['thread'] and receiver['pitch']==screw['pitch'] and length+1e-5>=thread_start) if joint.method=='captive_nut' else False
        intrusion=sum(volumes(shapes[joint.receiver_part],joint.members).values()) if joint.receiver_part else 0
        receiver_support=0;anti_rotation=False
        receiver_access=False;access_collision=0
        if joint.receiver_part:
            nut=shapes[joint.receiver_part];probe=nut.translate(-axis*.05)
            receiving_members=[joint.receiver_host_part] if joint.receiver_host_part else joint.members
            receiver_support=sum(volumes(probe,receiving_members).values())
            turned=nut.rotate(origin.toTuple(),(origin+axis).toTuple(),30)
            anti_rotation=any(v>1e-5 for v in volumes(turned,receiving_members).values())
            if joint.receiver_entry_offset is not None:
                from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
                from OCP.gp import gp_Vec
                offset=cq.Vector(*joint.receiver_entry_offset)
                if offset.Length<1e-6:raise ValueError('Nut entry offset must be nonzero')
                # Convex outer hex prism + leading face extrusions is a
                # continuous sweep; a few animation poses cannot replace it.
                outer=cq.Workplane('XY').polygon(6,2*receiver['acrossFlats']/math.sqrt(3)).extrude(receiver['height']).val()
                cross=cq.Vector(0,0,1).cross(axis)
                if cross.Length>1e-8:outer=outer.rotate((0,0,0),cross.toTuple(),math.degrees(math.acos(max(-1.,min(1.,axis.z)))))
                elif axis.z<0:outer=outer.rotate((0,0,0),(1,0,0),180)
                outer=outer.translate(origin+axis*length);sweeps=[]
                for face in outer.Faces():
                    if face.normalAt().dot(offset)>1e-8:sweeps.append(cq.Shape.cast(BRepPrimAPI_MakePrism(face.wrapped,gp_Vec(*offset.toTuple())).Shape()))
                swept=outer.fuse(*sweeps)
                access_collision=sum(volumes(swept,joint.members).values())
                receiver_access=access_collision<1e-5
        tail_start=joint.receiver_start_mm+(receiver['height'] if receiver else joint.engagement_mm)+1e-4
        tail_intrusion=0
        if screw['length']+.3>tail_start:
            tail=cq.Solid.makeCylinder(screw['diameter']/2,screw['length']+.3-tail_start,origin+axis*tail_start,axis)
            tail_intrusion=sum(volumes(tail,obstacles).values())
        if joint.method!='captive_nut':reviews.append('Joint method needs specific pilot/thread/insert evidence: '+joint.id)
        passed=pose and head_supported and clear and matching and receiver_access and receiver_support>1e-5 and anti_rotation and engagement+1e-5>=joint.engagement_mm and intrusion<1e-5 and tail_intrusion<1e-5 and all(r['volumeMm3']<1e-5 for r in tool_blockers)
        joints.append(dict(id=joint.id,method=joint.method,receiverHostPart=joint.receiver_host_part,hardwarePoseMatched=pose,headBearingPassed=head_supported,headBearingWorkingFraction=head_fraction,bearingReference='shoulder-end' if thread_start else 'under-head',bearingOffsetMm=thread_start,receiverBearingMm3=receiver_support,receiverAntiRotationBlocked=anti_rotation,receiverEntryCollisionMm3=access_collision,receiverEntryPassed=receiver_access,tipKeepoutCollisionMm3=tail_intrusion,pathBlockers=blockers,toolBlockers=[r for r in tool_blockers if r['volumeMm3']>1e-5],toolRemovedParts=joint.tool_removed_parts,toolAccessReason=joint.tool_access_reason,engagementMm=engagement,requiredEngagementMm=joint.engagement_mm,receiverCollisionMm3=intrusion,matchingThread=matching,assembly=joint.assembly,passed=passed))
    from mechanical_collision import intersection_volume,blocking_witness
    # These exact results already describe the same immutable nominal parts.
    nominal_pairs={frozenset((p['partA'],p['partB'])):p['intersectionMm3']
                   for p in getattr(model,'measures',{}).get('pairwise_interference',[])
                   if p.get('intersectionEvaluation')!='nonphysical-reference'}
    retentions=[]
    for retention in plan.retentions:
        shape=shapes[retention.part]
        initial=sum(nominal_pairs[frozenset((retention.part,n))]
                    if frozenset((retention.part,n)) in nominal_pairs
                    else intersection_volume(shape,shapes[n],first_leaves=part_geometry(retention.part)[1],second_leaves=part_geometry(n)[1]) for n in retention.keepers)
        stops=[]
        for stop in retention.stops:
            offset=cq.Vector(*stop.direction).normalized()*stop.distance_mm
            moved=shape.translate(offset.toTuple())
            moved_leaves=[(s,s.BoundingBox()) for s in moved.Solids()]
            # A stop needs one actual blocker, not an exhaustive blocker list.
            # Small keepers (e.g. mounting screws) usually give the cheapest proof.
            def keeper_size(name):
                b=part_geometry(name)[0];return b.xlen*b.ylen*b.zlen
            collisions=[]
            for n in sorted(retention.keepers,key=keeper_size):
                collision=dict(part=n,**blocking_witness(moved,shapes[n],first_leaves=moved_leaves,second_leaves=part_geometry(n)[1]));collisions.append(collision)
                if collision['volumeMm3']>1e-5:break
            blockers=[c for c in collisions if c['volumeMm3']>1e-5]
            stops.append(dict(id=stop.id,offsetMm=list(offset.toTuple()),blockers=blockers,passed=bool(blockers),evaluation='First exact blocking witness; not an exhaustive blocker inventory.'))
        retentions.append(dict(id=retention.id,part=retention.part,nominalOverlapMm3=initial,stops=stops,assembly=retention.assembly,
            passed=initial<1e-5 and all(s['passed'] for s in stops),scope='Declared displaced poses only; not continuous or rotational escape, load or tolerance proof.'))
    endpoints=[]
    for endpoint in plan.wire_endpoints:
        if endpoint.state=='pending_measurement':
            endpoints.append(dict(id=endpoint.id,status='pending_measurement',passed=None,reason=endpoint.reason))
            reviews.append('Wire endpoint pending measurement: '+endpoint.id)
            continue
        vertex=cq.Vertex.makeVertex(*endpoint.point)
        wire=shapes[endpoint.wire_part];terminal=shapes[endpoint.terminal_part]
        wd=wire.distance(vertex);td=terminal.distance(vertex);gap=wire.distance(terminal)
        contact=max(wd,td,gap)<=endpoint.tolerance_mm
        endpoints.append(dict(id=endpoint.id,status='geometric-contact' if contact else 'disconnected',passed=contact,
            wirePart=endpoint.wire_part,terminalPart=endpoint.terminal_part,pointMm=endpoint.point,
            pointToWireMm=wd,pointToTerminalMm=td,partGapMm=gap,toleranceMm=endpoint.tolerance_mm,
            electricalContinuityVerified=False,scope='Declared point on both final solids only; not pin identity, conductivity, insulation, crimp or strain-relief proof.'))
    passed=all(r['passed'] for r in walls+fits+joints+retentions+build_sizes) and all(r['passed'] is not False for r in endpoints)
    tolerance_checked=bool(plan.fits) and all(f.kind in {'clamp','adhesive'} or f.tolerance_budget_mm is not None for f in plan.fits)
    return dict(status='failed' if not passed else 'needs-review' if reviews else 'checked-critical-regions',checksPassed=passed,profile=profile,process=plan.process,material=plan.material,finish=plan.finish,walls=walls,fits=fits,joints=joints,retentions=retentions,wireEndpoints=endpoints,buildSizes=build_sizes,reviews=reviews,missingWallParts=missing,fullWallScan=False,toleranceStackVerified=tolerance_checked,manufacturingApproved=False,
        scope='Native declared critical sections, selected interfaces, cylindrical screw/tool envelopes and declared displaced retention poses. Finite coverage, nominal dimensions, simplified purchased hardware; not complete DFM, thread strength or supplier release.')
