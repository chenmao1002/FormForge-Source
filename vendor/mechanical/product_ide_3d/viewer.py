"""Standalone offline WebGL assembly review from the delivered GLB.

This is a visual geometry/provenance review, never a load or life simulation.
All meshes and shaders are embedded; opening the HTML needs no server or CDN.
"""
import base64
import json
from pathlib import Path

import numpy as np

from .glb import read_glb, accessor, node_transform as _local_transform, scene_coordinate_frame


def _encoded(array, dtype):
    return base64.b64encode(np.ascontiguousarray(array, dtype=dtype).tobytes()).decode("ascii")


def decode_glb(path, part_names):
    """Decode real triangle geometry with every GLTF hierarchy transform applied."""
    document, binary = read_glb(Path(path))
    names = set(part_names)
    meshes, all_points = [], []
    scene_roots = document["scenes"][document.get("scene", 0)]["nodes"]
    coordinate_frame = scene_coordinate_frame(document)
    root_matrix = _local_transform(document["nodes"][scene_roots[0]])
    axis = root_matrix[:3, :3] @ np.array([0., 0., 1.])
    axis /= np.linalg.norm(axis)

    def visit(index, parent, inherited_name=None, ancestors=()):
        if index in ancestors:
            raise ValueError("GLB scene hierarchy contains a cycle")
        node = document["nodes"][index]
        node_name = node.get("name", "")
        name = node_name if node_name in names else inherited_name
        transform = parent @ _local_transform(node)
        if "mesh" in node:
            mesh = document["meshes"][node["mesh"]]
            if mesh.get("name") in names:
                name = mesh["name"]
            if name not in names:
                raise ValueError("GLB mesh cannot be matched to an assembly part: " + node_name)
            for primitive in mesh["primitives"]:
                if primitive.get("mode", 4) != 4:
                    raise ValueError("Offline viewer supports triangle primitives")
                points = np.asarray(accessor(document, binary, primitive["attributes"]["POSITION"]), dtype=float)
                points = points @ transform[:3, :3].T + transform[:3, 3]
                indices = np.asarray(accessor(document, binary, primitive["indices"])).reshape(-1).astype(np.uint32)
                if len(indices) % 3 or not len(indices) or int(indices.max()) >= len(points):
                    raise ValueError("GLB contains invalid triangle indices")
                if "NORMAL" in primitive["attributes"]:
                    normals = np.asarray(accessor(document, binary, primitive["attributes"]["NORMAL"]), dtype=float)
                    normals = normals @ np.linalg.inv(transform[:3, :3])
                else:
                    normals = np.zeros_like(points)
                    faces = indices.reshape(-1, 3)
                    vectors = np.cross(points[faces[:, 1]] - points[faces[:, 0]],
                                       points[faces[:, 2]] - points[faces[:, 0]])
                    for column in range(3):
                        np.add.at(normals, faces[:, column], vectors)
                norms = np.linalg.norm(normals, axis=1)
                normals /= np.maximum(norms[:, None], 1e-12)
                material_index = primitive.get("material")
                material = document.get("materials", [])[material_index] if material_index is not None else {}
                rgba = material.get("pbrMetallicRoughness", {}).get("baseColorFactor", [0.5, 0.6, 0.7, 1])
                meshes.append({"part": name, "positions": _encoded(points, "<f4"),
                               "normals": _encoded(normals, "<f4"), "indices": _encoded(indices, "<u4"),
                               "count": len(indices), "rgba": rgba,
                               "min": points.min(axis=0).tolist(), "max": points.max(axis=0).tolist()})
                all_points.append(points)
        for child in node.get("children", []):
            visit(child, transform, name, ancestors + (index,))

    for index in scene_roots:
        visit(index, np.eye(4))
    if not meshes:
        raise ValueError("GLB contains no matched assembly mesh")
    points = np.concatenate(all_points)
    return {"meshes": meshes, "min": points.min(axis=0).tolist(), "max": points.max(axis=0).tolist(),
            "axis": axis.tolist(), "coordinateFrame": coordinate_frame,
            "triangles": sum(mesh["count"] // 3 for mesh in meshes)}


_PROVENANCE = {
    "en": {"manufacturer_step": "Manufacturer STEP", "drawing_reconstruction": "Supplier drawing reconstruction", "custom_design": "Designed in this project"},
    "zh-CN": {"manufacturer_step": "厂商原始 STEP", "drawing_reconstruction": "厂商尺寸图重建", "custom_design": "本项目设计"},
}
_ROLES = {
    "en": {"machined": "CNC part", "purchased": "Purchased part", "fastener": "Fastener"},
    "zh-CN": {"machined": "CNC 自制件", "purchased": "采购件", "fastener": "紧固件"},
}
_UI = {
    "en": {
        "pageTitle": "Mechanical joint · 3D assembly review", "language": "Language", "target": "target",
        "reduction": "harmonic reduction", "efficiency": "Assumed efficiency", "bounds": "Assembly envelope (CAD XYZ)",
        "operating": "{torque} N·m joint design; reference output speed {rpm} rpm. Rating and dimension screening does not establish physical load capacity.",
        "reset": "Reset view", "showAll": "Show all", "explode": "Axial exploded view", "parts": "Assembly parts · select to show",
        "source": "Source", "notes": "Part / process notes", "showPart": "Show", "caption": "3D assembly · delivered GLB geometry",
        "hint": "Drag to orbit / wheel to zoom\nRight-drag or Shift-drag to pan", "provenanceNote": "Drawing reconstructions preserve envelopes and mating interfaces. Supplier information governs purchased-part internals. This file works fully offline; source links open only on request.",
        "webgl": "WebGL 2 is unavailable. Open this HTML in a current Edge or Chrome browser with hardware acceleration enabled.",
        "notStarted": "3D rendering did not start", "loadFailed": "3D geometry failed to load: ",
        "partCount": "parts", "triangles": "triangles", "visible": "shown",
    },
    "zh-CN": {
        "pageTitle": "机械臂关节 · 3D 装配查看", "language": "语言", "target": "设计目标",
        "reduction": "谐波减速", "efficiency": "效率假设", "bounds": "装配外包尺寸（CAD XYZ）",
        "operating": "{torque} N·m 关节设计方案，参考输出速度 {rpm} rpm。额定参数和尺寸筛选不等于实物负载验证。",
        "reset": "重置视角", "showAll": "全部显示", "explode": "轴向爆炸视图", "parts": "装配零件 · 勾选显示",
        "source": "来源", "notes": "零件与加工说明", "showPart": "显示", "caption": "3D 装配 · 实际交付 GLB 网格",
        "hint": "拖动旋转 / 滚轮缩放\n右键拖动或 Shift 拖动平移", "provenanceNote": "图纸重建模型保留外形和装配接口；采购件内部结构以厂商资料为准。此文件完整离线运行，来源链接按需打开。",
        "webgl": "此浏览器未启用 WebGL 2。请用启用硬件加速的新版 Edge 或 Chrome 打开本 HTML。",
        "notStarted": "3D 渲染未启动", "loadFailed": "3D 网格加载失败：",
        "partCount": "零件", "triangles": "三角形", "visible": "个显示",
    },
}


def export_viewer(glb_path, html_path, design, model, language="en", localization=None):
    language = {"en": "en", "zh": "zh-CN", "zh-cn": "zh-CN"}.get(language.replace("_", "-").casefold())
    if language is None:
        raise ValueError("Viewer language must be en or zh-CN")
    geometry = decode_glb(glb_path, [part.name for part in design.parts])
    parts = []
    for part in design.parts:
        measure = model.measures[part.name]
        centre_cad = (np.asarray(measure["min_mm"]) + np.asarray(measure["max_mm"])) / 2
        # Axial ordering is recorded in the original CAD Z frame. GLB root
        # transform converts the original Z direction to the displayed axis.
        localized = {}
        for choice in ("en", "zh-CN"):
            localized[choice] = {
                "name": localization.part(part.name, choice) if localization else part.name,
                "role": localization.role(part.role, choice) if localization else _ROLES[choice][part.role],
                "provenance": _PROVENANCE[choice][part.model_provenance],
                "material": localization.text(part.material, choice) if localization else part.material,
                "notes": [localization.text(note, choice) if localization else note for note in part.notes],
            }
        parts.append({"name": part.name, "role": part.role, "provenance": part.model_provenance,
                      "bounds": measure["bounds_mm"], "sources": part.sources,
                      "axialCentre": float(centre_cad[2]), "material": part.material, "localized": localized})
    ordered = sorted(parts, key=lambda part: part["axialCentre"])
    for rank, part in enumerate(ordered):
        part["explodeRank"] = 0 if len(ordered) == 1 else 2 * rank / (len(ordered) - 1) - 1
    data = {"name": design.name, "language": language, "interface": _UI,
            "targetTorque": design.target_output_torque_nm,
            "nominalOutputRpm": design.nominal_output_rpm,
            "ratio": design.ratio, "efficiency": design.efficiency_assumption,
            "boundsCad": model.measures["assembly_bounds_mm"],
            "parts": parts, "geometry": geometry}
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    Path(html_path).write_text(_HTML.replace("__DATA__", encoded), encoding="utf-8")
    return {"offline": True, "triangles": geometry["triangles"], "parts": len(parts),
            "bytes": Path(html_path).stat().st_size}


_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mechanical joint · 3D assembly review</title>
<style>
:root{color-scheme:dark;font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#e5edf4;background:#09131f}
*{box-sizing:border-box}body{margin:0;height:100vh;display:grid;grid-template-columns:330px 1fr;overflow:hidden}
aside{padding:24px 20px;background:#101d2b;border-right:1px solid #274052;overflow:auto}h1{font-size:21px;line-height:1.45;margin:6px 0 12px;font-weight:650}h2{font-size:14px;font-weight:600;margin:26px 0 12px;color:#b5c8d9}
.eyebrow{font-size:11px;letter-spacing:2px;color:#22d3ee}.spec{display:flex;gap:7px;flex-wrap:wrap;margin:15px 0}.spec span{padding:6px 9px;background:#1b3447;color:#bce8f1;border-radius:6px;font-size:12px}.note{font-size:12px;line-height:1.65;color:#8fa8bc}
.toolbar{display:flex;gap:8px;margin:18px 0}button{font:inherit;font-size:12px;border:1px solid #365266;background:#193245;color:#d9edf7;border-radius:6px;padding:8px 10px;cursor:pointer}button:hover{background:#25485e}
.control{font-size:13px;color:#d0dfe9}input[type=range]{width:100%;accent-color:#22d3ee;margin:12px 0 5px}input[type=checkbox]{accent-color:#22d3ee;flex:none}.part{margin:7px 0;padding:10px;background:#152738;border:1px solid #243d50;border-radius:7px}.part label{display:flex;align-items:center;gap:7px;cursor:pointer;font-size:12px;word-break:break-word}.swatch{width:9px;height:9px;flex:none;border-radius:50%}.part small{display:block;color:#8fa8bc;font-size:11px;line-height:1.6;margin:6px 0 0 25px}.part a{color:#69d6e7;text-decoration:none;margin-right:8px}
.languageRow{display:flex;align-items:center;justify-content:space-between;margin:0 0 20px;font-size:12px;color:#8fa8bc}select{font:inherit;padding:6px 9px;border:1px solid #365266;border-radius:6px;color:#d9edf7;background:#193245}.technicalId{font-family:Consolas,monospace}.part details{margin:8px 0 0 25px;color:#8fa8bc;font-size:11px;line-height:1.6}.part summary{cursor:pointer;color:#bdd5e5}.part details p{white-space:pre-line;margin:6px 0 0}
main{position:relative;min-width:0;min-height:0}canvas{width:100%;height:100%;display:block;touch-action:none}.caption{position:absolute;top:22px;left:24px;pointer-events:none}.caption strong{font-size:14px;font-weight:500;color:#d4e1eb}.caption p{font-size:12px;color:#7d9caf;line-height:1.65;margin:7px 0}.badge{position:absolute;right:22px;bottom:20px;padding:8px 11px;border:1px solid #284357;border-radius:6px;font-size:11px;color:#99b6c9;background:#0c1a27cc;pointer-events:none}.error{position:absolute;left:10%;top:35%;width:80%;padding:25px;background:#39202a;color:#ffbdc6;border-radius:10px;display:none}
@media(max-width:780px){body{grid-template-columns:260px 1fr}aside{padding:16px 12px}h1{font-size:17px}.caption{left:14px;top:15px}.caption p{max-width:180px}.badge{right:12px;bottom:12px}}@media(max-width:520px){body{grid-template-columns:1fr;grid-template-rows:60vh 40vh}main{grid-row:1}aside{grid-row:2;border-right:0;border-top:1px solid #274052}.caption p{max-width:none}}
</style></head><body><aside>
<label class="languageRow"><span id="languageLabel">Language</span><select id="language" aria-label="Language"><option value="en">English</option><option value="zh-CN">简体中文</option></select></label>
<div class="eyebrow">PRODUCT IDE 3D / LOCAL REVIEW</div><h1 id="title"></h1><div class="spec" id="spec"></div>
<p class="note" id="bounds"></p><p class="note" id="operatingNote"></p>
<div class="toolbar"><button id="reset">Reset view</button><button id="showAll">Show all</button></div>
<div class="control"><span id="explodeLabel">Axial exploded view</span> <span id="explodeValue">0%</span><input id="explode" type="range" min="0" max="100" value="0" aria-label="Axial exploded view"></div>
<h2 id="partsHeading">Assembly parts</h2><div id="parts"></div><p class="note" id="provenanceNote"></p>
</aside><main><canvas id="scene" aria-label="3D assembly review"></canvas>
<div class="caption"><strong id="caption"></strong><p id="hint" style="white-space:pre-line"></p></div><div class="badge" id="status"></div><div class="error" id="error"></div>
</main><script id="model-data" type="application/json">__DATA__</script><script>
'use strict';
const data=JSON.parse(document.getElementById('model-data').textContent),geo=data.geometry;
let language=data.language||'en';
document.getElementById('title').textContent=data.name;
const visibility=new Map(data.parts.map(p=>[p.name,true])),partByName=new Map(data.parts.map(p=>[p.name,p])),checks=[],partElements=[];
for(const part of data.parts){const card=document.createElement('div');card.className='part';const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=true;check.addEventListener('change',()=>{visibility.set(part.name,check.checked);if(typeof window.render==='function')render()});checks.push(check);const swatch=document.createElement('span');swatch.className='swatch';const mesh=geo.meshes.find(m=>m.part===part.name),rgb=mesh?mesh.rgba.slice(0,3).map(v=>Math.round(Math.pow(v,1/2.2)*255)):[125,165,190];swatch.style.background='rgb('+rgb.join(',')+')';const text=document.createElement('span');label.append(check,swatch,text);const id=document.createElement('small');id.className='technicalId';id.textContent=part.name;const note=document.createElement('small');note.style.whiteSpace='pre-line';card.append(label,id,note);let summary=null,notes=null;if(part.localized.en.notes.length){const details=document.createElement('details');summary=document.createElement('summary');notes=document.createElement('p');details.append(summary,notes);card.append(details)}const sourceLinks=[];if(part.sources.length){const links=document.createElement('small');part.sources.forEach((source,i)=>{const a=document.createElement('a');a.href=source;a.target='_blank';a.rel='noopener noreferrer';links.append(a);sourceLinks.push(a)});card.append(links)}partElements.push({part,check,text,id,note,summary,notes,sourceLinks});document.getElementById('parts').append(card)}
function updateLanguage(){const ui=data.interface[language];document.documentElement.lang=language;document.title=ui.pageTitle+' — '+data.name;document.getElementById('language').value=language;for(const [id,key]of [['languageLabel','language'],['reset','reset'],['showAll','showAll'],['explodeLabel','explode'],['partsHeading','parts'],['provenanceNote','provenanceNote'],['caption','caption'],['hint','hint']])document.getElementById(id).textContent=ui[key];document.getElementById('language').setAttribute('aria-label',ui.language);document.getElementById('explode').setAttribute('aria-label',ui.explode);document.getElementById('scene').setAttribute('aria-label',ui.caption+'; '+ui.hint);const specs=document.getElementById('spec');specs.replaceChildren();for(const value of [data.targetTorque+' N·m '+ui.target,data.ratio+':1 '+ui.reduction,ui.efficiency+' '+Math.round(data.efficiency*100)+'%']){const s=document.createElement('span');s.textContent=value;specs.append(s)}document.getElementById('bounds').textContent=ui.bounds+' '+data.boundsCad.map(v=>v.toFixed(1)).join(' × ')+' mm';document.getElementById('operatingNote').textContent=ui.operating.replace('{torque}',data.targetTorque).replace('{rpm}',data.nominalOutputRpm);for(const entry of partElements){const local=entry.part.localized[language];entry.text.textContent=local.name;entry.id.style.display=local.name===entry.part.name?'none':'block';entry.check.setAttribute('aria-label',ui.showPart+' '+local.name+' ('+entry.part.name+')');entry.note.textContent=local.role+' · '+local.provenance+'\n'+local.material+'\n'+entry.part.bounds.map(v=>v.toFixed(1)).join(' × ')+' mm';if(entry.summary){entry.summary.textContent=ui.notes;entry.notes.textContent=local.notes.join('\n')}entry.sourceLinks.forEach((a,i)=>a.textContent=ui.source+' '+(i+1))}if(typeof window.render==='function')render()}
document.getElementById('language').addEventListener('change',e=>{language=e.target.value;updateLanguage()});updateLanguage();
const canvas=document.getElementById('scene'),gl=canvas.getContext('webgl2',{antialias:true,alpha:false});
function fail(message){const e=document.getElementById('error');e.textContent=message;e.style.display='block';document.getElementById('status').textContent=data.interface[language].notStarted}
if(!gl){fail(data.interface[language].webgl)}else{try{start()}catch(error){fail(data.interface[language].loadFailed+error.message);console.error(error)}}
function start(){
const vertex=`#version 300 es
in vec3 aPosition;in vec3 aNormal;uniform mat4 uVP;uniform vec3 uShift;out vec3 vNormal;out vec3 vPosition;
void main(){vNormal=aNormal;vPosition=aPosition+uShift;gl_Position=uVP*vec4(vPosition,1.0);}`;
const fragment=`#version 300 es
precision highp float;in vec3 vNormal;in vec3 vPosition;uniform vec3 uColor;uniform vec3 uEye;out vec4 frag;
void main(){vec3 n=normalize(vNormal);if(!gl_FrontFacing)n=-n;vec3 l=normalize(vec3(0.5,0.8,0.7));float light=0.38+0.52*max(dot(n,l),0.0)+0.10*max(dot(n,normalize(vec3(-0.6,0.2,-0.8))),0.0);vec3 v=normalize(uEye-vPosition);float spec=pow(max(dot(n,normalize(l+v)),0.0),45.0)*0.22;vec3 c=uColor*light+vec3(spec);frag=vec4(pow(c,vec3(1.0/2.2)),1.0);}`;
function shader(type,source){const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s}
const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,vertex));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));gl.useProgram(program);
const loc={position:gl.getAttribLocation(program,'aPosition'),normal:gl.getAttribLocation(program,'aNormal'),vp:gl.getUniformLocation(program,'uVP'),shift:gl.getUniformLocation(program,'uShift'),color:gl.getUniformLocation(program,'uColor'),eye:gl.getUniformLocation(program,'uEye')};
function decode64(value,Type){const raw=atob(value),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);return new Type(bytes.buffer)}
const meshes=geo.meshes.map(mesh=>{const vao=gl.createVertexArray();gl.bindVertexArray(vao);for(const [key,location]of [['positions',loc.position],['normals',loc.normal]]){const b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,decode64(mesh[key],Float32Array),gl.STATIC_DRAW);gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,3,gl.FLOAT,false,0,0)}const ib=gl.createBuffer();gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,ib);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,decode64(mesh.indices,Uint32Array),gl.STATIC_DRAW);return{...mesh,vao}});
const centre=geo.min.map((v,i)=>(v+geo.max[i])/2),extent=Math.max(...geo.max.map((v,i)=>v-geo.min[i])),initialDistance=extent*2.1;
let yaw=0.65,pitch=0.34,distance=initialDistance,displayDistance=initialDistance,pan=[0,0,0],explosion=0,pointer=null;
function sub(a,b){return a.map((v,i)=>v-b[i])}function cross(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]}function dot(a,b){return a.reduce((s,v,i)=>s+v*b[i],0)}function unit(a){const l=Math.hypot(...a);return a.map(v=>v/l)}
function multiply(a,b){const r=new Float32Array(16);for(let c=0;c<4;c++)for(let row=0;row<4;row++)for(let k=0;k<4;k++)r[c*4+row]+=a[k*4+row]*b[c*4+k];return r}
function perspective(fov,aspect,near,far){const f=1/Math.tan(fov/2),r=new Float32Array(16);r[0]=f/aspect;r[5]=f;r[10]=(far+near)/(near-far);r[11]=-1;r[14]=2*far*near/(near-far);return r}
function lookAt(eye,target){const z=unit(sub(eye,target)),x=unit(cross([0,1,0],z)),y=cross(z,x);return new Float32Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1])}
window.render=function(){const dpr=Math.min(devicePixelRatio||1,2),w=Math.round(canvas.clientWidth*dpr),h=Math.round(canvas.clientHeight*dpr);if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}gl.viewport(0,0,w,h);gl.clearColor(0.035,0.071,0.115,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);const visibleMeshes=meshes.filter(mesh=>visibility.get(mesh.part)),boundsMin=[Infinity,Infinity,Infinity],boundsMax=[-Infinity,-Infinity,-Infinity];for(const mesh of visibleMeshes){const amount=partByName.get(mesh.part).explodeRank*explosion*extent*0.85;mesh.shift=geo.axis.map(v=>v*amount);for(let i=0;i<3;i++){boundsMin[i]=Math.min(boundsMin[i],mesh.min[i]+mesh.shift[i]);boundsMax[i]=Math.max(boundsMax[i],mesh.max[i]+mesh.shift[i])}}if(!visibleMeshes.length){for(let i=0;i<3;i++){boundsMin[i]=geo.min[i];boundsMax[i]=geo.max[i]}}const target=boundsMin.map((v,i)=>(v+boundsMax[i])/2+pan[i]),radius=Math.max(0.1,Math.hypot(...boundsMax.map((v,i)=>v-boundsMin[i]))/2),aspect=w/Math.max(h,1),halfAngle=Math.min(0.325,Math.atan(Math.tan(0.325)*aspect));displayDistance=radius/Math.sin(halfAngle)*1.10*(distance/initialDistance);const eye=[target[0]+displayDistance*Math.cos(pitch)*Math.sin(yaw),target[1]+displayDistance*Math.sin(pitch),target[2]+displayDistance*Math.cos(pitch)*Math.cos(yaw)];const vp=multiply(perspective(0.65,aspect,Math.max(radius*0.001,0.01),displayDistance+radius*4+extent),lookAt(eye,target));gl.uniformMatrix4fv(loc.vp,false,vp);gl.uniform3fv(loc.eye,eye);for(const mesh of visibleMeshes){gl.uniform3fv(loc.shift,mesh.shift);gl.uniform3fv(loc.color,mesh.rgba.slice(0,3));gl.bindVertexArray(mesh.vao);gl.drawElements(gl.TRIANGLES,mesh.count,gl.UNSIGNED_INT,0)}const ui=data.interface[language];document.getElementById('status').textContent=visibility.size+' '+ui.partCount+' / '+geo.triangles.toLocaleString()+' '+ui.triangles+' | '+[...visibility.values()].filter(Boolean).length+' '+ui.visible;window.__assemblyViewer={ready:true,partCount:visibility.size,visibleParts:[...visibility].filter(p=>p[1]).map(p=>p[0]),triangles:geo.triangles,explosion,drawCalls:visibleMeshes.length,width:w,height:h,language,displayDistance,displayRadius:radius};};
canvas.addEventListener('contextmenu',e=>e.preventDefault());canvas.addEventListener('pointerdown',e=>{pointer={id:e.pointerId,x:e.clientX,y:e.clientY,pan:e.button===2||e.shiftKey};canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!pointer||pointer.id!==e.pointerId)return;const dx=e.clientX-pointer.x,dy=e.clientY-pointer.y;pointer.x=e.clientX;pointer.y=e.clientY;if(pointer.pan){const factor=displayDistance*0.0015;pan[0]-=dx*factor*Math.cos(yaw);pan[2]+=dx*factor*Math.sin(yaw);pan[1]+=dy*factor}else{yaw-=dx*0.008;pitch=Math.max(-1.48,Math.min(1.48,pitch+dy*0.008))}render()});function release(){pointer=null}canvas.addEventListener('pointerup',release);canvas.addEventListener('pointercancel',release);canvas.addEventListener('wheel',e=>{e.preventDefault();distance=Math.max(extent*0.3,Math.min(extent*15,distance*Math.exp(e.deltaY*0.001)));render()},{passive:false});
document.getElementById('explode').addEventListener('input',e=>{explosion=Number(e.target.value)/100;document.getElementById('explodeValue').textContent=e.target.value+'%';render()});document.getElementById('reset').addEventListener('click',()=>{yaw=0.65;pitch=0.34;distance=initialDistance;pan=[0,0,0];render()});document.getElementById('showAll').addEventListener('click',()=>{for(const key of visibility.keys())visibility.set(key,true);for(const check of checks)check.checked=true;render()});new ResizeObserver(render).observe(canvas);render();
}
</script></body></html>'''
