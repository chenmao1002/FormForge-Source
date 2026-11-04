"""Project-scoped immutable input records with resumable, hash-checked transport.

The registry owns bytes and caller declarations, not domain validation, supplier
certification or executable build authority. Existing adapters freeze inputs again.
"""
import base64
import binascii
import copy
import hashlib
import json
import re
import stat
from pathlib import Path
from urllib.parse import quote, urlsplit

from .source_schemas import CHUNK_BYTES
from .store import DomainError, Store, encode, now, uid

SUFFIXES = {'.step', '.stp', '.pdf', '.md', '.json', '.csv', '.txt', '.png', '.jpg', '.jpeg',
            '.kicad_sym', '.kicad_mod', '.kicad_sch', '.kicad_pcb', '.kicad_pro',
            '.c', '.h', '.cpp', '.hpp', '.s', '.ld', '.cmake', '.hex', '.bin', '.elf', '.zip', '.xlsx'}
TEXT_SUFFIXES = SUFFIXES - {'.pdf', '.png', '.jpg', '.jpeg', '.bin', '.elf', '.zip', '.xlsx'}
MAX_PROJECT_BYTES = 512 * 1024 * 1024
MAX_PROJECT_RECORDS = 512
META_KEYS = ('name', 'uri', 'version', 'license', 'note', 'origin')


def fail(message, code='invalid_input', status=400):
    raise DomainError(code, message, status)


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    with path.open('rb') as f:
        h = hashlib.sha256()
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
        return h.hexdigest()


def validate_name(name):
    stem = name.split('.')[0].upper()
    if (name != name.strip() or name.startswith('.') or name.endswith('.') or
        re.search(r'[<>:"/\\|?*\x00-\x1f\x7f\ud800-\udfff]', name) or Path(name).is_reserved() or
        stem in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', stem) or
        any(x in name.lower() for x in ('credential', 'api-token', '.env')) or
        Path(name).suffix.lower() not in SUFFIXES):
        fail('资料文件名或类型不允许；只接受独立数据文件名，不接受路径、凭据或程序。')


