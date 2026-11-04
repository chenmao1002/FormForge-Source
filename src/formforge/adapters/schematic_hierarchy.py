"""Create explicit single-instance child sheets; electrical connections remain declared edits."""
import re
import uuid
from datetime import date
from pathlib import Path,PureWindowsPath
from ..store import DomainError
from .importer import digest,within
from .native_syntax import parse
from .schematic import fail,number,object_id,properties,safe_string


def plan_sheet(source,entry,pages,op,new_files):
    page=pages[op['sheet']]
    if page['instanceCount']!=1:fail('共享图页暂不支持新建子图页。')
    if digest(page['path'])!=op['expectedSheetHash']:
        raise DomainError('version_conflict','图页摘要已变化，请重新读取。',409)
    name=op['filename']
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}\.kicad_sch',name) or PureWindowsPath(name).is_reserved():
        fail('子图页文件名必须是普通的 .kicad_sch 文件名。')
    relative=(Path(op['sheet']).parent/name).as_posix()
    target=within(source,relative)
    if target.exists() or relative.casefold() in {p.casefold() for p in new_files}:
        fail('子图页文件已经存在，不能覆盖或重复添加。')
    names=[properties(s).get('Sheetname') for s in page['tree'].children('sheet')]
    if any(n and n.values[2].casefold()==op['name'].casefold() for n in names) or any(v['parent']==op['sheet'] and v['name'].casefold()==op['name'].casefold() for v in new_files.values()):
        fail('同一父图页的子图页名称不能重复。')
    def page_numbers(node):
        for item in node.values:
            if hasattr(item,'kind'):
                if item.kind=='page' and len(item.values)>1 and str(item.values[1]).isdigit():yield int(item.values[1])
                yield from page_numbers(item)
    ordinal=max([1,*[n for p in pages.values() for n in page_numbers(p['tree'])]])+len(new_files)+1
    child_id,sheet_id=str(uuid.uuid4()),str(uuid.uuid4())
    child=f'''(kicad_sch (version 20250114) (generator "formforge")
      (uuid "{child_id}") (paper {safe_string(op['paper'])})
      (title_block (title {safe_string(op['title'])}) (date "{date.today().isoformat()}") (rev "1"))
      (lib_symbols) (embedded_fonts no))'''
    parse(child)
    x,y=op['at'];w,h=op['size'];pos=f'{number(x)} {number(y)}'
    text=f'''(sheet (at {pos}) (size {number(w)} {number(h)})
      (stroke (width 0.15) (type default)) (fill (color 0 0 0 0)) (uuid "{sheet_id}")
      (property "Sheetname" {safe_string(op['name'])} (at {number(x)} {number(y-2.54)} 0)
        (effects (font (size 1.27 1.27)) (justify left bottom)))
      (property "Sheetfile" {safe_string(name)} (at {number(x)} {number(y+h+2.54)} 0)
        (effects (font (size 1.27 1.27)) (justify left)))
      (instances (project {safe_string(Path(entry).stem)} (path {safe_string(page['instancePaths'][0])} (page "{ordinal}")))))'''
    parse(text)
    new_files[relative]=dict(data=child.encode('utf8'),parent=op['sheet'],name=op['name'])
    at=page['tree'].end-1
    return [(at,at,'\n'+text+'\n')],dict(kind='add_sheet',sheet=op['sheet'],objectId=sheet_id,before=None,
        after=dict(path=relative,name=op['name'],paper=op['paper'],page=str(ordinal),uuid=child_id))
