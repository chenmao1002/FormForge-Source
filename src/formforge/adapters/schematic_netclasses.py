"""Retain native resolved netclasses across single-source rebuilds."""
import json
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from .schematic import load_pages,properties,location,object_id,safe_string,fail
from .topology import symbol_pins
from .schematic_cleanup import components


def constituents(value,names):
    # KiCad 10 joins constituent names with a literal comma, without escaping.
    # Match against actual project names; a comma may itself belong to a name.
    @lru_cache(None)
    def parse_at(pos):
        choices=[]
        for name in sorted(names):
            end=pos+len(name)
            if not value.startswith(name,pos):continue
            if end==len(value):choices.append((name,))
            elif value[end]==',':
                choices.extend((name,*rest) for rest in parse_at(end+1) if name not in rest)
            if len(choices)>1:return tuple(choices[:2])
        return tuple(choices)
    found=parse_at(0)
    if len(found)!=1:fail('无法唯一解析原生组合网类 '+value+'；请保留原层次整理，不能猜测网类成员。')
    return list(found[0])


def capture(source,entry,xml):
    project=source/Path(entry).with_suffix('.kicad_pro');names={'Default'}
    if project.is_file():
        settings=json.loads(project.read_text('utf-8-sig'))
        names.update(str(c['name']) for c in settings.get('net_settings',{}).get('classes',[]) if c.get('name'))
    rows=[]
    for net in ET.parse(xml).findall('nets/net'):
        resolved=net.get('class')
        if resolved is None:fail('原生网表缺少网类结果，不能保真重建。')
        rows.append(dict(name=net.get('name'),resolved=resolved,classes=constituents(resolved,names),
            pins=sorted([p.get('ref'),p.get('pin')] for p in net.findall('node'))))
    return rows


def apply(source,entry,plan,cancel):
    targets={tuple(pin):tuple(row['classes']) for row in plan for pin in row['pins']}
    applied=[]
    for sheet,page in load_pages(source,entry).items():
        cancel();root,_,_=components(page);owners={};labels={};edits=[]
        for node in page['tree'].children('symbol'):
            ref=properties(node)['Reference'].values[2]
            if ref.startswith('#'):continue
            for pin in symbol_pins(page,node):
                key=root(tuple(pin['at']));classes=targets.get((ref,pin['number']),('Default',))
                if key in owners and owners[key]!=classes:fail('实体连线包含不同原生网类目标，不能猜测保持。')
                owners[key]=classes
        for node in page['objects']:
            if node.kind in {'label','global_label','hierarchical_label'}:
                labels.setdefault(root(tuple(location(node)[:2])),[]).append(node)
        for key,classes in owners.items():
            custom=[name for name in classes if name!='Default']
            candidates=labels.get(key,[])
            if custom and not candidates:fail('非默认网类的实体支路缺少可绑定标签；请保留原层次整理。')
            for node in candidates:
                # Assignment is last, after all wire/label cleanup and repetition.
                # Fields are hidden metadata; no project rules are rewritten.
                patches=[(p.start,p.end,'') for p in node.children('property') if p.values[1]=='Netclass']
                text=node.raw
                for a,b,v in sorted(patches,reverse=True):text=text[:a-node.start]+v+text[b-node.start:]
                fields=''.join(f' (property "Netclass" {safe_string(name)} {node.one("at").raw} (effects (font (size 1.27 1.27)) (hide yes)))' for name in custom)
                text=text[:-1]+fields+')'
                if text!=node.raw:edits.append((node.start,node.end,text))
                if custom:applied.append(dict(sheet=sheet,labelId=object_id(node),classes=custom))
        text=page['text']
        for a,b,v in sorted(edits,reverse=True):text=text[:a]+v+text[b:]
        if edits:page['path'].write_text(text,'utf8')
    return dict(sourceNets=plan,labelAssignments=applied,scope='Native resolved constituents attached as hidden Netclass fields after geometry; original project rules unchanged and final native classes independently checked.')
