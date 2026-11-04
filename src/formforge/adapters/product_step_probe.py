"""Independent frozen STEP read; does not rebuild or approve an assembly."""
import importlib.metadata
import json
from pathlib import Path
import sys


def main(request):
    import cadquery as cq
    rows=[]
    for item in request['parts']:
        shape=cq.importers.importStep(item['path']).val();solids=shape.Solids();box=shape.BoundingBox()
        closed=bool(solids)and all(s.isValid()and all(shell.Closed()for shell in s.Shells())for s in solids)
        rows.append(dict(id=item['id'],solidCount=len(solids),validClosedSolids=closed,volumeMm3=sum(s.Volume()for s in solids),
                         dimensionsMm=[box.xlen,box.ylen,box.zlen],passed=closed and(not item['singleSolid']or len(solids)==1)))
    return dict(parts=rows,checksPassed=bool(rows)and all(r['passed']for r in rows),
                runtime={n:importlib.metadata.version(n)for n in ('cadquery','cadquery-ocp')},
                scope='Independent solid readback of frozen outputs, not collision, fit, movement or manufacturing approval.')


if __name__=='__main__':
    result=main(json.loads(Path(sys.argv[1]).read_text(encoding='utf8')))
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
