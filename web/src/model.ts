import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';

export interface AssemblyPart {id:string;displayName?:string;nodeIndex:number;nodeName:string;explodeVectorCADmm:number[];volumeMm3:number;solidCount:number}
export interface AssemblyView {schemaVersion:number;units:string;glbSha256:string;coordinateFrame:{cad_to_gltf_matrix_column_major:number[];linear_determinant:number};parts:AssemblyPart[];linearMotionView?:{minimum:number;maximum:number;startLabel:string;endLabel:string;viewOnly:boolean}}

export class ModelViewer {
  private renderer:THREE.WebGLRenderer;
  private scene=new THREE.Scene();
  private camera=new THREE.PerspectiveCamera(38,1,0.001,100000);
  private controls:OrbitControls;
  private observer:ResizeObserver;
  private model?:THREE.Object3D;
  private loadIndex=0;
  private radius=1;
  private queued=false;
  private keyLight=new THREE.DirectionalLight(0xffffff,3.5);
  private shadowFloor=new THREE.Mesh(new THREE.PlaneGeometry(1,1),new THREE.ShadowMaterial({color:0x405344,opacity:.18}));
  private parts=new Map<string,{object:THREE.Object3D;position:THREE.Vector3;offset:THREE.Vector3}>();
  private originalMaterials=new Map<THREE.Mesh,THREE.Material|THREE.Material[]>();
  constructor(private host:HTMLElement){
    this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio,2));
    this.renderer.outputColorSpace=THREE.SRGBColorSpace;
    this.renderer.toneMapping=THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure=.9;
    this.renderer.shadowMap.enabled=true;
    this.renderer.shadowMap.type=THREE.PCFSoftShadowMap;
    this.scene.background=new THREE.Color(0xf2f5ef);
    host.append(this.renderer.domElement);
    const room=new RoomEnvironment(),lighting=new THREE.PMREMGenerator(this.renderer);
    this.scene.environment=lighting.fromScene(room,.04).texture;
    this.scene.environmentIntensity=.65;
    room.dispose();lighting.dispose();
    this.scene.add(new THREE.HemisphereLight(0xffffff,0x60776a,.5));
    this.keyLight.castShadow=true;
    this.keyLight.shadow.mapSize.set(2048,2048);
    this.keyLight.shadow.bias=-.0002;
    this.scene.add(this.keyLight,this.keyLight.target);
    this.shadowFloor.rotation.x=-Math.PI/2;
    this.shadowFloor.receiveShadow=true;
    this.shadowFloor.visible=false;
    this.scene.add(this.shadowFloor);
    this.controls=new OrbitControls(this.camera,this.renderer.domElement);
    this.controls.addEventListener('change',()=>this.draw());
    this.observer=new ResizeObserver(()=>{const w=host.clientWidth,h=host.clientHeight;if(w&&h){this.renderer.setSize(w,h);this.camera.aspect=w/h;this.camera.updateProjectionMatrix();this.draw();}});
    this.observer.observe(host);
  }
  clear(){
    this.clay(false);
    ++this.loadIndex;
    if(this.model){this.scene.remove(this.model);this.disposeObject(this.model);this.model=undefined;}
    this.shadowFloor.visible=false;
    this.parts.clear();this.draw();
  }
  async load(url:string,assembly?:AssemblyView){
    const revision=++this.loadIndex;
    const gltf=await new GLTFLoader().loadAsync(url);
    if(revision!==this.loadIndex){this.disposeObject(gltf.scene);return;}
    const parts=new Map<string,{object:THREE.Object3D;position:THREE.Vector3;offset:THREE.Vector3}>();
    if(assembly){
      try{
        if(assembly.units!=='mm'||assembly.coordinateFrame.linear_determinant<=0)throw new Error('模型坐标信息不完整');
        const matrix=assembly.coordinateFrame.cad_to_gltf_matrix_column_major;
        if(!Array.isArray(matrix)||matrix.length!==16||!matrix.every(Number.isFinite))throw new Error('模型坐标变换不正确');
        const linear=new THREE.Matrix3().setFromMatrix4(new THREE.Matrix4().fromArray(matrix));
        for(const part of assembly.parts){
          const object=await gltf.parser.getDependency('node',part.nodeIndex) as THREE.Object3D;
          let present=false;gltf.scene.traverse(node=>{if(node===object)present=true;});
          if(!present||parts.has(part.id)||part.explodeVectorCADmm.length!==3||!part.explodeVectorCADmm.every(Number.isFinite))throw new Error('零件与原生模型不匹配');
          parts.set(part.id,{object,position:object.position.clone(),offset:new THREE.Vector3(...part.explodeVectorCADmm as [number,number,number]).applyMatrix3(linear)});
        }
      }catch(error){this.disposeObject(gltf.scene);throw error;}
    }
    if(revision!==this.loadIndex){this.disposeObject(gltf.scene);return;}
    this.clay(false);
    if(this.model){this.scene.remove(this.model);this.disposeObject(this.model);}
    this.model=gltf.scene;this.parts=parts;
    this.model.traverse(node=>{if(node instanceof THREE.Mesh){node.castShadow=true;node.receiveShadow=true;}});
    const box=new THREE.Box3().setFromObject(this.model),center=box.getCenter(new THREE.Vector3());
    this.model.position.sub(center);this.radius=Math.max(box.getSize(new THREE.Vector3()).length()/2,.001);
    this.scene.add(this.model);this.reset();
  }
  reset(){
    const box=this.model?new THREE.Box3().setFromObject(this.model):new THREE.Box3();
    const center=box.isEmpty()?new THREE.Vector3():box.getCenter(new THREE.Vector3());
    if(!box.isEmpty())this.radius=Math.max(box.getSize(new THREE.Vector3()).length()/2,.001);
    if(!box.isEmpty()){
      this.shadowFloor.position.set(center.x,box.min.y-this.radius*.015,center.z);
      this.shadowFloor.scale.setScalar(this.radius*8);this.shadowFloor.visible=true;
      this.keyLight.position.copy(center).add(new THREE.Vector3(2,4,3).multiplyScalar(this.radius));
      this.keyLight.target.position.copy(center);
      const shadow=this.keyLight.shadow;shadow.camera.left=shadow.camera.bottom=-this.radius*1.8;
      shadow.camera.right=shadow.camera.top=this.radius*1.8;
      shadow.camera.near=this.radius*.1;shadow.camera.far=this.radius*12;
      shadow.normalBias=this.radius*.001;shadow.camera.updateProjectionMatrix();
    }
    this.camera.near=this.radius/1000;this.camera.far=this.radius*100;this.camera.position.set(this.radius*1.5,this.radius*1.7,this.radius*2.2).add(center);this.camera.updateProjectionMatrix();this.controls.target.copy(center);this.controls.update();this.draw();
  }
  explode(amount:number){
    if(!Number.isFinite(amount))return;
    for(const p of this.parts.values())p.object.position.copy(p.position);
    this.model?.updateMatrixWorld(true);
    const targets=Array.from(this.parts.values()).map(p=>({p,world:p.object.getWorldPosition(new THREE.Vector3()).addScaledVector(p.offset,amount)}));
    for(const {p,world} of targets)p.object.position.copy(p.object.parent?p.object.parent.worldToLocal(world):world);
    this.model?.updateMatrixWorld(true);this.draw();
  }
  showPart(id:string,visible:boolean){const p=this.parts.get(id);if(p){p.object.visible=visible;this.draw();}}
  cameraView(view:'iso'|'top'|'front'|'side'){
    if(view==='iso'){this.reset();return;}
    const directions={top:new THREE.Vector3(0,1,.0001),front:new THREE.Vector3(0,0,1),side:new THREE.Vector3(1,0,0)};
    this.camera.position.copy(this.controls.target).addScaledVector(directions[view].normalize(),this.radius*3.2);
    this.controls.update();this.draw();
  }
  wireframe(enabled:boolean){
    this.model?.traverse(node=>{if(node instanceof THREE.Mesh){
      const materials=Array.isArray(node.material)?node.material:[node.material];
      for(const material of materials)if('wireframe' in material){material.wireframe=enabled;material.needsUpdate=true;}
    }});this.draw();
  }
  clay(enabled:boolean){
    for(const [mesh,material] of this.originalMaterials){
      const current=Array.isArray(mesh.material)?mesh.material:[mesh.material];current.forEach(m=>m.dispose());mesh.material=material;
    }this.originalMaterials.clear();
    this.model?.traverse(node=>{
      if(node.userData.formforgeDecal)node.visible=!enabled;
      if(enabled&&node instanceof THREE.Mesh){
        let parent:THREE.Object3D|null=node;while(parent){if(parent.userData.formforgeDecal)return;parent=parent.parent;}
        this.originalMaterials.set(node,node.material);node.material=new THREE.MeshStandardMaterial({color:0xb8b8b8,roughness:.8,metalness:0});
      }
    });this.draw();
  }
  exportImage(name:string){
    this.renderer.render(this.scene,this.camera);
    const link=document.createElement('a');link.download=name;link.href=this.renderer.domElement.toDataURL('image/png');link.click();
  }
  zoom(factor:number){this.camera.position.sub(this.controls.target).multiplyScalar(1/factor).add(this.controls.target);this.controls.update();this.draw();}
  private draw(){if(this.queued)return;this.queued=true;requestAnimationFrame(()=>{this.queued=false;if(this.host.clientWidth&&this.host.clientHeight)this.renderer.render(this.scene,this.camera);});}
  private disposeObject(object:THREE.Object3D){object.traverse(node=>{if(node instanceof THREE.Mesh){node.geometry.dispose();const materials=Array.isArray(node.material)?node.material:[node.material];materials.forEach(material=>{for(const value of Object.values(material))if(value instanceof THREE.Texture)value.dispose();material.dispose();});}});}
}
