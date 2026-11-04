"""Composable, data-only solid modelling with editable parameter expressions.

Runs in the existing isolated CadQuery worker. Engineering qualification is
optional; valid solids and faithful STEP exports are the default build checks.
"""
import ast
import itertools
import math
import operator
from types import SimpleNamespace
from typing import Annotated, Literal

from pydantic import Field, model_validator
from product_ide_3d.spec import Record
from mechanical_explosion import ExplosionPlan
from mechanical_engineering import EngineeringPlan
from mechanical_presentation import apply_screw_style
from mechanical_appearance import AppearancePlan
from mechanical_integrity import SolidIntent, apply_integrity

Number = float | str
Vec2 = tuple[Number, Number]
Vec3 = tuple[Number, Number, Number]
Name = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')]


class Frame(Record):
    plane: Literal['XY', 'XZ', 'YZ', 'YX', 'ZX', 'ZY'] = 'XY'
    origin: Vec3 = (0, 0, 0)
    rotation: Vec3 = (0, 0, 0)


class Segment(Record):
    kind: Literal['line', 'arc', 'spline'] = 'line'
    to: Vec2
    through: Vec2 | None = None
    points: list[Vec2] = Field(default_factory=list)


class Profile(Record):
    kind: Literal['rectangle', 'circle', 'ellipse', 'polygon', 'spline', 'sketch']
    size: Vec2 | None = None
    radius: Number | None = None
    points: list[Vec2] = Field(default_factory=list)
    start: Vec2 = (0, 0)
    segments: list[Segment] = Field(default_factory=list)


class Feature(Record):
    id: Name


class Box(Feature):
    op: Literal['box']
    size: Vec3
    centered: bool = True


class Cylinder(Feature):
    op: Literal['cylinder']
    radius: Number
    height: Number


class Cone(Feature):
    op: Literal['cone']
    radius_bottom: Number
    radius_top: Number
    height: Number


class Sphere(Feature):
    op: Literal['sphere']
    radius: Number


class Extrude(Feature, Frame):
    op: Literal['extrude']
    profile: Profile
    holes: list[Profile] = Field(default_factory=list)
    distance: Number
    taper: Number = 0


class Revolve(Feature, Frame):
    op: Literal['revolve']
    profile: Profile
    angle: Number = 360
    axis_start: Vec2 = (0, 0)
    axis_end: Vec2 = (0, 1)


class Section(Frame):
    profile: Profile


class Loft(Feature):
    op: Literal['loft']
    sections: list[Section] = Field(min_length=2)
    ruled: bool = False


class Sweep(Feature, Frame):
    op: Literal['sweep']
    profile: Profile
    path: list[Vec3] = Field(min_length=2)
    smooth: bool = False
    frenet: bool = False


class Boolean(Feature):
    op: Literal['union', 'cut', 'intersect']
    inputs: list[Name] = Field(min_length=2)
    refine: bool = Field(default=False, description='Opt-in same-domain face/edge unification for a single-solid Boolean result. Keeps material and solid count; do not apply to a complete PCBA assembly. Old designs default to false.')


class EdgeFinish(Feature):
    op: Literal['fillet', 'chamfer']
    source: Name
    size: Number
    edges: str = '|Z'


class Shell(Feature):
    op: Literal['shell']
    source: Name
    thickness: Number
    faces: str | None = '>Z'


class Transform(Feature):
    op: Literal['transform']
    source: Name
    translation: Vec3 = (0, 0, 0)
    rotation: Vec3 = (0, 0, 0)


class Mirror(Feature):
    op: Literal['mirror']
    source: Name
    plane: Literal['XY', 'XZ', 'YZ'] = 'YZ'
    origin: Vec3 = (0, 0, 0)


class Pattern(Feature):
    op: Literal['linear_pattern', 'polar_pattern']
    source: Name
    count: Number
    spacing: Vec3 = (0, 0, 0)
    angle: Number = 360
    axis_start: Vec3 = (0, 0, 0)
    axis_end: Vec3 = (0, 0, 1)


class Step(Feature):
    op: Literal['step']
    step: str = Field(description='冻结的工程内STEP路径。电路板与板上元器件使用完整assembly.step，作为一个PCBA特征和输出零件，不逐器件拆分。')
    step_sha256: str | None = None
    preserve_colors: bool = True


