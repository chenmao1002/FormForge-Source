"""Offline articulated-arm preview using the triangles from the delivered GLB.

The GLB contains baked default-pose geometry. Joint changes apply the complete
hierarchical rigid transform relative to that pose, including the GLTF root
coordinate conversion. Sliders provide visual review, not motion certification.
"""
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

from .glb import read_glb
from .viewer import _local_transform, decode_glb


_LABELS = {
    "base": "安装底座", "shoulder": "肩关节", "upper": "肩关节", "upper_arm": "上臂",
    "elbow": "肘关节", "forearm": "肘关节", "wrist": "腕部",
    "gripper": "夹爪", "finger_left": "左夹指", "finger_right": "右夹指",
    "left_finger": "左夹指", "right_finger": "右夹指",
    "default": "默认姿态", "rest": "收拢", "folded": "收拢",
    "reach": "前伸", "extended": "前伸", "lift": "抬起", "raised": "抬起",
    "grip": "夹爪活动指", "open": "张开", "closed": "闭合",
}
_PROVENANCE = {
    "custom_design": "项目设计", "drawing_reconstruction": "资料尺寸重建",
    "manufacturer_step": "厂商 STEP", "envelope": "采购件外形包络",
    "dimensioned_envelope": "资料尺寸外形包络",
}


def _label(item):
    return (getattr(item, "label", None) or getattr(item, "display_name", None) or getattr(item, "label_zh", None)
            or _LABELS.get(item.name, item.name))


def _columns(matrix):
    result = np.asarray(matrix, dtype=float)
    if result.shape != (4, 4) or not np.isfinite(result).all():
        raise ValueError("Arm viewer transform must be a finite 4 x 4 matrix")
    return result.T.reshape(-1).tolist()


def _safe_sources(sources):
    """Expose source links without accepting executable URL schemes."""
    return [source for source in sources
            if urlsplit(source).scheme.lower() in {"http", "https"}
            and urlsplit(source).netloc]


def _validate_geometry(geometry):
    if not np.isfinite(np.asarray([geometry["min"], geometry["max"]], dtype=float)).all():
        raise ValueError("Arm viewer GLB geometry bounds must be finite")
    for mesh in geometry["meshes"]:
        lengths = []
        for name in ("positions", "normals"):
            values = np.frombuffer(base64.b64decode(mesh[name], validate=True), dtype="<f4")
            if not len(values) or len(values) % 3 or not np.isfinite(values).all():
                raise ValueError("Arm viewer GLB geometry must be finite triangle data")
            lengths.append(len(values))
        if lengths[0] != lengths[1]:
            raise ValueError("Arm viewer GLB positions and normals must match")
        rgba = np.asarray(mesh["rgba"], dtype=float)
        if rgba.shape != (4,) or not np.isfinite(rgba).all():
            raise ValueError("Arm viewer GLB material must be finite")


def export_arm_viewer(glb_path, html_path, design, model):
    """Write a standalone Chinese WebGL viewer for an articulated-arm model."""
    from .arm_cad import forward_kinematics

    names = [part.name for part in design.parts]
    geometry = decode_glb(glb_path, names)
    _validate_geometry(geometry)
    mesh_names = {mesh["part"] for mesh in geometry["meshes"]}
    if mesh_names != set(names):
        raise ValueError("Arm viewer GLB must contain every design part")
    document, _ = read_glb(Path(glb_path))
    roots = document["scenes"][document.get("scene", 0)]["nodes"]
    if len(roots) != 1:
        raise ValueError("Articulated arm GLB requires one common coordinate root")
    basis = _local_transform(document["nodes"][roots[0]])
    try:
        np.linalg.inv(basis)
    except np.linalg.LinAlgError as error:
        raise ValueError("Arm viewer GLB coordinate root must be invertible") from error
    default = forward_kinematics(design)
    links = [{"name": link.name, "label": _label(link), "parent": link.parent,
              "translation": list(link.translation), "axis": list(link.axis),
              "angle": link.angle_deg, "min": link.min_angle_deg,
              "max": link.max_angle_deg,
              "defaultInverse": _columns(np.linalg.inv(basis @ np.asarray(default[link.name])))}
             for link in design.links]
    parts = []
    for part in design.parts:
        parts.append({"name": part.name, "label": _label(part), "link": part.link,
                      "material": getattr(part, "material", ""),
                      "provenance": _PROVENANCE.get(getattr(part, "model_provenance", ""),
                                                    getattr(part, "model_provenance", "")),
                      "sources": _safe_sources(getattr(part, "sources", [])),
                      "notes": list(getattr(part, "notes", []))})
    poses = [{"name": "default", "label": "默认姿态", "angles": {}}]
    poses += [{"name": pose.name, "label": _label(pose), "angles": dict(pose.angles_deg)}
              for pose in design.poses if pose.name != "default"]
    data = {"name": design.name, "links": links, "parts": parts, "poses": poses,
            "basis": _columns(basis), "geometry": geometry,
            "summary": {"payload_g": getattr(design, "payload_g", None),
                        "target_payload_g": getattr(design, "target_payload_g", None),
                        "budget_cny": getattr(design, "budget_cny", None),
                        "measures": model.measures, "checks": model.checks,
                        "warnings": model.warnings},
            "notes": list(getattr(design, "notes", [])),
            "assumptions": list(getattr(design, "assumptions", []))}
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    # User/source names are only ever textContent; prevent ending the JSON script.
    encoded = encoded.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    target = Path(html_path)
    target.write_text(_HTML.replace("__DATA__", encoded), encoding="utf-8")
    return {"offline": True, "triangles": geometry["triangles"], "parts": len(parts),
            "links": len(links), "poses": len(poses), "bytes": target.stat().st_size}


