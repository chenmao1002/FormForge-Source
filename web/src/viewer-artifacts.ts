import type {Artifact} from './api';

export type PcbViewMode='2d'|'3d';
export const isModelArtifact=(artifact:Artifact)=>['board_model','pcb_geometry_model','mechanical_model','mechanical_insertion_model'].includes(artifact.kind);
export const hasAssemblyControls=(artifact:Artifact)=>['mechanical_model','mechanical_insertion_model','pcb_geometry_model'].includes(artifact.kind);

/** Only show native outputs bound to the revision and workspace being viewed. */
export function viewerArtifacts(artifacts:Artifact[],view:string,revisionId:string,pcbMode:PcbViewMode):Artifact[]{
  const kinds=view==='schematic'?['schematic_preview','baseline_schematic_preview']:view==='mechanical'?['mechanical_model','mechanical_insertion_model']:view==='pcb'?(pcbMode==='3d'?['pcb_geometry_model','board_model']:['pcb_preview','pcb_reference_preview']):[];
  return artifacts.filter(a=>a.revisionId===revisionId&&kinds.includes(a.kind))
    .sort((a,b)=>view==='mechanical'?0:kinds.indexOf(a.kind)-kinds.indexOf(b.kind));
}