class StandardPart(Feature):
    op: Literal['standard_part']
    part_id: str = Field(description='mechanical_schema制造规划中的来源绑定标准件ID；购买件使用简化几何，不能作为打印螺纹。')


Operation = Annotated[
    Box | Cylinder | Cone | Sphere | Extrude | Revolve | Loft | Sweep |
    Boolean | EdgeFinish | Shell | Transform | Mirror | Pattern | Step | StandardPart,
    Field(discriminator='op'),
]


class OutputPart(SolidIntent):
    name: Name
    feature: Name
    color: tuple[float, float, float] = (.32, .55, .68)
    display_name: str | None = Field(default=None, max_length=120, description='零件显示名；螺钉按实际标准件尺寸生成M2.5_5MM等规格名，name仍为稳定实例ID。')


class FeatureDesign(Record):
    schema_version: Literal[1] = 1
    kind: Literal['feature_model'] = 'feature_model'
    name: Name = 'feature-model'
    parameters: dict[str, Number] = Field(default_factory=dict)
    parameter_labels: dict[str, str] = Field(default_factory=dict)
    screw_color: tuple[Annotated[float, Field(ge=0, le=1)], Annotated[float, Field(ge=0, le=1)], Annotated[float, Field(ge=0, le=1)]] | None = Field(default=None, description='全部已识别螺钉共用颜色。省略时沿用第一颗螺钉颜色，重建时统一应用，不能逐颗覆盖。')
    features: list[Operation] = Field(min_length=1)
    parts: list[OutputPart] = Field(default_factory=list)
    check_interference: bool = False
    evidence_files: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    explosion: ExplosionPlan | None = Field(default=None, description='查看用爆炸分组；同组零件保持相对位置。省略时按几何高度分层，不按输出顺序。')
    engineering_plan: EngineeringPlan | None = Field(default=None,description='准备制作实物时先选择工艺/材料/处理、零件角色、固定方式、配合与关键壁厚；未填写仅作为概念CAD。')
    appearance: AppearancePlan | None = Field(default=None, description='参考图引导可编辑CAD；PBR材质及贴花写入GLB，保持STEP/STL实体。图片引用冻结inputs路径及SHA。')

    @model_validator(mode='after')
    def references(self):
        seen = set()
        for feature in self.features:
            if feature.id in seen:
                raise ValueError('Duplicate feature id: ' + feature.id)
            refs = getattr(feature, 'inputs', []) + ([feature.source] if hasattr(feature, 'source') else [])
            for ref in refs:
                if ref not in seen:
                    raise ValueError(f'{feature.id}: unknown or forward feature reference {ref}')
            seen.add(feature.id)
        if not self.parts:
            self.parts = [OutputPart(name='body', feature=self.features[-1].id)]
        names = set()
        for part in self.parts:
            if part.feature not in seen:
                raise ValueError('Unknown output feature: ' + part.feature)
            if part.name in names or part.name in {'assembly', 'exploded', self.name}:
                raise ValueError('Duplicate or reserved part name: ' + part.name)
            if any(c < 0 or c > 1 for c in part.color):
                raise ValueError('Part colors must be between 0 and 1')
            names.add(part.name)
        if self.explosion is not None:self.explosion.validate_parts(names)
        if self.engineering_plan is not None:self.engineering_plan.validate_parts(names)
        from mechanical_engineering import registry
        apply_screw_style(self, registry()['hardware'])
        return self