# Matrix utilities have no browser dependencies and are exercised by a JS
# regression test against the CAD kernel's forward-kinematics result.
_MATRIX_JS = r'''
function identity(){return new Float64Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])}
function multiply(a,b){const r=new Float64Array(16);for(let c=0;c<4;c++)for(let row=0;row<4;row++)for(let k=0;k<4;k++)r[c*4+row]+=a[k*4+row]*b[c*4+k];return r}
function localMatrix(link,angle){const a=angle*Math.PI/180,[x,y,z]=link.axis,n=Math.hypot(x,y,z),u=x/n,v=y/n,w=z/n,c=Math.cos(a),s=Math.sin(a),t=1-c;return new Float64Array([c+u*u*t,v*u*t+w*s,w*u*t-v*s,0,u*v*t-w*s,c+v*v*t,w*v*t+u*s,0,u*w*t+v*s,v*w*t-u*s,c+w*w*t,0,...link.translation,1])}
function worldMatrices(links,angles){const byName=new Map(links.map(l=>[l.name,l])),result=new Map();function visit(name){if(result.has(name))return result.get(name);const l=byName.get(name),world=multiply(l.parent?visit(l.parent):identity(),localMatrix(l,angles[name]??l.angle));result.set(name,world);return world}for(const l of links)visit(l.name);return result}
function poseDeltas(links,angles,basis){const world=worldMatrices(links,angles),result=new Map();for(const l of links)result.set(l.name,multiply(multiply(basis,world.get(l.name)),l.defaultInverse));return result}
function point(matrix,p){return[0,1,2].map(r=>matrix[r]*p[0]+matrix[4+r]*p[1]+matrix[8+r]*p[2]+matrix[12+r])}
function normalMatrix(m){const a=[m[0],m[1],m[2]],b=[m[4],m[5],m[6]],c=[m[8],m[9],m[10]],cross=(u,v)=>[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]],x=cross(b,c),y=cross(c,a),z=cross(a,b),det=a.reduce((sum,v,i)=>sum+v*x[i],0);return new Float32Array([...x,...y,...z].map(v=>v/det))}
'''

