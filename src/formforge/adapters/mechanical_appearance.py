"""Versioned visual styling on native CAD meshes; no change to STEP solids."""
import copy
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

Finite = Annotated[float, Field(allow_inf_nan=False)]
Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, le=10000, allow_inf_nan=False)]
Vector = tuple[Finite, Finite, Finite]
Name = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')]

POLICY = dict(version='appearance-v1', kinds=['feature_model', 'assembly'],
    generation='external-agent-reference-to-editable-cad', automaticImageToCad=False,
    imageFormats=['png', 'jpg', 'jpeg'], export='embedded GLB materials and projected decals',
    coordinateFrame='CAD assembly XYZ in mm; normal points outwards; up is image top',
    physicalGeometry=False, printArtwork=False,
    agentInstructions='Read appearance schema. Record visual intent and frozen reference images with source, SHA and generation prompt when applicable. The external Agent generates or reads reference images, compares proportions, silhouette, transitions, seams and interface access, then composes editable native CAD features. Preserve real dimensions and engineering constraints; never infer hidden structure or manufacturing approval from an image. Use materials for roughness/metalness and decals for surface graphics. Bind every decal to one output part and a frozen PNG/JPEG. Inspect grey geometry and textured front/side/back views; a successful projection is not an aesthetic review. GLB carries appearance; STEP/STL retain engineering geometry. No background model calls.')

AGENT_INSTRUCTIONS = '\n外观设计先读 mechanical_schema.appearancePolicy：外部 Agent 生成或读取参考图，保存 appearance.brief、references 的用途、图片 SHA 与适用的生成提示词，再用放样/扫掠/样条等可编辑特征实现比例、轮廓、过渡、接缝和接口。参考图不证明隐藏结构、精确尺寸或制造资格。appearance.materials 按输出零件设置 roughness/metalness；decals 绑定唯一 part 和冻结 PNG/JPEG、CAD 毫米中心、朝外 normal、图片上方向 up、宽高、投影深度及旋转。图片经 source_transfer 或声明来源导入，使用 inputs/<id>.<suffix> 与 image_sha256。检查灰模和贴花模型的正侧背面；投影通过不是外观评审。GLB内嵌贴花并随零件隐藏/爆炸，STEP/STL保持工程实体；当前只做视觉贴花，不输出实体贴纸制作资料，不自动调用收费模型或宣称自动一图转CAD。\n'


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ReferenceImage(Record):
    image: str
    image_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    purpose: str = Field(min_length=1, max_length=1000)
    generation_prompt: str = Field(default='', max_length=6000)


class SurfaceMaterial(Record):
    part: Name
    roughness: Unit = .55
    metalness: Unit = 0


class Decal(Record):
    id: Name
    part: Name
    image: str
    image_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    center_mm: Vector
    normal: Vector = (0, 0, 1)
    up: Vector = (0, 1, 0)
    width_mm: Positive
    height_mm: Positive
    depth_mm: Positive = 1
    rotation_deg: Finite = 0
    opacity: Unit = 1

    @model_validator(mode='after')
    def valid_frame(self):
        n, u = self.normal, self.up
        cross = [n[1]*u[2]-n[2]*u[1], n[2]*u[0]-n[0]*u[2], n[0]*u[1]-n[1]*u[0]]
        if sum(x*x for x in cross) < 1e-12:
            raise ValueError('Decal normal and up must be nonzero and nonparallel')
        return self


class AppearancePlan(Record):
    brief: str = Field(default='', max_length=6000)
    references: list[ReferenceImage] = Field(default_factory=list, max_length=16)
    materials: list[SurfaceMaterial] = Field(default_factory=list, max_length=128)
    decals: list[Decal] = Field(default_factory=list, max_length=64)

    @model_validator(mode='after')
    def unique(self):
        if len({r.part for r in self.materials}) != len(self.materials):
            raise ValueError('Only one material setting is allowed per part')
        if len({r.id for r in self.decals}) != len(self.decals):
            raise ValueError('Decal ids must be unique')
        return self


