"""Data-only references for the CAD boundary; usable by the isolated worker."""
from pathlib import PurePosixPath

KINDS=('enclosure','panel','u_bracket','assembly','rotary_assembly','articulated_arm','feature_model')


def feature_pcba_binding(design, part_name, step_sha, sources):
    """Trace one intact coloured STEP through rigid transforms to one output."""
    if not isinstance(design.get('features'),list) or not isinstance(design.get('parts'),list) or any(not isinstance(r,dict) for r in design['features']+design['parts']):
        raise ValueError('PCBA binding requires explicit feature and output records')
    features={f['id']:f for f in design.get('features',[])}
    if len(features)!=len(design['features']):raise ValueError('Duplicate feature id in PCBA binding')
    outputs=design.get('parts',[])
    matches=[p for p in outputs if p.get('name')==part_name]
    if len(matches)!=1:raise ValueError('pcbPart must identify one explicit feature_model output part')
    def chain(part):
        result=[];seen=set();current=part.get('feature')
        while current in features and current not in seen:
            seen.add(current);f=features[current];result.append(f)
            if f.get('op')!='transform':return result
            current=f.get('source')
        raise ValueError('PCBA feature chain is missing or cyclic')
    path=chain(matches[0]);leaf=path[-1]
    if leaf.get('op')!='step' or leaf.get('step_sha256')!=step_sha or leaf.get('preserve_colors',True) is not True:
        raise ValueError('Bound PCBA must preserve the complete same-SHA coloured STEP through rigid transforms only')
    source=sources.get(leaf.get('step'))
    if not source or source['sha256']!=step_sha:raise ValueError('PCBA STEP must match its frozen native geometry source')
    # A duplicate STEP feature is also a second assembly, even with a new id.
    def contains_pcba(ref,seen=None):
        seen=set() if seen is None else seen
        if ref in seen:return False
        seen.add(ref);f=features.get(ref,{})
        if f.get('op')=='step' and f.get('step_sha256')==step_sha:return True
        return any(contains_pcba(r,seen) for r in [*f.get('inputs',[]),*([f['source']] if 'source' in f else [])])
    if any(p is not matches[0] and contains_pcba(p.get('feature')) for p in outputs):
        raise ValueError('The bound PCBA cannot be duplicated, split or fused into another output part')
    return dict(part=part_name,sourceFeature=leaf['id'],step=leaf['step'],stepSha256=step_sha,
                transformFeatures=[f['id'] for f in reversed(path[:-1])],
                scope='One complete source assembly through rigid transforms; mounting, collision and engineering checks remain separate.')

def references(value):
    found=[]
    def walk(item):
        if isinstance(item,dict):
            for key,v in item.items():
                if key=='step' and v is not None:
                    if not isinstance(v,str):raise ValueError('STEP reference must be a frozen relative filename')
                    found.append((v,item.get('step_sha256')))
                elif key=='image' and v is not None:
                    if not isinstance(v,str):raise ValueError('Image reference must be a frozen relative filename')
                    found.append((v,item.get('image_sha256')))
                elif key=='evidence_files':
                    if not isinstance(v,list) or any(not isinstance(p,str) for p in v):raise ValueError('Evidence references must be frozen filenames')
                    found.extend((p,None) for p in v)
                else:walk(v)
        elif isinstance(item,list):
            for child in item:walk(child)
    walk(value);return found

def validate_references(design,files):
    refs=references(design)
    for path,declared_hash in refs:
        p=PurePosixPath(path)
        if '\\' in path or p.is_absolute() or len(p.parts)!=2 or p.parts[0]!='inputs' or any(s in ('.','..') for s in p.parts) or str(p)!=path or path not in files:
            raise ValueError('Mechanical file references must exactly match declared frozen inputs: '+path)
        if declared_hash is not None and declared_hash!=files[path]:raise ValueError('File reference SHA differs from declared frozen source: '+path)
    if {p for p,_ in refs}!=set(files):raise ValueError('Every source must be used by an explicit STEP, image or evidence reference')
