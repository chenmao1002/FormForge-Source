"""Run inside the independent interpreter; inventory installed trusted dependencies."""
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path


def inventory():
    if sys.version_info<(3,12):
        raise ValueError('CAM runtime requires Python >=3.12.')
    root=Path(sys.prefix).resolve()
    versions={};files={};licenses={}
    for dist in importlib.metadata.distributions():
        name=dist.metadata['Name'].lower().replace('_','-')
        if name=='pip':continue
        versions[name]=dist.version
        licenses[name]=dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'See bundled distribution licenses'
        for file in dist.files or []:
            path=Path(dist.locate_file(file)).resolve()
            if path.suffix in {'.pyc','.pyo'} or '__pycache__' in path.parts:
                continue
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('Runtime dependency missing or outside its environment: '+str(file))
            files[path.relative_to(root).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    for name,version in {'gerbonara':'1.6.3','shapely':'2.1.2','openpyxl':'3.1.5','defusedxml':'0.7.1'}.items():
        if versions.get(name)!=version:raise ValueError('Unexpected CAM dependency '+name)
    site_roots=[Path(p).resolve() for p in sys.path if p and Path(p).name=='site-packages' and Path(p).resolve().is_relative_to(root)]
    actual=set()
    for folder in site_roots:
        for path in folder.rglob('*'):
            relative=path.relative_to(folder)
            if not path.is_file() or path.suffix in {'.pyc','.pyo'} or '__pycache__' in relative.parts:continue
            if relative.parts[0]=='pip' or (relative.parts[0].startswith('pip-') and relative.parts[0].endswith('.dist-info')):continue
            actual.add(path.relative_to(root).as_posix())
    expected={name for name in files if any((root/name).is_relative_to(folder) for folder in site_roots)}
    if actual!=expected:raise ValueError('CAM site-packages contains missing or unregistered payload files.')
    return dict(pythonVersion=sys.version,environment=str(root),basePrefix=sys.base_prefix,versions=versions,
                files=files,siteRoots=[p.relative_to(root).as_posix() for p in site_roots],licenses=licenses,stdlibTrust='installed-interpreter; not copied into each package')


if __name__=='__main__':
    print(json.dumps(inventory(),ensure_ascii=False,sort_keys=True))
