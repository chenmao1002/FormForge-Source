"""Read frozen STEP solids and export review STL using the locked CAD runtime."""
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import sys


def measure(path,expected,export_stl=False):
    import cadquery as cq
    raw=path.read_bytes()
    if len(raw)>64*1024*1024 or hashlib.sha256(raw).hexdigest()!=expected['stepSha256']:raise ValueError('STEP bytes changed')
    text=raw.decode('ascii')
    units=re.findall(r'\bLENGTH_UNIT\s*\(\s*\).*?;',text,re.S)
    if not units or any(not re.search(r'SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.\s*\)',u) for u in units) or 'CONVERSION_BASED_UNIT' in text:
        raise ValueError('Only explicit millimetre STEP is supported')
    shape=cq.importers.importStep(str(path)).val();solids=shape.Solids()
    if len(solids)!=1 or not shape.isValid() or not all(s.Closed() for s in shape.Shells()):raise ValueError('Fabricated part must be one valid closed solid')
    volume=shape.Volume();center=shape.Center();box=shape.BoundingBox()
    measured=dict(volumeMm3=volume,solidCount=len(solids),centroidCADmm=[center.x,center.y,center.z],
        boundsCADmm=dict(min=[box.xmin,box.ymin,box.zmin],max=[box.xmax,box.ymax,box.zmax]))
    json.dumps(measured,allow_nan=False)
    if volume<=0 or any(v<=0 for v in [box.xlen,box.ylen,box.zlen]):raise ValueError('STEP has no positive solid volume or extent')
    source=expected['part']
    checks=dict(validClosedSingleSolid=True,millimetreUnit=True,
        volumeMatches=abs(volume-source['volumeMm3'])<=max(.001,source['volumeMm3']*1e-6),
        centroidMatches=math.dist(measured['centroidCADmm'],source['centroidCADmm'])<=.001,
        boundsMatch=all(abs(a-b)<=.01 for k in ['min','max'] for a,b in zip(measured['boundsCADmm'][k],source['boundsCADmm'][k])))
    result=dict(partId=source['id'],stepSha256=expected['stepSha256'],measured=measured,checks=checks,passed=all(checks.values()),
        dimensionsMm=[box.xlen,box.ylen,box.zlen],units='mm')
    if export_stl:
        stl=path.with_suffix('.stl')
        cq.exporters.export(shape,str(stl),exportType='STL',tolerance=.04,angularTolerance=.1,opt={'ascii':False})
        result['stl']=dict(path=str(stl),sha256=hashlib.sha256(stl.read_bytes()).hexdigest(),
            toleranceMm=.04,angularToleranceRadians=.1,format='binary-STL',units='mm')
    return result


def main(request):
    versions={name:importlib.metadata.version(name) for name in ['cadquery','cadquery-ocp']}
    if versions!={'cadquery':'2.6.1','cadquery-ocp':'7.8.1.1.post1'}:raise ValueError('Structural CAD runtime differs from the locked version')
    root=Path(request['root']).resolve();parts=[]
    for row in request['parts']:
        path=(root/row['path']).resolve()
        if not path.is_relative_to(root):raise ValueError('STEP path outside the frozen task')
        part=measure(path,row,export_stl=request.get('exportStl',False))
        if 'stl' in part:part['stl']['path']=Path(part['stl']['path']).relative_to(root).as_posix()
        parts.append(part)
    return dict(parts=parts,checksPassed=bool(parts) and all(p['passed'] for p in parts),runtime=versions,
        tolerances=dict(volumeRelative=1e-6,volumeAbsoluteMm3=.001,centroidMm=.001,boundsMm=.01),
        topologyEquivalenceVerified=False,manufacturingApproved=False)


if __name__=='__main__':
    result=main(json.loads(Path(sys.argv[1]).read_text('utf8')))
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