class Numbers:
    """Arithmetic only: no Python eval, attributes, imports, or subscripts."""
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}
    functions = {n: getattr(math, n) for n in ('sqrt', 'sin', 'cos', 'tan', 'radians', 'degrees')}
    functions.update(abs=abs, min=min, max=max)

    def __init__(self, parameters):
        self.parameters = parameters
        self.resolved = {}
        self.active = set()
        for name in parameters:
            if not name.isidentifier() or name in self.functions or name in {'pi', 'e'}:
                raise ValueError('Invalid or reserved parameter name: ' + name)
            self.parameter(name)

    def parameter(self, name):
        if name in self.resolved:
            return self.resolved[name]
        if name not in self.parameters:
            raise ValueError('Unknown parameter: ' + name)
        if name in self.active:
            raise ValueError('Cyclic parameter: ' + name)
        self.active.add(name)
        try:
            value = self(self.parameters[name])
            self.resolved[name] = value
            return value
        finally:
            self.active.remove(name)

    def __call__(self, value):
        def visit(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                return float(node.value)
            if isinstance(node, ast.Name):
                return {'pi': math.pi, 'e': math.e}[node.id] if node.id in {'pi', 'e'} else self.parameter(node.id)
            if isinstance(node, ast.BinOp) and type(node.op) in self.binary:
                return self.binary[type(node.op)](visit(node.left), visit(node.right))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self.functions and not node.keywords:
                return self.functions[node.func.id](*(visit(a) for a in node.args))
            raise ValueError('Use parameter names, numbers and arithmetic expressions only')
        try:
            result = float(visit(ast.parse(value, mode='eval').body)) if isinstance(value, str) else float(value)
            if not math.isfinite(result):
                raise ValueError('Number must be finite')
            return result
        except (ArithmeticError, SyntaxError, TypeError) as error:
            raise ValueError(f'Invalid numeric expression {value!r}: {error}') from error

    def vector(self, value):
        return tuple(self(v) for v in value)

    def positive(self, value):
        result = self(value)
        if result <= 0:
            raise ValueError('Dimension must be positive')
        return result


def _plane(frame, numbers):
    import cadquery as cq
    return cq.Workplane(frame.plane, origin=numbers.vector(frame.origin)).transformed(rotate=numbers.vector(frame.rotation))


def _profile(wp, profile, numbers):
    if profile.kind == 'rectangle':
        if profile.size is None:
            raise ValueError('Rectangle requires size [width, height]')
        return wp.rect(*(numbers.positive(v) for v in profile.size))
    if profile.kind == 'circle':
        if profile.radius is None:
            raise ValueError('Circle requires radius')
        return wp.circle(numbers.positive(profile.radius))
    if profile.kind == 'ellipse':
        if profile.size is None:
            raise ValueError('Ellipse requires size [width, height]')
        return wp.ellipse(*(numbers.positive(v)/2 for v in profile.size))
    points = [numbers.vector(p) for p in profile.points]
    if profile.kind == 'polygon':
        if len(points) < 3:
            raise ValueError('Polygon requires at least three points')
        return wp.polyline(points).close()
    if profile.kind == 'spline':
        if len(points) < 3:
            raise ValueError('Closed spline requires at least three points')
        return wp.spline(points, periodic=True).close()
    wp = wp.moveTo(*numbers.vector(profile.start))
    if not profile.segments:
        raise ValueError('Sketch requires segments')
    for segment in profile.segments:
        end = numbers.vector(segment.to)
        if segment.kind == 'line':
            wp = wp.lineTo(*end)
        elif segment.kind == 'arc':
            if segment.through is None:
                raise ValueError('Arc requires a through point')
            wp = wp.threePointArc(numbers.vector(segment.through), end)
        else:
            wp = wp.spline([*(numbers.vector(p) for p in segment.points), end], includeCurrent=True)
    return wp.close()


def _shape(wp):
    import cadquery as cq
    solids = wp.solids().vals()
    if not solids:
        raise ValueError('Operation produced no solid')
    return solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)


