export interface Project {id:string;name:string;description:string;version:number;updatedAt?:string;headRevisionId?:string;moduleHeads?:{mechanical?:string;firmware?:string;manufacturing?:string};specification:Record<string,any>;preferences:Record<string,any>}
export interface SymbolLibrary {id:string;sha256:string;sourceUri:string;sourceVersion:string;description:string;license:string;symbolCount:number;formatVersion:string;importedAt:string}
export interface Revision {id:string;projectId:string;createdAt:string;entrypoints:{schematic?:string;board?:string};manifest:{path:string;sha256:string;size:number}[];origin?:string;parentRevisionId?:string;message?:string;changes?:any[];validation?:{status:string;adopted?:boolean;checksPassed?:boolean;draftChecksPassed?:boolean;adoptionMode?:string;message?:string};pcbDesign?:{schematicRevisionId:string;handoffJobId:string;reviewJobId:string;stackup?:{name:string;nominalThickness:number;thicknessNote:string;copper:{name:string;thickness:number}[];source:{url:string;version:string;note:string}}};createdByJobId?:string;downstreamRequiresReview?:string[];connectivityIntent?:{mode:string;removeGroups:string[];addGroups:{reference:string;pin:string}[][]};symbolLibraries?:SymbolLibrary[];designSpecification?:{title:string;requirements:string;decisions:Record<string,unknown>}}
export interface Job {id:string;projectId:string;kind:string;status:string;phase:string;createdAt:string;startedAt?:string;finishedAt?:string;error?:string;result?:any;inputRevision?:string;cancelRequested:boolean}
export interface PcbDependency {revisionId:string;boardSha256:string;geometryIncluded:boolean;geometryJobId?:string;geometryStepSha256?:string;coverage?:{selectedComponents:number;totalComponents:number}}
export interface Revision {module?:string;mechanicalDesign?:{kind:string;pcbDependency?:PcbDependency;sources:any[];engineBundleHash:string}}
export interface Revision {firmwareDesign?:{profile:string;requirements:string;hardwareDependency:{revisionId:string;hardwareHash:string};[key:string]:any}}
export interface Revision {procurementDesign?:{mode:string;lineGroups:number;manufacturingDependency:{jobId:string;revisionId:string;packageSha256:string};[key:string]:any}}
export interface Artifact {id:string;projectId:string;jobId:string;revisionId:string;name:string;kind:string;mimeType:string;size:number;sha256:string;createdAt:string}
export interface Context {project:Project;revisions:Revision[];jobs:Job[];artifacts:Artifact[];sourceRegistry?:{sources:any[];total:number};capabilities:any;agentWorkflow?:import('./agent-workflow').AgentWorkflow;processReview?:import('./agent-workflow').ProcessReview}
export interface EventRecord {seq:number;projectId:string;jobId?:string;type:string;payload:any;createdAt:string}
export interface Context {productConcepts?:import('./product-concepts').ProductConcepts}
export interface Revision {_summary?:boolean;manifestFileCount?:number}
export interface Job {_summary?:boolean}

let sessionGeneration=0;
let sessionRenewal:Promise<any>|null=null;
const responseError=(result:any)=>new Error(result?.message || (Array.isArray(result?.detail)?result.detail.map((x:any)=>x.msg).join('；'):'操作未成功，请检查输入。'));

function renewSession():Promise<any>{
  if(sessionRenewal)return sessionRenewal;
  const pending=(async()=>{
    const response=await fetch('/api/session',{method:'POST'});
    const result=await response.json();
    if(!response.ok)throw responseError(result);
    sessionGeneration++;
    return result;
  })().finally(()=>{if(sessionRenewal===pending)sessionRenewal=null;});
  sessionRenewal=pending;
  return pending;
}

export async function api<T=any>(path:string,method='GET',body?:unknown,signal?:AbortSignal):Promise<T>{
  if(path==='session'&&method==='POST'&&body===undefined)return renewSession();
  // Freeze the original JSON before authentication recovery can yield.
  // Keep canonical source copy for reversible UI language switching. Direct
  // HTTP/MCP clients may request localized responses independently.
  const options:RequestInit={method,headers:{'Accept-Language':'zh-CN',...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal};
  const generation=sessionGeneration;
  let response=await fetch('/api/'+path,options);
  let result=await response.json();
  if(response.status===403&&result?.error==='access_denied'&&result?.reasonCode==='session_required'){
    if(generation===sessionGeneration)await renewSession();
    response=await fetch('/api/'+path,options);
    result=await response.json();
  }
  if(!response.ok)throw responseError(result);
  return result;
}
export const fileUrl=(artifact:Artifact)=>'/api/artifacts/'+encodeURIComponent(artifact.id)+'/file';
export const requestId=()=>crypto.randomUUID();
