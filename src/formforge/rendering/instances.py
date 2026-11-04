"""Human navigation and selected native references for shared schematic files."""
from .primitives import value,identity
from ..store import DomainError


def sheet_instances(pages,entry):
    by_path={p['path'].resolve():name for name,p in pages.items()};result=[]
    def visit(name,path,label):
        result.append(dict(sheet=name,instancePath=path,label=label))
        page=pages[name]
        for n in page['tree'].children('sheet'):
            fields={f.values[1]:f.values[2] for f in n.children('property')}
            child=by_path[(page['path'].parent/fields['Sheetfile']).resolve()]
            visit(child,path+'/'+identity(n,''),(label+' / ' if label!='主图' else '')+fields.get('Sheetname',child))
    visit(entry,'/'+identity(pages[entry]['tree'],''),'主图')
    return result


from ..adapters.schematic_references import selected_references,reference_style,unit_suffix
