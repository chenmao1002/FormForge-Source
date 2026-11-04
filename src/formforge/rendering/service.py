"""One read-only scene entry for HTTP, MCP, tests and incremental consumers."""
from collections import OrderedDict
from copy import deepcopy
import hashlib,json,threading
from ..adapters.importer import digest as file_digest,within,verify_snapshot
from ..adapters.native_syntax import parse
from ..adapters.schematic import load_pages
from ..store import DomainError
from . import schematic,pcb
from .instances import sheet_instances,selected_references,reference_style
from pathlib import Path


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class SceneReader:
    def __init__(self,service):
        self.service=service
        self.snapshots=OrderedDict()
        self.page_sets=OrderedDict()
        self.integrity=OrderedDict()
        self.key_locks={}
        self.lock=threading.Lock()

    def _key_lock(self,key):
        with self.lock:return self.key_locks.setdefault(key,threading.Lock())

    def _stat_signature(self,source,manifest,paths):
        signature=[]
        selected=set(paths)
        try:
            for record in manifest:
                if record['path']not in selected:continue
                path=within(source,record['path'])
                if not path.is_file():return None
                info=path.stat()
                signature.append((record['path'],info.st_size,info.st_mtime_ns,info.st_ctime_ns,getattr(info,'st_ino',0)))
        except OSError:return None
        return tuple(signature)if len(signature)==len(selected)else None

    def _drop_revision(self,project_id,revision_id):
        for key in [key for key in self.snapshots if key[:2]==(project_id,revision_id)]:self.snapshots.pop(key,None)
        for key in [key for key in self.page_sets if key[:2]==(project_id,revision_id)]:self.page_sets.pop(key,None)
        for key in [key for key in self.integrity if key[:2]==(project_id,revision_id)]:self.integrity.pop(key,None)

    def _ensure_verified(self,project_id,revision_id,source,manifest,paths):
        identity=(project_id,revision_id,tuple(paths))
        signature=self._stat_signature(source,manifest,paths)
        with self.lock:
            if signature is not None and self.integrity.get(identity)==signature:
                self.integrity.move_to_end(identity);return
        with self._key_lock(('integrity',project_id,revision_id)):
            signature=self._stat_signature(source,manifest,paths)
            with self.lock:
                if signature is not None and self.integrity.get(identity)==signature:
                    self.integrity.move_to_end(identity);return
                known=identity in self.integrity
                revision_verified=any(key[:2]==(project_id,revision_id)for key in self.integrity)
            if not known and revision_verified:
                expected={record['path']:record['sha256']for record in manifest}
                valid=signature is not None and all(file_digest(within(source,path))==expected.get(path)for path in paths)
                if valid:
                    with self.lock:
                        self.integrity[identity]=signature
                        while len(self.integrity)>32:self.integrity.popitem(last=False)
                    return
                with self.lock:self._drop_revision(project_id,revision_id)
                raise DomainError('input_changed','输入快照发生变化，拒绝使用旧版本执行检查。',409)
            with self.lock:self._drop_revision(project_id,revision_id)
            verify_snapshot(source,manifest)
            signature=self._stat_signature(source,manifest,paths)
            if signature is None:raise DomainError('input_changed','输入快照发生变化，拒绝使用旧版本执行检查。',409)
            with self.lock:
                self.integrity[identity]=signature
                while len(self.integrity)>32:self.integrity.popitem(last=False)

    def _pages(self,project_id,revision_id,source,manifest,entry,integrity_paths):
        key=(project_id,revision_id,entry)
        with self.lock:
            cached=self.page_sets.get(key)
            if cached is not None:self.page_sets.move_to_end(key)
        if cached is not None:return cached
        with self._key_lock(('pages',)+key):
            with self.lock:
                cached=self.page_sets.get(key)
                if cached is not None:self.page_sets.move_to_end(key)
            if cached is not None:return cached
            pages=load_pages(source,entry)
            self._ensure_verified(project_id,revision_id,source,manifest,integrity_paths)
            with self.lock:
                self.page_sets[key]=pages
                while len(self.page_sets)>8:self.page_sets.popitem(last=False)
            return pages

    def read(self,project_id,revision_id,kind,sheet=None,since=None,instance_path=None):
        if kind not in ('schematic','pcb'):raise DomainError('invalid_input','绘图类型必须为 schematic 或 pcb。')
        revision=self.service.revision(project_id,revision_id)
        source=within(self.service.config.data,revision['relativePath'])
        entry=revision['entrypoints'].get('schematic'if kind=='schematic'else'board')
        if not entry:raise DomainError('unsupported_capability','此版本没有对应的原生图纸。')
        if kind=='pcb'and (sheet or instance_path):raise DomainError('invalid_input','PCB 绘图不接受原理图图页或实例参数。')
        selected=sheet or entry
        project_file=Path(entry).with_suffix('.kicad_pro').as_posix() if kind=='schematic' else None
        tracked={r['path'] for r in revision['manifest']}
        if project_file and within(source,project_file).exists() and project_file not in tracked:
            raise DomainError('input_changed','绘图项目设置不在版本快照中，请重新导入。',409)
        integrity_paths=tuple(r['path']for r in revision['manifest']if r['path'].endswith('.kicad_sch') or r['path']==project_file)if kind=='schematic'else(entry,)
        self._ensure_verified(project_id,revision_id,source,revision['manifest'],integrity_paths)
        instances=[];selected_path=None
        if kind=='schematic':
            pages=self._pages(project_id,revision_id,source,revision['manifest'],entry,integrity_paths)
            if selected not in pages:raise DomainError('invalid_input','图页不属于指定原理图版本。')
            page=pages[selected];instances=sheet_instances(pages,entry)
            if instance_path and instance_path not in page['instancePaths']:raise DomainError('invalid_input','实例路径不属于所选图页。')
            if page['instanceCount']!=1 and not instance_path:raise DomainError('unsupported_capability','共享图页须明确选择实例路径。')
            selected_path=instance_path or page['instancePaths'][0]
        key=(project_id,revision_id,kind,selected,selected_path)
        with self.lock:
            cached=deepcopy(self.snapshots.get(key))
            if cached is not None:self.snapshots.move_to_end(key)
        if cached:
            scene=cached
        else:
            if kind=='schematic':
                references=selected_references(page,selected_path,Path(entry).stem)
                style=reference_style(within(source,project_file) if project_file in tracked else None)
                drawing=schematic.build(page,references,style);sheets=list(pages);raw=page['raw']
            else:
                path=within(source,entry)
                if path.stat().st_size>24*1024*1024:raise DomainError('unsupported_capability','实时绘图暂支持24MB以内原生PCB。')
                raw=path.read_bytes();tree=parse(raw.decode('utf-8-sig'));drawing=pcb.build(tree);sheets=[]
            expected=next((r['sha256']for r in revision['manifest']if r['path']==selected),None)
            raw_hash=hashlib.sha256(raw).hexdigest()
            if raw_hash!=expected:raise DomainError('input_changed','实际绘图文件与版本快照摘要不一致。',409)
            ids=[r['id']for r in drawing.objects]
            if len(ids)!=len(set(ids)):raise DomainError('invalid_input','绘图对象身份重复，无法增量更新。')
            scene=dict(schemaVersion=1,projectId=project_id,revisionId=revision_id,kind=kind,sheet=selected,sheets=sheets,units='mm',
                sourceSha256=raw_hash,objects=drawing.objects,bounds=drawing.bounds(),
                layers=list(dict.fromkeys(o['layer']for o in drawing.objects)),
                coverage=dict(complete=not drawing.omitted,omitted=drawing.omitted,typography='browser-font-approximation',nativePlotEquivalent=False),
                scope='Actual immutable native candidate geometry; rendering is not ERC, DRC, connectivity, manufacture or adoption proof.')
            if kind=='schematic':scene.update(instancePath=selected_path,sheetInstances=instances,renderInputs=[dict(path=r['path'],sha256=r['sha256']) for r in revision['manifest'] if r['path'] in {selected,project_file}])
            if kind=='pcb':scene['pcbSummary']=pcb.summary(tree)
            scene['sceneHash']=digest(scene)
            self._ensure_verified(project_id,revision_id,source,revision['manifest'],integrity_paths)
            with self.lock:
                self.snapshots[key]=deepcopy(scene)
                while len(self.snapshots)>32:self.snapshots.popitem(last=False)
        scene['validation']=revision.get('validation')or dict(status='unverified')
        with self.lock:
            before=next((s for s in self.snapshots.values()if s['sceneHash']==since and s['projectId']==project_id and s['kind']==kind and s['sheet']==scene['sheet'] and s.get('instancePath')==selected_path),None)
            before=deepcopy(before)
        if before:
            old={o['id']:o for o in before['objects']};new={o['id']:o for o in scene['objects']}
            return {**{k:v for k,v in scene.items()if k!='objects'},'mode':'delta','baseSceneHash':since,
                'upsert':[o for id,o in new.items()if old.get(id)!=o],'remove':[id for id in old if id not in new],
                **({'order':list(new)}if list(new)!=list(old)else{})}
        return {**scene,'mode':'snapshot'}


def scene_reader(service):
    if not hasattr(service,'drawing_scenes'):service.drawing_scenes=SceneReader(service)
    return service.drawing_scenes