def image_info(root, row):
    from PIL import Image
    path = root / row.image
    if path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        raise ValueError('Appearance images must be frozen PNG or JPEG')
    if hashlib.sha256(path.read_bytes()).hexdigest() != row.image_sha256:
        raise ValueError('Appearance image SHA mismatch: ' + row.image)
    with Image.open(path) as image:
        if image.format not in {'PNG', 'JPEG'} or image.width*image.height > 16777216:
            raise ValueError('Appearance image must be PNG/JPEG, at most 16 megapixels')
        image.verify()
        return dict(width=image.width, height=image.height, mimeType='image/png' if image.format == 'PNG' else 'image/jpeg')


def write_glb(path, document, binary):
    document['buffers'] = [dict(byteLength=len(binary))]
    encoded = json.dumps(document, separators=(',', ':'), allow_nan=False).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary = bytes(binary) + b'\0' * (-len(binary) % 4)
    path.write_bytes(struct.pack('<III', 0x46546C67, 2, 28+len(encoded)+len(binary)) +
                     struct.pack('<II', len(encoded), 0x4E4F534A) + encoded +
                     struct.pack('<II', len(binary), 0x004E4942) + binary)


def clip_polygon(vertices, axis, bound, sign):
    """Clip packed [projector XYZ, CAD XYZ, CAD normal] vertices to one plane."""
    result = []
    if not vertices:
        return result
    previous = vertices[-1]
    pd = sign*previous[axis]-bound
    for current in vertices:
        cd = sign*current[axis]-bound
        if (pd <= 0) != (cd <= 0):
            result.append(previous + (current-previous)*(pd/(pd-cd)))
        if cd <= 0:
            result.append(current)
        previous, pd = current, cd
    return result