_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>小型机器人手臂 · 3D 动作预览</title><style>
:root{color-scheme:dark;font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#e5edf4;background:#09131f}*{box-sizing:border-box}body{margin:0;height:100vh;display:grid;grid-template-columns:330px 1fr;overflow:hidden}aside{padding:22px 20px;background:#101d2b;border-right:1px solid #274052;overflow:auto}h1{font-size:21px;line-height:1.5;margin:8px 0 12px}h2{font-size:14px;color:#bdd5e5;margin:22px 0 12px}.eyebrow{color:#22d3ee;font-size:11px;letter-spacing:1.5px}.note{font-size:12px;line-height:1.7;color:#9bb2c4;white-space:pre-line}.spec{display:flex;gap:7px;flex-wrap:wrap}.spec span{padding:6px 9px;background:#1b3447;color:#bce8f1;border-radius:6px;font-size:12px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}button,select{font:inherit;font-size:12px;border:1px solid #365266;background:#193245;color:#d9edf7;border-radius:6px;padding:8px 10px;cursor:pointer}button:hover{background:#25485e}select{width:100%}.control{margin:16px 0;font-size:13px}.control label{display:flex;justify-content:space-between;gap:10px}.control small{font-size:11px;color:#829caf}input[type=range]{width:100%;accent-color:#22d3ee;margin:10px 0 2px}input[type=checkbox]{accent-color:#22d3ee;flex:none}.part{padding:10px;margin:7px 0;background:#152738;border:1px solid #243d50;border-radius:7px}.part label{display:flex;gap:7px;align-items:center;cursor:pointer;font-size:12px;word-break:break-word}.part small{display:block;margin:5px 0 0 25px;font-size:11px;line-height:1.65;color:#8fa8bc}.part a{color:#69d6e7;margin-right:10px;text-decoration:none}.part details{font-size:11px;line-height:1.65;color:#8fa8bc;margin:7px 0 0 25px}.part summary{cursor:pointer}.part p{white-space:pre-line;margin:6px 0}.swatch{width:9px;height:9px;flex:none;border-radius:50%}main{position:relative;min-width:0;min-height:0}canvas{width:100%;height:100%;display:block;touch-action:none}.caption{position:absolute;top:22px;left:24px;pointer-events:none}.caption strong{font-size:14px;font-weight:500}.caption p{font-size:12px;color:#91a9bb;line-height:1.6;margin:7px 0}.badge{position:absolute;right:20px;bottom:20px;padding:8px 11px;border:1px solid #284357;border-radius:6px;font-size:11px;color:#aec5d4;background:#0c1a27dd;pointer-events:none}.error{position:absolute;left:10%;top:35%;width:80%;padding:25px;background:#39202a;color:#ffbdc6;border-radius:10px;display:none}
@media(max-width:780px){body{grid-template-columns:270px 1fr}aside{padding:16px 12px}h1{font-size:18px}.caption{left:14px;top:15px}.caption p{max-width:175px}.badge{right:12px;bottom:12px}}@media(max-width:560px){body{grid-template-columns:1fr;grid-template-rows:57vh 43vh}main{grid-row:1}aside{grid-row:2;border-right:0;border-top:1px solid #274052}.caption p{max-width:none}}
</style></head><body><aside><div class="eyebrow">PRODUCT IDE 3D / 离线动作审查</div><h1 id="title"></h1><div class="spec" id="spec"></div><p class="note" id="summary"></p>
<h2>关节动作</h2><select id="pose" aria-label="预设姿态"></select><div id="joints"></div><div class="toolbar"><button id="reset">重置姿态与视角</button><button id="showAll">全部显示</button></div>
<p class="note" id="motionNote">滑块允许范围内的连续外观预览。碰撞、间隙和力矩只检查设计文件中的离散预设姿态；滑动过程和预设之间的路径尚未验证。载荷为设计目标，尚未完成实物测试。</p><details class="note"><summary>检查依据与假设</summary><p id="assumptions"></p></details>
<h2>零件 · 勾选显示</h2><div id="parts"></div><p class="note">画面使用本次交付 GLB 的实际网格，完全离线运行。采购件的外形重建用于安装和空间检查，不代表内部结构。来源链接按需打开。</p></aside>
<main><canvas id="scene" aria-label="机器人手臂三维动作预览"></canvas><div class="caption"><strong>串联手臂 · 三维动作预览</strong><p>拖动旋转 / 滚轮缩放<br>右键或 Shift 拖动平移</p></div><div class="badge" id="status">正在加载实际 GLB 网格…</div><div class="error" id="error"></div></main>
<script id="model-data" type="application/json">__DATA__</script><script>
'use strict';
const data=JSON.parse(document.getElementById('model-data').textContent),geo=data.geometry;
__MATRIX_JS__
const visibility=new Map(data.parts.map(p=>[p.name,true])),partByName=new Map(data.parts.map(p=>[p.name,p])),angles=Object.fromEntries(data.links.map(l=>[l.name,l.angle])),checks=[],controls=[];
document.getElementById('title').textContent=data.name;
function spec(text){const span=document.createElement('span');span.textContent=text;document.getElementById('spec').append(span)}
spec(data.parts.length+' 个零件');spec(data.links.filter(l=>l.parent&&l.min<l.max).length+' 个活动关节');
const summary=data.summary,m=summary.measures,payload=summary.payload_g??summary.target_payload_g;
if(payload!=null)spec('载荷目标 '+payload+' g');if(summary.budget_cny!=null)spec('预算目标 ¥'+summary.budget_cny);
const summaryLines=[];if(m.assembly_bounds_mm)summaryLines.push('默认姿态外包尺寸 '+m.assembly_bounds_mm.map(v=>Number(v).toFixed(1)).join(' × ')+' mm');
for(const key of ['estimated_cost_cny','total_cost_cny','cost_estimate_cny'])if(m[key]!=null){summaryLines.push('估算零件费用 ¥'+Number(m[key]).toFixed(1)+'（未含运费、工具和电源）');break}
if(m.total_part_mass_g!=null)summaryLines.push('零件计算质量 '+Number(m.total_part_mass_g).toFixed(1)+' g');if(m.poses)summaryLines.push('已检查 '+m.poses.length+' 个离散姿态');if(summary.checks.all_declared_poses_no_interference!=null)summaryLines.push('离散姿态干涉检查：'+(summary.checks.all_declared_poses_no_interference?'通过':'存在未通过项'));if(summary.checks.all_sampled_static_torque_budgets!=null)summaryLines.push('离散静态力矩规划：'+(summary.checks.all_sampled_static_torque_budgets?'通过':'存在未通过项'));if(data.notes.length)summaryLines.push(...data.notes);document.getElementById('summary').textContent=summaryLines.join('\n');document.getElementById('assumptions').textContent=[...data.assumptions,...summary.warnings].join('\n');
const poseSelect=document.getElementById('pose');for(const pose of data.poses){const option=document.createElement('option');option.value=pose.name;option.textContent=pose.label;poseSelect.append(option)}const custom=document.createElement('option');custom.value='__custom__';custom.textContent='手动调整（路径未验证）';poseSelect.append(custom);
function updateControls(){for(const c of controls){c.input.value=angles[c.link.name];c.value.textContent=Number(angles[c.link.name]).toFixed(1)+'°'}}
function setPose(name){const pose=data.poses.find(p=>p.name===name);if(!pose)return;for(const l of data.links)angles[l.name]=pose.angles[l.name]??l.angle;poseSelect.value=name;updateControls();if(typeof window.render==='function')render()}
poseSelect.addEventListener('change',e=>setPose(e.target.value));
for(const link of data.links.filter(l=>l.parent&&l.min<l.max)){const card=document.createElement('div');card.className='control';const label=document.createElement('label'),name=document.createElement('span'),value=document.createElement('span');name.textContent=link.label;label.append(name,value);const input=document.createElement('input');input.type='range';input.min=link.min;input.max=link.max;input.step='0.1';input.value=link.angle;input.setAttribute('aria-label',link.label);input.dataset.link=link.name;const limits=document.createElement('small');limits.textContent=link.min+'° ～ '+link.max+'°';input.addEventListener('input',()=>{angles[link.name]=Number(input.value);poseSelect.value='__custom__';updateControls();if(typeof window.render==='function')render()});card.append(label,input,limits);controls.push({link,input,value});document.getElementById('joints').append(card)}updateControls();
for(const part of data.parts){const card=document.createElement('div');card.className='part';const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=true;check.dataset.part=part.name;check.setAttribute('aria-label','显示 '+part.label);check.addEventListener('change',()=>{visibility.set(part.name,check.checked);if(typeof window.render==='function')render()});checks.push(check);const swatch=document.createElement('span');swatch.className='swatch';const mesh=geo.meshes.find(m=>m.part===part.name);swatch.style.background='rgb('+mesh.rgba.slice(0,3).map(v=>Math.round(Math.pow(Math.max(v,0),1/2.2)*255)).join(',')+')';const name=document.createElement('span');name.textContent=part.label;label.append(check,swatch,name);card.append(label);const note=document.createElement('small');note.textContent=[part.name,part.material,part.provenance].filter(Boolean).join(' / ');card.append(note);if(part.notes.length){const details=document.createElement('details'),title=document.createElement('summary'),text=document.createElement('p');title.textContent='零件说明';text.textContent=part.notes.join('\n');details.append(title,text);card.append(details)}if(part.sources.length){const sources=document.createElement('small');part.sources.forEach((source,index)=>{const a=document.createElement('a');a.href=source;a.textContent='来源 '+(index+1);a.target='_blank';a.rel='noopener noreferrer';sources.append(a)});card.append(sources)}document.getElementById('parts').append(card)}
const canvas=document.getElementById('scene'),gl=canvas.getContext('webgl2',{antialias:true,alpha:false});
function fail(message){document.getElementById('error').textContent=message;document.getElementById('error').style.display='block';document.getElementById('status').textContent='三维预览未启动';window.__armViewer={ready:false,error:message}}
if(!gl){fail('浏览器未启用 WebGL 2。请使用启用硬件加速的新版本 Edge 或 Chrome 打开。')}else{try{start()}catch(error){fail('实际网格加载失败：'+error.message);console.error(error)}}
function start(){
const vertex=`#version 300 es
in vec3 aPosition;in vec3 aNormal;uniform mat4 uVP;uniform mat4 uModel;uniform mat3 uNormal;out vec3 vNormal;out vec3 vPosition;
void main(){vNormal=uNormal*aNormal;vPosition=(uModel*vec4(aPosition,1.0)).xyz;gl_Position=uVP*vec4(vPosition,1.0);}`;
const fragment=`#version 300 es
precision highp float;in vec3 vNormal;in vec3 vPosition;uniform vec3 uColor;uniform vec3 uEye;out vec4 frag;
void main(){vec3 n=normalize(vNormal);if(!gl_FrontFacing)n=-n;vec3 l=normalize(vec3(0.5,0.8,0.7));float light=0.38+0.52*max(dot(n,l),0.0)+0.10*max(dot(n,normalize(vec3(-0.6,0.2,-0.8))),0.0);vec3 v=normalize(uEye-vPosition);float spec=pow(max(dot(n,normalize(l+v)),0.0),45.0)*0.22;frag=vec4(pow(uColor*light+vec3(spec),vec3(1.0/2.2)),1.0);}`;
function shader(type,source){const result=gl.createShader(type);gl.shaderSource(result,source);gl.compileShader(result);if(!gl.getShaderParameter(result,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(result));return result}
const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,vertex));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));gl.useProgram(program);
const loc={position:gl.getAttribLocation(program,'aPosition'),normal:gl.getAttribLocation(program,'aNormal'),vp:gl.getUniformLocation(program,'uVP'),model:gl.getUniformLocation(program,'uModel'),normalMatrix:gl.getUniformLocation(program,'uNormal'),color:gl.getUniformLocation(program,'uColor'),eye:gl.getUniformLocation(program,'uEye')};
function decode64(value,Type){const raw=atob(value),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);return new Type(bytes.buffer)}
const meshes=geo.meshes.map(mesh=>{const vao=gl.createVertexArray();gl.bindVertexArray(vao);for(const [key,location]of [['positions',loc.position],['normals',loc.normal]]){const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,decode64(mesh[key],Float32Array),gl.STATIC_DRAW);gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,3,gl.FLOAT,false,0,0)}const ib=gl.createBuffer();gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,ib);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,decode64(mesh.indices,Uint32Array),gl.STATIC_DRAW);const corners=[];for(let i=0;i<8;i++)corners.push([0,1,2].map(c=>(i&(1<<c))?mesh.max[c]:mesh.min[c]));return{...mesh,vao,corners}});
const extent=Math.max(1,...geo.max.map((v,i)=>v-geo.min[i]));let yaw=0.65,pitch=0.34,zoom=1,displayDistance=extent*2.1,pan=[0,0,0],pointer=null;
function sub(a,b){return a.map((v,i)=>v-b[i])}function cross(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]}function dot(a,b){return a.reduce((s,v,i)=>s+v*b[i],0)}function unit(a){return a.map(v=>v/Math.hypot(...a))}
function perspective(fov,aspect,near,far){const f=1/Math.tan(fov/2),r=new Float64Array(16);r[0]=f/aspect;r[5]=f;r[10]=(far+near)/(near-far);r[11]=-1;r[14]=2*far*near/(near-far);return r}
function lookAt(eye,target){const z=unit(sub(eye,target)),x=unit(cross([0,1,0],z)),y=cross(z,x);return new Float64Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1])}
window.render=function(){const dpr=Math.min(devicePixelRatio||1,2),w=Math.max(1,Math.round(canvas.clientWidth*dpr)),h=Math.max(1,Math.round(canvas.clientHeight*dpr));if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}gl.viewport(0,0,w,h);gl.clearColor(0.035,0.071,0.115,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);const deltas=poseDeltas(data.links,angles,data.basis),visibleMeshes=meshes.filter(mesh=>visibility.get(mesh.part)),boundsMin=[Infinity,Infinity,Infinity],boundsMax=[-Infinity,-Infinity,-Infinity];for(const mesh of visibleMeshes){mesh.delta=deltas.get(partByName.get(mesh.part).link);for(const corner of mesh.corners){const p=point(mesh.delta,corner);for(let i=0;i<3;i++){boundsMin[i]=Math.min(boundsMin[i],p[i]);boundsMax[i]=Math.max(boundsMax[i],p[i])}}}if(!visibleMeshes.length){for(let i=0;i<3;i++){boundsMin[i]=geo.min[i];boundsMax[i]=geo.max[i]}}const target=boundsMin.map((v,i)=>(v+boundsMax[i])/2+pan[i]),radius=Math.max(0.1,Math.hypot(...boundsMax.map((v,i)=>v-boundsMin[i]))/2),aspect=w/h,halfAngle=Math.min(0.325,Math.atan(Math.tan(0.325)*aspect));displayDistance=radius/Math.sin(halfAngle)*1.12*zoom;const eye=[target[0]+displayDistance*Math.cos(pitch)*Math.sin(yaw),target[1]+displayDistance*Math.sin(pitch),target[2]+displayDistance*Math.cos(pitch)*Math.cos(yaw)],vp=multiply(perspective(0.65,aspect,Math.max(radius*0.001,0.01),displayDistance+radius*4+extent),lookAt(eye,target));gl.uniformMatrix4fv(loc.vp,false,new Float32Array(vp));gl.uniform3fv(loc.eye,new Float32Array(eye));for(const mesh of visibleMeshes){gl.uniformMatrix4fv(loc.model,false,new Float32Array(mesh.delta));gl.uniformMatrix3fv(loc.normalMatrix,false,normalMatrix(mesh.delta));gl.uniform3fv(loc.color,new Float32Array(mesh.rgba.slice(0,3)));gl.bindVertexArray(mesh.vao);gl.drawElements(gl.TRIANGLES,mesh.count,gl.UNSIGNED_INT,0)}const visibleParts=[...visibility].filter(p=>p[1]).map(p=>p[0]);document.getElementById('status').textContent=visibleParts.length+' / '+visibility.size+' 个零件 · '+geo.triangles.toLocaleString()+' 个三角形 · '+(poseSelect.value==='__custom__'?'手动姿态（路径未验证）':poseSelect.selectedOptions[0].textContent);window.__armViewer={ready:true,angles:{...angles},pose:poseSelect.value,partCount:visibility.size,visibleParts,triangles:geo.triangles,drawCalls:visibleMeshes.length,width:w,height:h,displayDistance,displayRadius:radius,zoom,linkDeltas:Object.fromEntries([...deltas].map(([name,matrix])=>[name,Array.from(matrix)])),boundsMin,boundsMax};};
canvas.addEventListener('contextmenu',e=>e.preventDefault());canvas.addEventListener('pointerdown',e=>{pointer={id:e.pointerId,x:e.clientX,y:e.clientY,pan:e.button===2||e.shiftKey};canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!pointer||pointer.id!==e.pointerId)return;const dx=e.clientX-pointer.x,dy=e.clientY-pointer.y;pointer.x=e.clientX;pointer.y=e.clientY;if(pointer.pan){const factor=displayDistance*0.0015;pan[0]-=dx*factor*Math.cos(yaw);pan[2]+=dx*factor*Math.sin(yaw);pan[1]+=dy*factor}else{yaw-=dx*0.008;pitch=Math.max(-1.48,Math.min(1.48,pitch+dy*0.008))}render()});function release(){pointer=null}canvas.addEventListener('pointerup',release);canvas.addEventListener('pointercancel',release);canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(0.25,Math.min(8,zoom*Math.exp(e.deltaY*0.001)));render()},{passive:false});
document.getElementById('reset').addEventListener('click',()=>{yaw=0.65;pitch=0.34;zoom=1;pan=[0,0,0];setPose('default')});document.getElementById('showAll').addEventListener('click',()=>{for(const name of visibility.keys())visibility.set(name,true);for(const check of checks)check.checked=true;render()});new ResizeObserver(render).observe(canvas);render();
}
</script></body></html>'''.replace("__MATRIX_JS__", _MATRIX_JS)
