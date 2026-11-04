"""Real CAM geometry; profile centerlines, curved slots and explicit error bounds."""
import hashlib
import json
import math
from html import escape
import xml.etree.ElementTree as ET

from gerbonara.graphic_objects import Arc, Line, Region, Flash
from gerbonara.utils import MM
from shapely import set_precision
from shapely.geometry import LineString, Point, Polygon, GeometryCollection
from shapely.ops import linemerge, unary_union
from shapely.strtree import STRtree

ARC_ERROR_MM = .005
GRID_MM = .000001
MAX_PROFILE_OBJECTS = 5000


def mm(value,unit):
    value=float(MM(value,unit))
    if not math.isfinite(value) or abs(value)>100000:
        raise ValueError('Non-finite or unsupported coordinate extent.')
    return value


def object_path(obj):
    if isinstance(obj,Line):
        return [(mm(obj.x1,obj.unit),mm(obj.y1,obj.unit)),(mm(obj.x2,obj.unit),mm(obj.y2,obj.unit))]
    if isinstance(obj,Arc):
        result=[]
        for segment in obj.approximate(max_error=ARC_ERROR_MM,unit=MM):
            path=object_path(segment)
            if not result:result.append(path[0])
            result.append(path[1])
            if len(result)>100000:raise ValueError('Arc approximation exceeds its point budget.')
        return result
    raise ValueError('Unsupported profile/slot primitive: '+type(obj).__name__)


def centerline_bounds(obj):
    points=object_path(obj)
    if isinstance(obj,Arc):
        cx,cy=(mm(v,obj.unit) for v in obj.center)
        x0,y0=points[0];x1,y1=points[-1]
        radius=math.hypot(x0-cx,y0-cy)
        if abs(radius-math.hypot(x1-cx,y1-cy))>max(GRID_MM,radius*1e-6):
            raise ValueError('Profile arc has inconsistent endpoint radii.')
        start=math.atan2(y0-cy,x0-cx);end=math.atan2(y1-cy,x1-cx)
        direction=-1 if obj.clockwise else 1
        sweep=((end-start)*direction)%(2*math.pi)
        if math.dist(points[0],points[-1])<=GRID_MM:sweep=2*math.pi
        for a in [0,math.pi/2,math.pi,3*math.pi/2]:
            if ((a-start)*direction)%(2*math.pi)<=sweep+1e-12:
                points.append((cx+radius*math.cos(a),cy+radius*math.sin(a)))
    return min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)


def region_polygon(obj):
    points=[]
    for edge in obj.outline_objects():
        part=object_path(edge)
        if points and math.dist(points[-1],part[0])>GRID_MM:
            raise ValueError('Region contour has a gap.')
        points.extend(part if not points else part[1:])
    if len(points)<4 or math.dist(points[0],points[-1])>GRID_MM:
        raise ValueError('Region contour is not closed.')
    points[-1]=points[0]
    polygon=Polygon(points)
    if not polygon.is_valid or polygon.area<=GRID_MM**2:
        raise ValueError('Region contour is self-intersecting or empty.')
    return polygon