def _operation(feature, shapes, numbers, root):
    import cadquery as cq
    n = numbers
    op = feature.op
    wp = cq.Workplane('XY')
    if op == 'standard_part':
        from mechanical_engineering import standard_shape
        return standard_shape(feature.part_id)
    if op == 'box':
        return _shape(wp.box(*(n.positive(v) for v in feature.size), centered=feature.centered))
    if op == 'cylinder':
        return _shape(wp.circle(n.positive(feature.radius)).extrude(n.positive(feature.height)))
    if op == 'cone':
        bottom, top = n(feature.radius_bottom), n(feature.radius_top)
        if min(bottom, top) < 0 or max(bottom, top) <= 0:
            raise ValueError('Cone requires nonnegative radii, at least one positive')
        return cq.Solid.makeCone(bottom, top, n.positive(feature.height))
    if op == 'sphere':
        return _shape(wp.sphere(n.positive(feature.radius)))
    if op == 'extrude':
        distance = n(feature.distance)
        result = _profile(_plane(feature, n), feature.profile, n).extrude(distance, taper=n(feature.taper))
        for hole in feature.holes:
            result = result.cut(_profile(_plane(feature, n), hole, n).extrude(distance, taper=n(feature.taper)))
        return _shape(result)
    if op == 'revolve':
        return _shape(_profile(_plane(feature, n), feature.profile, n).revolve(n(feature.angle), n.vector(feature.axis_start), n.vector(feature.axis_end)))
    if op == 'loft':
        wires = [_profile(_plane(section, n), section.profile, n).val() for section in feature.sections]
        return cq.Solid.makeLoft(wires, ruled=feature.ruled)
    if op == 'sweep':
        points = [cq.Vector(*n.vector(p)) for p in feature.path]
        path = cq.Edge.makeSpline(points) if feature.smooth else cq.Wire.makePolygon(points)
        return _shape(_profile(_plane(feature, n), feature.profile, n).sweep(path, isFrenet=feature.frenet))
    if op in {'union', 'cut', 'intersect'}:
        result = shapes[feature.inputs[0]]
        if op == 'cut':
            # One native cut with all tools avoids repeatedly rebuilding the
            # same trimmed case for every overlapping clearance envelope.
            result = result.cut(*(shapes[ref] for ref in feature.inputs[1:]))
        else:
            for ref in feature.inputs[1:]:
                result = getattr(result, {'union': 'fuse', 'intersect': 'intersect'}[op])(shapes[ref])
        if feature.refine:
            if len(result.Solids()) != 1:
                raise ValueError('Boolean refine requires a single-solid result; assemblies are not refined')
            result = result.clean()
            if len(result.Solids()) != 1:
                raise ValueError('Boolean refine changed the solid count')
        return result
    if op in {'fillet', 'chamfer'}:
        edges = wp.newObject([shapes[feature.source]]).edges(feature.edges)
        if not edges.vals():
            raise ValueError('Edge selector matched no edges: ' + feature.edges)
        return _shape(getattr(edges, op)(n.positive(feature.size)))
    if op == 'shell':
        selected = wp.newObject([shapes[feature.source]])
        if feature.faces is not None:
            selected = selected.faces(feature.faces)
            if not selected.vals():
                raise ValueError('Face selector matched no faces: ' + feature.faces)
        return _shape(selected.shell(n(feature.thickness)))
    if op == 'transform':
        shape = shapes[feature.source]
        for axis, angle in zip([(1, 0, 0), (0, 1, 0), (0, 0, 1)], n.vector(feature.rotation)):
            if angle:
                shape = shape.rotate((0, 0, 0), axis, angle)
        return shape.translate(n.vector(feature.translation))
    if op == 'mirror':
        return _shape(wp.newObject([shapes[feature.source]]).mirror(feature.plane, n.vector(feature.origin)))
    if op in {'linear_pattern', 'polar_pattern'}:
        count = n.positive(feature.count)
        if not count.is_integer():
            raise ValueError('Pattern count must be a positive integer')
        source = shapes[feature.source]
        copies = []
        for i in range(int(count)):
            if op == 'linear_pattern':
                copies.append(source.translate(tuple(v*i for v in n.vector(feature.spacing))))
            else:
                copies.append(source.rotate(n.vector(feature.axis_start), n.vector(feature.axis_end), n(feature.angle)*i/count))
        return cq.Compound.makeCompound(copies)
    if op == 'step':
        return _shape(cq.importers.importStep(str(root/feature.step)))
    raise ValueError('Unsupported feature operation: ' + op)


