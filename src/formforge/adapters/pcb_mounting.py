"""Explicit, frozen pure NPTH features; board_only alone is never electrical authority."""
import hashlib,json,math,re,shutil
from pathlib import Path
from .native_syntax import Node,parse
from .importer import digest,reparse,within
from ..store import DomainError

def fail(message):raise DomainError('invalid_input',message)

def canonical(node):
    if isinstance(node,Node):return [canonical(v) for v in node.values if not isinstance(v,Node) or v.kind!='uuid']
    try:return float(node)
    except (ValueError,TypeError):return node

def shape(node,instance=False):
    allowed={'version','generator','layer','descr','tags','property','attr','duplicate_pad_numbers_are_jumpers','fp_circle','fp_text','pad','embedded_fonts'}
    if instance:allowed|={'at','uuid','path','sheetname','sheetfile'}
    if node.kind!='footprint' or any(n.kind not in allowed for n in node.values if isinstance(n,Node)):fail('机械孔只接受无模型、无铜图形的单个纯NPTH圆孔。')
    if node.one('layer').values[1]!='F.Cu':fail('机械孔仅支持正面原点圆孔。')
    for n in node.children('property')+node.children('fp_circle')+node.children('fp_text'):
        if n.one('layer').values[1] not in {'F.SilkS','F.Fab','F.CrtYd','Cmts.User'}:fail('机械孔图形/字段不能位于铜、板框或其他未支持层。')
    pads=node.children('pad')
    if len(pads)!=1:fail('机械孔必须只有一个无号NPTH焊盘。')
    p=pads[0]
    if p.values[1:4]!=['','np_thru_hole','circle'] or any(n.kind not in {'at','size','drill','layers','uuid'} for n in p.values if isinstance(n,Node)):fail('不能把电气焊盘、网络或孔偏移伪装为机械孔。')
    at=list(map(float,p.one('at').values[1:]));size=list(map(float,p.one('size').values[1:]));drill=p.one('drill').values[1:]
    if len(drill)!=1 or not .8<=float(drill[0])<=10 or len(size)!=2 or any(abs(v-float(drill[0]))>1e-6 for v in size) or any(abs(v)>1e-6 for v in at) or p.one('layers').values[1:]!=['*.Cu','*.Mask']:fail('只支持原点、同尺寸、无偏移的圆形NPTH孔。')
    if instance and not {'board_only','exclude_from_pos_files','exclude_from_bom'}<=set(node.one('attr').values[1:]):fail('机械孔必须明确排除原理图、BOM和贴装坐标。')
    raw=[canonical(n) for kind in ['pad','fp_circle','fp_text'] for n in node.children(kind)]
    return dict(drill=float(drill[0]),geometryHash=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest())

def freeze(source,record,expected_drill):
    path=Path(record['path'])
    if not path.is_absolute() or any(p.exists() and reparse(p) for p in [path,*path.parents]) or path.suffix.lower()!='.kicad_mod' or not path.is_file() or path.stat().st_size>256*1024:fail('机械孔来源须为无链接、至多256KB的绝对.kicad_mod路径。')
    if digest(path)!=record['sha256']:raise DomainError('input_changed','机械孔来源SHA不符。',409)
    node=parse(path.read_text(encoding='utf-8-sig'));proof=shape(node);name=node.values[1]
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,150}',name) or abs(proof['drill']-expected_drill)>1e-6:fail('机械孔名称或明确孔径与来源不符。')
    lib='FFMH_'+record['sha256'][:16];relative=f'formforge-footprints/{lib}.pretty/{name}.kicad_mod';dest=within(source,relative);dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists() and digest(dest)!=record['sha256']:fail('已冻结机械孔来源变化。')
    shutil.copyfile(path,dest)
    if digest(dest)!=record['sha256'] or digest(path)!=record['sha256']:raise DomainError('input_changed','机械孔来源复制时变化。',409)
    return dict(source={**record,'originalPath':str(path),'path':relative,'trust':'caller-declared; content-sha256-verified'},footprint=lib+':'+name,**proof)

def append_libraries(source,features):
    path=source/'fp-lib-table';tree=parse(path.read_text(encoding='utf8'));rows=[{k:n.one(k).values[1] for k in ['name','type','uri','options','descr']} for n in tree.children('lib')];names={n['name'] for n in rows}
    for f in features:
        lib=f['footprint'].split(':')[0]
        if lib in names:continue
        names.add(lib);rows.append(dict(name=lib,type='KiCad',uri='${KIPRJMOD}/formforge-footprints/'+lib+'.pretty',options='',descr='FormForge frozen pure NPTH source'))
    # KiCad10's library-table reader requires whitespace between tokens. Its
    # tolerant board parser accepting `(lib(name ...)(type ...))` is not evidence
    # that the separate table reader loaded it: it can silently fall back global.
    text='(fp_lib_table\n (version 7)\n'+''.join(' (lib '+' '.join('('+k+' '+json.dumps(v)+')' for k,v in row.items())+')\n' for row in rows)+')\n'
    path.write_text(text,encoding='utf8')

def verify(tree,basis,source):
    nodes={f.one('uuid').values[1]:f for f in tree.children('footprint')};records=basis.get('mountingFeatures',[]);seen=set();result=[]
    for r in records:
        if r['id'] in seen or r['id'] not in nodes:fail('已声明机械孔缺失或UUID重复。')
        seen.add(r['id']);node=nodes[r['id']];proof=shape(node,True);p=node.one('pad');at=list(map(float,node.one('at').values[1:]));ref=next(n.values[2] for n in node.children('property') if n.values[1]=='Reference')
        frozen=within(source,r['source']['path'])
        if digest(frozen)!=r['source']['sha256'] or shape(parse(frozen.read_text(encoding='utf-8-sig')))!=dict(drill=r['drill'],geometryHash=r['geometryHash']):fail('机械孔冻结来源与契约不同。')
        if proof!=dict(drill=r['drill'],geometryHash=r['geometryHash']) or node.values[1]!=r['footprint'] or ref!=r['reference'] or p.one('uuid').values[1]!=r['padId'] or math.dist(at[:2],r['at'])>1.1e-6 or abs(((at[2] if len(at)>2 else 0)-r['rotation']+180)%360-180)>1e-6:fail('原生机械孔与完整保存目标不同。')
        result.append({**r,'kind':'mounting-hole','plated':False,'electrical':False})
    return result