def profile_geometry(doc):
    if not doc.objects or len(doc.objects)>MAX_PROFILE_OBJECTS:
        raise ValueError('Profile must contain 1..5000 objects.')
    regions=[obj for obj in doc.objects if isinstance(obj,Region)]
    if regions:
        if len(regions)!=len(doc.objects):raise ValueError('Mixed filled-region and stroked profile is ambiguous.')
        board=GeometryCollection()
        for obj in regions:
            polygon=region_polygon(obj)
            board=board.union(polygon) if obj.polarity_dark else board.difference(polygon)
    else:
        lines=[];endpoint_degrees={}
        for obj in doc.objects:
            if not obj.polarity_dark:raise ValueError('Clear-polarity profile strokes have no unambiguous centerline boundary.')
            points=object_path(obj)
            line=set_precision(LineString(points),GRID_MM)
            if line.is_empty or line.length<=GRID_MM or not line.is_simple:
                raise ValueError('Degenerate or self-intersecting profile stroke.')
            lines.append(line)
            for end in [line.coords[0],line.coords[-1]]:
                key=tuple(round(float(v)/GRID_MM) for v in end)
                endpoint_degrees[key]=endpoint_degrees.get(key,0)+1
        if any(n!=2 for n in endpoint_degrees.values()):raise ValueError('Open or branching profile centerline.')
        tree=STRtree(lines);queries=0
        for i,line in enumerate(lines):
            endpoints={tuple(line.coords[0]),tuple(line.coords[-1])}
            for index in tree.query(line):
                j=int(index)
                if j<=i:continue
                queries+=1
                if queries>100000:raise ValueError('Profile intersection budget exceeded.')
                other=lines[j];hit=line.intersection(other)
                if hit.is_empty:continue
                other_ends={tuple(other.coords[0]),tuple(other.coords[-1])}
                points=[hit] if hit.geom_type=='Point' else list(hit.geoms) if hit.geom_type=='MultiPoint' else []
                if not points or any(tuple(p.coords[0]) not in endpoints & other_ends for p in points):
                    raise ValueError('Profile strokes cross, overlap, or touch away from shared endpoints.')
        merged=unary_union(lines)
        if merged.geom_type!='LineString':merged=linemerge(merged)
        rings=list(merged.geoms) if merged.geom_type=='MultiLineString' else [merged]
        board=GeometryCollection()
        for ring in rings:
            if not ring.is_ring:raise ValueError('Profile centerline does not form closed rings.')
            polygon=Polygon(ring)
            if not polygon.is_valid or polygon.area<=GRID_MM**2:raise ValueError('Invalid profile loop.')
            # Nested closed boundaries alternate board material/cutout; union would fill holes.
            board=board.symmetric_difference(polygon)
    if board.is_empty or not board.is_valid or board.geom_type not in {'Polygon','MultiPolygon'}:
        raise ValueError('Profile does not define valid board material.')
    x0,y0,x1,y1=board.bounds
    if not all(math.isfinite(v) for v in board.bounds) or max(x1-x0,y1-y0)>10000:
        raise ValueError('Unsupported profile dimensions.')
    polygons=list(board.geoms) if board.geom_type=='MultiPolygon' else [board]
    if not regions:
        exact=[centerline_bounds(obj) for obj in doc.objects]
        x0=min(b[0] for b in exact);y0=min(b[1] for b in exact);x1=max(b[2] for b in exact);y1=max(b[3] for b in exact)
    return board,dict(boundsMm=[x0,y0,x1,y1],widthMm=x1-x0,heightMm=y1-y0,areaMm2=board.area,
        boardIslands=len(polygons),cutouts=sum(len(p.interiors) for p in polygons),dimensionBasis='profile-centerlines-or-explicit-filled-region',
        arcApproximationMaxErrorMm=ARC_ERROR_MM,endpointGridMm=GRID_MM,closed=True,selfIntersections=False)


