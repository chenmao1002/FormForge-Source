"""Native instance reference text shared by presentation geometry and live drawing."""
import json,re
from .schematic import child_value as value,object_id,properties
from ..store import DomainError


def identity(node,fallback):return object_id(node) or fallback


def selected_references(page,instance_path,project_name):
    result={}
    for node in page['tree'].children('symbol'):
        projects=[p for i in node.children('instances') for p in i.children('project')]
        preferred=[p for p in projects if len(p.values)>1 and p.values[1]==project_name]
        paths=[p for project in (preferred or projects) for p in project.children('path') if p.values[1]==instance_path]
        refs={(value(p,'reference'),value(p,'unit')) for p in paths}
        if len(refs)>1 or (page['instanceCount']>1 and not refs):
            raise DomainError('unsupported_capability','此共享实例缺少唯一原生位号注释，请使用原生预览。')
        if refs:
            ref,unit=next(iter(refs))
            if unit is not None and str(unit)!=str(value(node,'unit',1)):
                raise DomainError('unsupported_capability','此实例的单元选择与共享几何不同，请使用原生预览。')
            if ref:result[identity(node,'')]=ref
    return result


def reference_style(project=None):
    """Read the native project presentation settings, leaving source bytes intact."""
    try:
        settings=json.loads(project.read_text('utf-8-sig')).get('schematic',{}) if project else {}
        first=settings.get('subpart_first_id',65);separator=settings.get('subpart_id_separator',0)
        if type(first) is not int or (first not in (65,97) and not 48<=first<=57):raise ValueError('unit notation')
        if type(separator) is not int or (separator and not 32<=separator<=126):raise ValueError('unit separator')
    except (ValueError,TypeError,AttributeError) as exc:
        raise DomainError('unsupported_capability','项目单元位号样式未受支持，请使用原生预览。') from exc
    return dict(first=first,separator=separator)


def unit_suffix(unit,style=None,definition=None):
    style=style or dict(first=65,separator=0)
    names=set()
    if definition:
        for part in definition.children('symbol'):
            match=re.search(r'_(\d+)_\d+$',str(part.values[1]))
            if match and int(match[1])==unit and value(part,'unit_name',''):
                names.add(str(value(part,'unit_name')))
    if len(names)>1:raise DomainError('unsupported_capability','同一单元的原生名称存在歧义，请使用原生预览。')
    if names:result=next(iter(names))
    elif 48<=style['first']<=57:result=str(unit)
    else:
        result=''
        while unit:
            unit,remainder=divmod(unit-1,26);result=chr(style['first']+remainder)+result
    return (chr(style['separator']) if style['separator'] else '')+result


def display_reference(definition,reference,unit,style=None):
    multi=any((match:=re.search(r'_(\d+)_\d+$',str(part.values[1]))) and int(match[1])>1 for part in definition.children('symbol'))
    return reference+(unit_suffix(unit,style,definition) if multi else '')


def reference_labels(page,style,project_name):
    definitions={str(n.values[1]):n for n in page['tree'].one('lib_symbols').children('symbol')}
    result={}
    for path in page['instancePaths']:
        selected=selected_references(page,path,project_name)
        for node in page['tree'].children('symbol'):
            identity=object_id(node);ref=selected.get(identity,properties(node)['Reference'].values[2])
            definition=definitions[value(node,'lib_id')]
            result.setdefault(identity,set()).add(display_reference(definition,ref,int(value(node,'unit',1)),style))
    return result