class SourceRegistry:
    def __init__(self, config, store):
        self.config, self.store = config, store

    def safe(self, relative):
        p = self.config.data / relative
        if not p.resolve().is_relative_to(self.config.data.resolve()):
            fail('资料路径超出本工程数据目录。')
        for part in [p, *p.parents]:
            if part.is_symlink() or (part.exists() and getattr(part.stat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                fail('资料不接受符号链接或重解析目录。')
        return p

    def owned(self, db, table, id, project):
        row = self.store._get(db, table, id)
        if row['projectId'] != project:
            fail('资料或上传不属于此工程。', 'not_found', 404)
        return row

    def metadata(self, row):
        out = {k: v for k, v in row.items() if k not in {'relativePath', 'provenancePath'}}
        ref = row['ref']
        common = dict(id=row['id'], path=ref, sha256=row['sha256'], version=row['version'], note=row['note'])
        out['bindings'] = dict(
            externalManufacturing=dict(sourceId=row['id'],expectedSha256=row['sha256'],expectedProvenanceSha256=row['provenanceSha256']),
            procurement=dict(sourceId=row['id'],expectedSha256=row['sha256'],expectedProvenanceSha256=row['provenanceSha256']),
            firmware={**common, 'uri': row['uri'], 'license': row['license']},
            mechanical={**common, 'uri': row['uri'], 'license': row['license']},
            review={**common, 'url': row['uri']},
            symbolLibrary=dict(sourcePath=ref, expectedSha256=row['sha256'], sourceUri=row['uri'],
                               sourceVersion=row['version'], description=row['note'], license=row['license']))
        out['downloadUrl'] = f"/api/projects/{row['projectId']}/sources/{row['id']}/file"
        return out

    def response(self, upload):
        result = {k: v for k, v in upload.items() if k not in {'chunks', 'fingerprint'}}
        result.update(maxChunkBytes=CHUNK_BYTES)
        if upload['status'] == 'committed':
            result['source'] = self.get(upload['projectId'], upload['sourceId'])
        return result

    def get_record(self, project, id):
        with self.store.read_tx() as db:
            self.store._get(db, 'projects', project)
            row = self.owned(db, 'sources', id, project)
        path = self.safe(row['relativePath'])
        provenance = self.safe(row['provenancePath'])
        if not path.is_file() or path.stat().st_size != row['size'] or digest_file(path) != row['sha256']:
            fail('已登记资料字节发生变化，拒绝使用。', 'input_changed', 409)
        if not provenance.is_file() or digest_file(provenance) != row['provenanceSha256']:
            fail('已登记资料的来源记录发生变化，拒绝使用。', 'input_changed', 409)
        expected={k:v for k,v in row.items() if k not in {'provenancePath','provenanceSha256'}}
        if provenance.read_bytes()!=encode(expected).encode('utf8'):
            fail('资料登记与冻结来源记录不一致，拒绝使用。', 'input_changed', 409)
        return row, path

    def get(self, project, id):
        return self.metadata(self.get_record(project, id)[0])

    def list(self, project, offset=0, limit=40, include_uploads=False):
        if offset < 0 or not 1 <= limit <= 100:
            fail('资料分页范围无效。')
        self.store.get('projects', project)
        rows = self.store.list('sources', project)
        result = dict(sources=[self.metadata(r) for r in rows[offset:offset + limit]], total=len(rows),
                      offset=offset, nextOffset=offset + limit if offset + limit < len(rows) else None)
        if include_uploads:
            uploads = self.store.list('source_uploads', project)
            result['uploads'] = [{k: v for k, v in u.items() if k not in {'chunks', 'fingerprint'}} for u in uploads[:100]]
        return result

    def read(self, project, id, encoding='metadata', offset=0, limit=65536):
        if encoding not in {'metadata', 'base64'} or offset < 0 or not 1 <= limit <= CHUNK_BYTES:
            fail('资料读取范围无效。')
        row, path = self.get_record(project, id)
        result = dict(source=self.metadata(row))
        if encoding == 'base64':
            if offset > row['size']:
                fail('读取位置超过文件长度。')
            with path.open('rb') as f:
                f.seek(offset)
                data = f.read(limit)
            result.update(offset=offset, size=len(data), encoding='base64', contentBase64=base64.b64encode(data).decode('ascii'),
                          sha256=digest_bytes(data), nextOffset=offset + len(data) if offset + len(data) < row['size'] else None)
        return result

    def begin(self, project, request):
        validate_name(request['name'])
        metadata = {k: request[k] for k in META_KEYS}
        try:
            for value in metadata.values():value.encode('utf8')
        except UnicodeError:fail('来源信息包含无效Unicode字符。')
        if any(not str(v).strip() for v in metadata.values()) or not re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:',metadata['uri']):
            fail('来源、版本、许可和说明不能为空；来源须为明确URI，例如https:或urn:。')
        fingerprint = digest_bytes(encode([project, metadata, request['size'], request['sha256']]).encode('utf8'))
        with self.store.tx() as db:
            self.store._get(db, 'projects', project)
            previous = db.execute('SELECT fingerprint,upload_id FROM source_requests WHERE key=?', (request['requestId'],)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    fail('此资料请求编号已用于不同内容或来源。', 'version_conflict', 409)
                upload = self.owned(db, 'source_uploads', previous[1], project)
            else:
                records = [json.loads(r[0]) for r in db.execute('SELECT data FROM source_uploads WHERE project_id=?', (project,))]
                active = [r for r in records if r['status'] != 'aborted']
                if len(active) >= MAX_PROJECT_RECORDS or sum(r['size'] for r in active) + request['size'] > MAX_PROJECT_BYTES:
                    fail(f'本工程资料配额已满（{MAX_PROJECT_RECORDS}份／{MAX_PROJECT_BYTES // (1024 * 1024)}MiB，含未完成上传）。')
                upload = dict(id=uid('u'), projectId=project, sourceId=uid('s'), **metadata, size=request['size'], sha256=request['sha256'],
                              fingerprint=fingerprint, status='uploading', receivedBytes=0, chunks=[], createdAt=now(), finishedAt=None)
                self.store._put(db, 'source_uploads', upload)
                db.execute('INSERT INTO source_requests VALUES(?,?,?)', (request['requestId'], fingerprint, upload['id']))
                self.store._event(db, 'source.upload.started', project, dict(uploadId=upload['id'], name=upload['name'], size=upload['size']))
        return upload

    def clean_staging(self, upload):
        if upload['status'] == 'uploading':return
        offsets={c['offset'] for c in upload['chunks']}|{upload['receivedBytes']}
        for suffix in [*[f'{offset:012d}.chunk{tail}' for offset in offsets for tail in ('','.tmp')], 'assembled.tmp']:
            path=self.safe(f"projects/{upload['projectId']}/source-uploads/{upload['id']}/{suffix}")
            if path.is_file():path.unlink()

    def append(self, project, request):
        try:
            data = base64.b64decode(request['contentBase64'], validate=True)
        except (ValueError, binascii.Error):
            fail('资料块不是有效base64。')
        if not 1 <= len(data) <= CHUNK_BYTES or digest_bytes(data) != request['sha256']:
            fail('资料块长度或SHA不一致。')
        offset = request['offset']
        with self.store.tx() as db:
            upload = self.owned(db, 'source_uploads', request['uploadId'], project)
            if upload['status'] == 'aborted':
                fail('上传已经取消，不能继续写入。', 'version_conflict', 409)
            if offset + len(data) > upload['size']:
                fail('资料块超过声明的文件长度。')
            if upload['status'] == 'committed':
                row = self.owned(db, 'sources', upload['sourceId'], project)
                path = self.safe(row['relativePath'])
                if digest_file(path) != row['sha256']:
                    fail('已登记资料发生变化。', 'input_changed', 409)
                with path.open('rb') as f:
                    f.seek(offset)
                    if f.read(len(data)) != data:
                        fail('不能改写已登记资料。', 'version_conflict', 409)
                return upload
            old = next((c for c in upload['chunks'] if c['offset'] == offset), None)
            relative = f"projects/{project}/source-uploads/{upload['id']}/{offset:012d}.chunk"
            path = self.safe(relative)
            if old:
                if old['sha256'] != request['sha256'] or old['size'] != len(data) or not path.is_file() or digest_file(path) != old['sha256']:
                    fail('重试资料块与已接收内容不同。', 'version_conflict', 409)
                return upload
            if offset != upload['receivedBytes']:
                fail('资料块位置不连续；请读取上传状态后续传。', 'version_conflict', 409)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if path.read_bytes() != data:
                    fail('未登记的资料块内容不一致。', 'input_changed', 409)
            else:
                draft=self.safe(relative+'.tmp')
                with draft.open('wb') as f:
                    f.write(data)
                draft.replace(path)
            upload['chunks'].append(dict(offset=offset, size=len(data), sha256=request['sha256']))
            upload['receivedBytes'] += len(data)
            self.store._put(db, 'source_uploads', upload)
            self.store._event(db, 'source.upload.progress', project, dict(uploadId=upload['id'], receivedBytes=upload['receivedBytes'], size=upload['size']))
            return upload

    def commit(self, project, id):
        with self.store.tx() as db:
            upload = self.owned(db, 'source_uploads', id, project)
            if upload['status'] == 'committed':
                return upload
            if upload['status'] != 'uploading' or upload['receivedBytes'] != upload['size']:
                fail('上传未完成或已取消，不能登记。', 'version_conflict', 409)
            relative = f"projects/{project}/sources/{upload['sourceId']}/{upload['name']}"
            target = self.safe(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = self.safe(f"projects/{project}/source-uploads/{id}/assembled.tmp")
            with temp.open('wb') as f:
                for chunk in upload['chunks']:
                    path = self.safe(f"projects/{project}/source-uploads/{id}/{chunk['offset']:012d}.chunk")
                    if not path.is_file() or path.stat().st_size != chunk['size'] or digest_file(path) != chunk['sha256']:
                        fail('已接收资料块发生变化，拒绝登记。', 'input_changed', 409)
                    f.write(path.read_bytes())
            if temp.stat().st_size != upload['size'] or digest_file(temp) != upload['sha256']:
                fail('文件总长度或SHA不符，未登记资料。', 'input_changed', 409)
            if target.exists():
                if digest_file(target) != upload['sha256']:
                    fail('资料目标存在不同内容，拒绝覆盖。', 'input_changed', 409)
            else:
                temp.replace(target)
            record = dict(id=upload['sourceId'], projectId=project, **{k: upload[k] for k in META_KEYS},
                          size=upload['size'], sha256=upload['sha256'], createdAt=upload['createdAt'],
                          ref=f"source://{upload['sourceId']}/{quote(upload['name'], safe='')}", relativePath=relative,
                          status='registered', provenanceTrust='caller-declared; original-bytes-sha256-verified')
            provenance_data = encode(record).encode('utf8')
            # Reserved sidecar name is separate from user filenames, including provenance.json.
            provenance_relative = f"projects/{project}/source-provenance/{record['id']}.json"
            provenance = self.safe(provenance_relative)
            provenance.parent.mkdir(parents=True, exist_ok=True)
            if provenance.exists():
                if provenance.read_bytes() != provenance_data:
                    fail('资料来源记录存在不同内容，拒绝覆盖。', 'input_changed', 409)
            else:
                draft=self.safe(provenance_relative+'.tmp')
                with draft.open('wb') as f:
                    f.write(provenance_data)
                draft.replace(provenance)
            record.update(provenancePath=provenance_relative, provenanceSha256=digest_bytes(provenance_data))
            self.store._put(db, 'sources', record)
            upload.update(status='committed', finishedAt=now())
            self.store._put(db, 'source_uploads', upload)
            self.store._event(db, 'source.registered', project, dict(sourceId=record['id'], name=record['name'], sha256=record['sha256']))
        return upload

    def transfer(self, project, request):
        action = request['action']
        if action == 'register_text':
            if Path(request['name']).suffix.lower() not in TEXT_SUFFIXES:
                fail('此文件类型不能按UTF-8文本登记，请使用分块文件上传。')
            try:data = request['text'].encode('utf8')
            except UnicodeError:fail('文本不是有效UTF-8字符序列。')
            if not 1 <= len(data) <= CHUNK_BYTES:
                fail('UTF-8文本必须在192KiB以内。')
            sha = digest_bytes(data)
            if request.get('expectedSha256') and request['expectedSha256'] != sha:
                fail('文本SHA与声明不一致。', 'input_changed', 409)
            upload = self.begin(project, {**request, 'size': len(data), 'sha256': sha})
            if upload['status'] != 'committed':
                upload = self.append(project, dict(uploadId=upload['id'], offset=0, contentBase64=base64.b64encode(data).decode('ascii'), sha256=sha))
                upload = self.commit(project, upload['id'])
        elif action == 'begin':
            upload = self.begin(project, request)
        elif action == 'append':
            upload = self.append(project, request)
        elif action == 'commit':
            upload = self.commit(project, request['uploadId'])
        elif action in {'status', 'abort'}:
            with self.store.tx() as db:
                upload = self.owned(db, 'source_uploads', request['uploadId'], project)
                if action == 'abort' and upload['status'] == 'uploading':
                    upload.update(status='aborted', finishedAt=now())
                    self.store._put(db, 'source_uploads', upload)
                    self.store._event(db, 'source.upload.aborted', project, dict(uploadId=upload['id']))
        else:
            fail('不支持的资料操作。')
        self.clean_staging(upload)
        return self.response(upload)

    def resolve(self, project, ref):
        uri = urlsplit(ref)
        if uri.scheme != 'source' or not re.fullmatch(r's_[0-9a-f]{16}', uri.netloc):
            fail('资料引用格式无效。')
        row, path = self.get_record(project, uri.netloc)
        if ref != row['ref']:
            fail('资料引用与登记文件名不一致。')
        return row, path

    def resolve_params(self, project, params):
        result = copy.deepcopy(params)
        resolved = {}
        records = {}

        def record_once(id):
            if id not in records:records[id]=self.get_record(project,id)
            return records[id]

        def resolve_once(ref):
            if ref not in resolved:resolved[ref]=self.resolve(project,ref)
            return resolved[ref]

        def visit(node):
            if isinstance(node, list):
                for value in node:
                    visit(value)
            elif isinstance(node, dict):
                for field in ('path', 'sourcePath'):
                    ref = node.get(field)
                    if not isinstance(ref, str):
                        continue
                    if ref.startswith('source://'):
                        row,path=resolve_once(ref)
                    else:
                        raw=Path(ref).expanduser()
                        if not raw.is_absolute():continue
                        try:relative=raw.resolve().relative_to(self.config.data/'projects')
                        except (OSError,ValueError):continue
                        if len(relative.parts)!=4 or relative.parts[1].lower()!='sources':continue
                        # Native-path compatibility must not let a caller relabel
                        # registered bytes or silently borrow a different project.
                        for part in [raw,*raw.parents]:
                            if part.is_symlink() or (part.exists() and getattr(part.stat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT):
                                fail('登记资料路径不接受符号链接或重解析目录。')
                        row,path=record_once(relative.parts[2])
                        if raw.resolve()!=path.resolve():fail('资料路径与登记记录不同。')
                        ref=row['ref']
                    comparisons = dict(sha256='sha256', expectedSha256='sha256', uri='uri', url='uri', sourceUri='uri',
                                       version='version', sourceVersion='version', license='license', note='note')
                    for key, metadata_key in comparisons.items():
                        if key in node and node[key] != row[metadata_key]:
                            fail('资料引用的摘要或来源声明与登记记录不同。', 'version_conflict', 409)
                    node[field] = str(path)
                    node['registeredSource'] = dict(id=row['id'], ref=ref, sha256=row['sha256'], provenanceSha256=row['provenanceSha256'],
                                                    origin=row['origin'], provenanceTrust=row['provenanceTrust'],
                                                    provenance={k:row[k] for k in META_KEYS},createdAt=row['createdAt'])
                for value in list(node.values()):
                    visit(value)
        visit(result)
        return result


def validate_registered_file(config, project, item):
    binding = item.get('registeredSource')
    if not binding:
        return False
    registry = SourceRegistry(config, Store(config.data / 'formforge.sqlite3'))
    row, path = registry.get_record(project, binding['id'])
    if str(path) != item['path'] or row['sha256'] != item['sha256'] or row['provenanceSha256'] != binding['provenanceSha256']:
        fail('审查资料与登记来源不匹配。', 'input_changed', 409)
    return True