def drill_features(doc,layer,board):
    rows=[];seen=set();problems=[]
    for index,obj in enumerate(doc.objects):
        tool=getattr(obj,'aperture',None)
        if tool is None or not hasattr(tool,'diameter'):
            raise ValueError('Drill aperture has no circular tool diameter.')
        diameter=mm(tool.diameter,tool.unit)
        if diameter<=0:raise ValueError('Non-positive drill tool diameter.')
        if isinstance(obj,Flash):
            points=[(mm(obj.x,obj.unit),mm(obj.y,obj.unit))];center=Point(points[0]);kind='hole'
        elif isinstance(obj,(Line,Arc)):
            points=object_path(obj);center=LineString(points);kind='curved-slot' if isinstance(obj,Arc) else 'slot'
        else:raise ValueError('Unsupported drill object: '+type(obj).__name__)
        actual=getattr(obj,'plated',None)
        if actual is not None and bool(actual)!=(layer['plating']=='plated'):
            problems.append(dict(code='drill-plating',message='Tool plating differs from file declaration.',featureIndex=index))
        geometry=dict(kind=kind,pointsMm=points,diameterMm=diameter,plating=layer['plating'])
        key=hashlib.sha256(json.dumps(geometry,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if key in seen:problems.append(dict(code='duplicate-drill',message='Duplicate drill geometry.',featureIndex=index))
        seen.add(key)
        clearance=float(board.boundary.distance(center))-diameter/2 if board is not None else None
        inside=bool(board.covers(center)) if board is not None else False
        if board is not None and (not inside or clearance < -GRID_MM):
            problems.append(dict(code='drill-outside-profile',message='Drill or slot extends outside board material.',featureIndex=index))
        elif board is not None and clearance<2*ARC_ERROR_MM:
            problems.append(dict(code='drill-edge-uncertainty',message='Hole is too close to an edge for this geometric error bound.',featureIndex=index))
        rows.append(dict(index=index,featureHash=key,**geometry,edgeClearanceMm=clearance,
                         conservativeEdgeClearanceMm=None if clearance is None else clearance-2*ARC_ERROR_MM))
    return rows,problems


def board_path(board):
    polygons=list(board.geoms) if board.geom_type=='MultiPolygon' else [board]
    commands=[]
    for polygon in polygons:
        for ring in [polygon.exterior,*polygon.interiors]:
            points=list(ring.coords);commands.append('M '+' L '.join(f'{x:.6f} {y:.6f}' for x,y in points)+' Z')
    return ' '.join(commands)


def safe_svg(text):
    root=ET.fromstring(text)
    tags={'svg','g','path','rect','circle','ellipse','polygon','polyline','line','defs','mask','text','title'}
    attributes={'width','height','viewBox','x','y','x1','y1','x2','y2','cx','cy','r','rx','ry','d','points','transform',
                'fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','fill-rule','opacity','fill-opacity','stroke-opacity',
                'mask','maskUnits','id','font-size','font-family','text-anchor','mask-type'}
    for element in root.iter():
        if element.tag.rsplit('}',1)[-1] not in tags:raise ValueError('Unexpected SVG element.')
        for key,value in element.attrib.items():
            if key.rsplit('}',1)[-1] not in attributes:raise ValueError('Unexpected SVG attribute.')
            if any(v in value.casefold() for v in ['javascript:','data:','http:','https:','file:']):raise ValueError('External SVG reference.')
            if 'url(' in value and not (key=='mask' and re_mask(value)):
                raise ValueError('SVG URL is not a local generated mask.')
    return text


def re_mask(value):
    import re
    return re.fullmatch(r'url\(#layer-[0-9]+\)',value) is not None


def render_svg(layers,board,placements,title,selected=None):
    bounds=[]
    if board is not None:bounds.append(board.bounds)
    for layer,doc in layers:
        bbox=doc.bounding_box(MM)
        if bbox:bounds.append((*bbox[0],*bbox[1]))
    if not bounds:raise ValueError('There is no renderable geometry.')
    x0=min(b[0] for b in bounds)-2;y0=min(b[1] for b in bounds)-2
    x1=max(b[2] for b in bounds)+2;y1=max(b[3] for b in bounds)+2
    if not all(math.isfinite(v) for v in [x0,y0,x1,y1]) or max(x1-x0,y1-y0)>10000:
        raise ValueError('CAM render bounds are invalid or excessive.')
    width=x1-x0;height=y1-y0
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.6f}mm" height="{height:.6f}mm" viewBox="{x0:.6f} {-y1:.6f} {width:.6f} {height:.6f}">',
           '<title>'+escape(title)+'</title>',f'<rect x="{x0:.6f}" y="{-y1:.6f}" width="{width:.6f}" height="{height:.6f}" fill="#142d32"/>',
           '<g transform="scale(1 -1)">']
    if board is not None:
        parts.append(f'<path d="{board_path(board)}" fill="#24554e" fill-rule="evenodd"/>')
    for index,(layer,doc) in enumerate(layers):
        if selected is not None and layer['path']!=selected:continue
        if selected is None and not ((layer['role']=='copper' and layer['copperLayer']==1) or (layer['role']=='legend' and layer['side']=='top') or layer['role']=='drill'):
            continue
        color={'copper':'#d7a452','mask':'#79bcff','paste':'#d1d8d8','legend':'#ffffff','profile':'#ff90df','drill':'#142d32'}[layer['role']]
        pieces=[];size=0
        for obj in doc.svg_objects(svg_unit=MM,fg='#ffffff',bg='#000000'):
            piece=str(obj);size+=len(piece.encode('utf8'))
            if size>8*1024*1024:raise ValueError('SVG layer exceeds 8MiB.')
            pieces.append(piece)
        objects=''.join(pieces)
        parts.extend([f'<defs><mask id="layer-{index}" maskUnits="userSpaceOnUse" mask-type="luminance" x="{x0:.6f}" y="{y0:.6f}" width="{width:.6f}" height="{height:.6f}">',
            f'<rect x="{x0:.6f}" y="{y0:.6f}" width="{width:.6f}" height="{height:.6f}" fill="#000000"/>',objects,'</mask></defs>',
            f'<rect x="{x0:.6f}" y="{y0:.6f}" width="{width:.6f}" height="{height:.6f}" fill="{color}" mask="url(#layer-{index})"/>'])
    if board is not None:parts.append(f'<path d="{board_path(board)}" fill="none" stroke="#a8d5ce" stroke-width="0.08"/>')
    for row in placements:
        if not row['selected']:continue
        x,y,angle=row['xMm'],row['yMm'],math.radians(row['rotationDegrees'])
        color='#82d8ff' if row['side']=='top' else '#ff9ada'
        parts.extend([f'<circle cx="{x:.6f}" cy="{y:.6f}" r="0.32" fill="none" stroke="{color}" stroke-width="0.1"/>',
                      f'<line x1="{x:.6f}" y1="{y:.6f}" x2="{x+math.cos(angle):.6f}" y2="{y+math.sin(angle):.6f}" stroke="{color}" stroke-width="0.12"/>'])
    parts.append('</g>')
    for row in placements:
        if row['selected']:
            parts.append(f'<text x="{row["xMm"]+.45:.6f}" y="{-row["yMm"]-.45:.6f}" fill="#e9ffff" font-size="0.9" font-family="sans-serif">{escape(row["reference"])}</text>')
    parts.append('</svg>')
    result=''.join(parts)
    if len(result.encode('utf8'))>16*1024*1024:raise ValueError('Combined SVG exceeds 16MiB.')
    return safe_svg(result)