def build(design, root):
    import cadquery as cq
    from time import perf_counter
    started=perf_counter();feature_times=[]
    numbers = Numbers(design.parameters)
    shapes = {}
    rows = []
    for feature in design.features:
        try:
            feature_started=perf_counter()
            shape = _operation(feature, shapes, numbers, root)
            if not shape.isValid():
                raise ValueError('Result is empty or not a valid solid')
            solids=shape.Solids()
            if not solids:raise ValueError('Result is empty or not a valid solid')
            volume=shape.Volume()
            if volume <= 0:raise ValueError('Result is empty or not a valid solid')
            shapes[feature.id] = shape
            rows.append(dict(id=feature.id, op=feature.op, solidCount=len(solids), volumeMm3=volume))
            feature_times.append(dict(id=feature.id,op=feature.op,seconds=perf_counter()-feature_started))
        except Exception as error:
            raise ValueError(f'Feature {feature.id} ({feature.op}): {error}') from error
    # Separate output instances must keep distinct OCCT identities, even when
    # two parts intentionally reference exactly the same feature and pose.
    feature_seconds=perf_counter()-started;stage=perf_counter()
    parts = {p.name: shapes[p.feature].copy() for p in design.parts}
    checks = dict(valid_feature_solids=True)
    pairs = []
    interference_checked=design.check_interference or design.engineering_plan is not None
    if interference_checked:
        from product_ide_3d.assembly_cad import bounds_overlap
        from mechanical_collision import intersection_volume
        boxes={name:shape.BoundingBox() for name,shape in parts.items()}
        # The same complete PCBA participates in many pairs. Computing all of
        # its native leaf bounds again for every pair dominated real builds.
        leaves={}
        for name,shape in parts.items():
            solids=shape.Solids()
            leaves[name]=[(solid,boxes[name] if len(solids)==1 else solid.BoundingBox()) for solid in solids]
        for (a, first), (b, second) in itertools.combinations(parts.items(), 2):
            if design.engineering_plan is not None and (a in design.engineering_plan.reference_parts or b in design.engineering_plan.reference_parts):
                pairs.append(dict(partA=a,partB=b,intersectionMm3=0,intersectionEvaluation='nonphysical-reference'));continue
            candidate=bounds_overlap(boxes[a],boxes[b])
            pairs.append(dict(partA=a,partB=b,intersectionMm3=intersection_volume(first,second,first_leaves=leaves[a],second_leaves=leaves[b]) if candidate else 0,intersectionEvaluation='exact' if candidate else 'bounds'))
        checks['assembly_no_interference'] = all(p['intersectionMm3'] < 1e-5 for p in pairs)
    # A whole imported assembly remains one output part. Preserve its source
    # face/node colors through rigid transforms instead of splitting devices.
    collision_seconds=perf_counter()-stage;stage=perf_counter()
    appearances={};features={f.id:f for f in design.features}
    for part in design.parts:
        feature=features[part.feature];transforms=[]
        while feature.op=='transform':transforms.append(feature);feature=features[feature.source]
        if feature.op!='step' or not feature.preserve_colors:continue
        from mechanical_pcb import colored_step
        native,_=colored_step(root/feature.step,shapes[feature.id])
        for transform in reversed(transforms):
            location=cq.Location(cq.Vector(*numbers.vector(transform.translation)))
            for axis,angle in reversed(list(zip([(1,0,0),(0,1,0),(0,0,1)],numbers.vector(transform.rotation)))):
                location=location*cq.Location(cq.Vector(0,0,0),cq.Vector(*axis),angle)
            wrapped=cq.Assembly(name='import_pose');wrapped.add(native,name='source',loc=location);native=wrapped
        appearances[part.name]=native
    model=SimpleNamespace(parts=parts, appearances=appearances, metadata={p.name: p for p in design.parts}, checks=checks,
                           warnings=[], measures=dict(parameters=numbers.resolved, features=rows,
                           interferenceChecked=interference_checked, pairwise_interference=pairs))
    if interference_checked:
        model._collision_geometry={name:(boxes[name],leaves[name]) for name in parts}
    appearance_seconds=perf_counter()-stage;stage=perf_counter()
    from mechanical_engineering import inspect_plan,registry
    engineering=inspect_plan(design,model);model.measures['engineering']=engineering
    engineering_seconds=perf_counter()-stage
    hardware=[]
    for part in design.parts:
        feature=features[part.feature]
        while feature.op=='transform':feature=features[feature.source]
        if feature.op=='standard_part':hardware.append(dict(part=part.name,partId=feature.part_id,**registry()['hardware'][feature.part_id]))
    model.measures['hardwareBom']=hardware
    from mechanical_explosion import review_joint_directions
    review=review_joint_directions(design);model.measures['explosionDirectionReview']=review
    model.warnings.extend('Explosion direction needs review: '+r['jointId'] for r in review['findings'])
    if design.engineering_plan is not None:
        model.checks['engineering_declared_geometry']=engineering['checksPassed']
        model.warnings.append('Engineering '+engineering['status']+': critical-region checks, not complete DFM or physical qualification.')
    apply_integrity(design,model)
    model.measures['performance']=dict(featureSeconds=feature_seconds,collisionSeconds=collision_seconds,
        sourceAppearanceSeconds=appearance_seconds,engineeringSeconds=engineering_seconds,buildSeconds=perf_counter()-started,
        slowestFeatures=sorted(feature_times,key=lambda row:row['seconds'],reverse=True)[:10])
    return model


def verify(design, root):
    from mechanical_assembly import verify as verify_assembly
    return verify_assembly(design, root, builder=build)