def style_glb(root, plan):
    import numpy as np
    from product_ide_3d.glb import read_glb, accessor, node_transform
    document, original = read_glb(root/'assembly.glb')
    binary = bytearray(original)
    root_index = document['scenes'][document.get('scene', 0)]['nodes'][0]
    transforms = {}

    def visit(index, parent):
        transform = parent if index == root_index else parent @ node_transform(document['nodes'][index])
        transforms[index] = transform
        for child in document['nodes'][index].get('children', []):
            visit(child, transform)
    visit(root_index, np.eye(4))

    def part_index(name):
        matches = [i for i in transforms if document['nodes'][i].get('name', '').split('/')[-1] == name]
        if len(matches) != 1:
            raise ValueError('Appearance target part is missing or ambiguous: ' + name)
        return matches[0]

    def descendants(index):
        yield index
        for child in document['nodes'][index].get('children', []):
            yield from descendants(child)

    # Preserve source colors and geometry; clone materials/meshes before editing.
    for setting in plan.materials:
        for index in list(descendants(part_index(setting.part))):
            node = document['nodes'][index]
            if 'mesh' not in node:
                continue
            mesh = copy.deepcopy(document['meshes'][node['mesh']])
            document['meshes'].append(mesh); node['mesh'] = len(document['meshes'])-1
            for primitive in mesh['primitives']:
                material = copy.deepcopy(document.get('materials', [])[primitive['material']]) if 'material' in primitive else {}
                material.setdefault('pbrMetallicRoughness', {}).update(roughnessFactor=setting.roughness, metallicFactor=setting.metalness)
                document.setdefault('materials', []).append(material)
                primitive['material'] = len(document['materials'])-1

    def buffer_view(data, target=None):
        binary.extend(b'\0' * (-len(binary) % 4))
        view = dict(buffer=0, byteOffset=len(binary), byteLength=len(data))
        if target: view['target'] = target
        document['bufferViews'].append(view); binary.extend(data)
        return len(document['bufferViews'])-1

    def attribute(values, width):
        array = np.asarray(values, dtype='<f4').reshape(-1, width)
        row = dict(bufferView=buffer_view(array.tobytes(), 34962), componentType=5126, count=len(array),
                   type='VEC'+str(width), min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        document['accessors'].append(row)
        return len(document['accessors'])-1

    textures = {}; report = []
    for decal in plan.decals:
        index = part_index(decal.part)
        info = image_info(root, decal)
        if decal.image not in textures:
            document.setdefault('images', []).append(dict(bufferView=buffer_view((root/decal.image).read_bytes()), mimeType=info['mimeType']))
            document.setdefault('samplers', []).append(dict(magFilter=9729, minFilter=9987, wrapS=33071, wrapT=33071))
            document.setdefault('textures', []).append(dict(source=len(document['images'])-1, sampler=len(document['samplers'])-1))
            textures[decal.image] = len(document['textures'])-1
        normal = np.asarray(decal.normal); normal = normal/np.linalg.norm(normal)
        up = np.asarray(decal.up); up = up-normal*np.dot(up, normal); up = up/np.linalg.norm(up)
        right = np.cross(up, normal)
        angle = math.radians(decal.rotation_deg)
        right, up = right*math.cos(angle)+up*math.sin(angle), up*math.cos(angle)-right*math.sin(angle)
        basis = np.stack([right, up, normal], axis=1)
        center = np.asarray(decal.center_mm)
        limits = np.array([decal.width_mm, decal.height_mm, decal.depth_mm])/2
        packed = []
        for child in list(descendants(index)):
            node = document['nodes'][child]
            if 'mesh' not in node or node.get('extras', {}).get('formforgeDecal'):
                continue
            transform = transforms[child]
            for primitive in document['meshes'][node['mesh']]['primitives']:
                points = accessor(document, original, primitive['attributes']['POSITION']).astype(float)
                points = points @ transform[:3, :3].T + transform[:3, 3]
                indices = accessor(document, original, primitive['indices']).reshape(-1, 3)
                triangles = points[indices]
                face_normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
                projected = (triangles-center) @ basis
                select = (face_normals @ normal > 1e-10) & np.all(projected.min(axis=1) <= limits, axis=1) & np.all(projected.max(axis=1) >= -limits, axis=1)
                normals = accessor(document, original, primitive['attributes']['NORMAL']).astype(float) @ np.linalg.inv(transform[:3, :3])
                normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
                for triangle, local, ns in zip(triangles[select], projected[select], normals[indices[select]]):
                    polygon = list(np.concatenate([local, triangle, ns], axis=1))
                    for axis in range(3):
                        for sign in (-1, 1): polygon = clip_polygon(polygon, axis, limits[axis], sign)
                    for j in range(1, len(polygon)-1):
                        candidate = [polygon[0], polygon[j], polygon[j+1]]
                        if np.linalg.norm(np.cross(candidate[1][3:6]-candidate[0][3:6], candidate[2][3:6]-candidate[0][3:6])) > 1e-10:
                            packed.extend(candidate)
        if not packed:
            raise ValueError('Decal does not intersect a front-facing target surface: ' + decal.id)
        values = np.asarray(packed)
        ns = values[:, 6:9]; ns /= np.maximum(np.linalg.norm(ns, axis=1, keepdims=True), 1e-12)
        # A small rendering offset avoids depth fighting; absent from STEP/STL.
        inverse = np.linalg.inv(transforms[index])
        points = (values[:, 3:6]+ns*.015) @ inverse[:3, :3].T + inverse[:3, 3]
        local_normals = ns @ transforms[index][:3, :3]
        local_normals /= np.maximum(np.linalg.norm(local_normals, axis=1, keepdims=True), 1e-12)
        uv = np.stack([values[:, 0]/decal.width_mm+.5, .5-values[:, 1]/decal.height_mm], axis=1)
        attributes = dict(POSITION=attribute(points, 3), NORMAL=attribute(local_normals, 3), TEXCOORD_0=attribute(uv, 2))
        document.setdefault('materials', []).append(dict(name='decal-'+decal.id, alphaMode='BLEND', doubleSided=False,
            pbrMetallicRoughness=dict(baseColorFactor=[1, 1, 1, decal.opacity], baseColorTexture=dict(index=textures[decal.image]), metallicFactor=0, roughnessFactor=.6)))
        document['meshes'].append(dict(primitives=[dict(attributes=attributes, material=len(document['materials'])-1, mode=4)]))
        document['nodes'].append(dict(name='__decal__'+decal.id, mesh=len(document['meshes'])-1, extras=dict(formforgeDecal=True, targetPart=decal.part)))
        document['nodes'][index].setdefault('children', []).append(len(document['nodes'])-1)
        report.append(dict(id=decal.id, part=decal.part, nodeIndex=len(document['nodes'])-1, imageSha256=decal.image_sha256,
                           triangles=len(values)//3, imageSize=[info['width'], info['height']], uvMin=uv.min(axis=0).tolist(), uvMax=uv.max(axis=0).tolist()))
    document.setdefault('asset', {}).setdefault('extras', {})['formforgeAppearance'] = dict(visualOnly=True, decalCount=len(report))
    write_glb(root/'assembly.glb', document, binary)
    return report


def apply_appearance(design, root):
    plan = getattr(design, 'appearance', None)
    if plan is None:
        return None
    names = {p.name for p in design.parts}
    if any(r.part not in names for r in [*plan.materials, *plan.decals]):
        raise ValueError('Appearance must target an explicit output part')
    references = [dict(**r.model_dump(), **image_info(root, r)) for r in plan.references]
    decals = style_glb(root, plan) if plan.materials or plan.decals else []
    result = dict(policy=POLICY['version'], brief=plan.brief, references=references,
                  materials=[r.model_dump() for r in plan.materials], decals=decals,
                  visualOnly=True, physicalGeometryChanged=False, aestheticReview='required',
                  generation=POLICY['generation'], automaticImageToCad=False)
    (root/'appearance-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    # The native verifier validates the final styled GLB, not a stale pre-style hash.
    path = root/'report.json'; report = json.loads(path.read_text(encoding='utf8'))
    for name in ['assembly.glb', 'appearance-report.json']:
        report['artifact_hashes'][name] = hashlib.sha256((root/name).read_bytes()).hexdigest()
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    return result


def verify_appearance(design, root, verification):
    """Independently read the exported hierarchy, image bytes, UVs and materials."""
    plan = getattr(design, 'appearance', None)
    if plan is None:
        return verification
    import numpy as np
    from product_ide_3d.glb import read_glb, accessor
    document, binary = read_glb(root/'assembly.glb')
    nodes = document['nodes']
    for ref in plan.references:
        image_info(root, ref)
    for decal in plan.decals:
        matches = [(i, n) for i, n in enumerate(nodes) if n.get('name') == '__decal__'+decal.id]
        if len(matches) != 1:
            raise ValueError('Exported decal identity mismatch')
        index, node = matches[0]
        parents = [n for n in nodes if index in n.get('children', [])]
        if len(parents) != 1 or parents[0].get('name', '').split('/')[-1] != decal.part:
            raise ValueError('Exported decal must remain attached to its declared part')
        primitive = document['meshes'][node['mesh']]['primitives'][0]
        uv = accessor(document, binary, primitive['attributes']['TEXCOORD_0'])
        if not len(uv) or not np.isfinite(uv).all() or uv.min() < -1e-5 or uv.max() > 1+1e-5:
            raise ValueError('Exported decal has invalid texture coordinates')
        material = document['materials'][primitive['material']]['pbrMetallicRoughness']
        image = document['images'][document['textures'][material['baseColorTexture']['index']]['source']]
        view = document['bufferViews'][image['bufferView']]
        data = binary[view.get('byteOffset', 0):view.get('byteOffset', 0)+view['byteLength']]
        if hashlib.sha256(data).hexdigest() != decal.image_sha256 or abs(material['baseColorFactor'][3]-decal.opacity) > 1e-8:
            raise ValueError('Exported decal image or opacity differs from the frozen design')
    for setting in plan.materials:
        targets = [n for n in nodes if n.get('name', '').split('/')[-1] == setting.part]
        if len(targets) != 1: raise ValueError('Exported material part missing')
        def check(node):
            if node.get('extras', {}).get('formforgeDecal'): return
            if 'mesh' in node:
                for primitive in document['meshes'][node['mesh']]['primitives']:
                    pbr = document['materials'][primitive['material']]['pbrMetallicRoughness']
                    if pbr.get('metallicFactor') != setting.metalness or pbr.get('roughnessFactor') != setting.roughness:
                        raise ValueError('Exported PBR material differs from the design')
            for child in node.get('children', []): check(nodes[child])
        check(targets[0])
    verification['checks']['appearance_glb_readback'] = True
    verification['appearanceScope'] = 'Frozen embedded images, UV bounds, parent part and PBR values; not aesthetic or manufacturing approval'
    return verification
