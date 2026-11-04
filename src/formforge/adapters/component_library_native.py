"""Isolated native readers. Requests are frozen task-local files, never code."""
import hashlib,json,math,re,sys
from pathlib import Path


def run(request):
    if request['kind']=='footprint':
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        from native_asserts import configure_native_asserts
        configure_native_asserts('exception')
        import pcbnew as pcb
        if not pcb.GetBuildVersion().startswith('10.'):raise ValueError('KiCad10 required')
        path=Path(request['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=request['sha256']:raise ValueError('Footprint bytes changed')
        fp=pcb.FootprintLoad(str(path.parent),path.stem)
        if fp is None:raise ValueError('Native footprint load failed')
        pads=[]
        for p in fp.Pads():
            at=p.GetPosition();size=p.GetSize();drill=p.GetDrillSize()
            pads.append(dict(number=p.GetNumber(),atMm=[pcb.ToMM(at.x),pcb.ToMM(at.y)],
                sizeMm=[pcb.ToMM(size.x),pcb.ToMM(size.y)],drillMm=[pcb.ToMM(drill.x),pcb.ToMM(drill.y)],attribute=int(p.GetAttribute())))
        return dict(engine=pcb.GetBuildVersion(),pads=pads,electricalPadNumbers=sorted({p['number'] for p in pads if p['number']}))
    import cadquery as cq
    import importlib.metadata
    versions={k:importlib.metadata.version(k) for k in ['cadquery','cadquery-ocp']}
    if versions!={'cadquery':'2.6.1','cadquery-ocp':'7.8.1.1.post1'}:raise ValueError('Locked CAD runtime required')
    models=[]
    for source in request['models']:
        path=Path(source['path']);raw=path.read_bytes()
        if len(raw)>64*1024*1024 or hashlib.sha256(raw).hexdigest()!=source['sha256']:raise ValueError('Model bytes changed')
        text=raw.decode('ascii');units=re.findall(r'\bLENGTH_UNIT\s*\(\s*\).*?;',text,re.S)
        if not units or 'CONVERSION_BASED_UNIT' in text or any(not re.search(r'SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.\s*\)',u) for u in units):raise ValueError('Explicit millimetre STEP required')
        shape=cq.importers.importStep(str(path)).val();solids=shape.Solids();box=shape.BoundingBox()
        if not solids or not shape.isValid() or not all(s.Closed() for s in shape.Shells()) or shape.Volume()<=0:raise ValueError('Valid closed solid model required')
        dimensions=[box.xlen,box.ylen,box.zlen]
        if not all(math.isfinite(x) and 0<x<20000 for x in dimensions):raise ValueError('Invalid model extent')
        models.append(dict(sha256=source['sha256'],units='mm',solidCount=len(solids),validClosedSolids=True,
            dimensionsMm=dimensions,boundsMm=dict(min=[box.xmin,box.ymin,box.zmin],max=[box.xmax,box.ymax,box.zmax])))
    return dict(runtime=versions,models=models)


if __name__=='__main__':
    request=json.loads(Path(sys.argv[1]).read_text('utf8'))
    result=run(request)
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
