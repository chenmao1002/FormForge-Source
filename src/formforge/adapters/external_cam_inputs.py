"""Bounded ZIP data and metadata; no extraction, imports, or host path evaluation."""
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import stat
import zipfile

MAX_ENTRY = 16 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 256


def path_name(name):
    if not isinstance(name,str) or not name or len(name)>400 or '\\' in name or any(ord(c)<32 or ord(c)==127 for c in name):
        raise ValueError('Invalid archive path.')
    path=PurePosixPath(name.rstrip('/'))
    if not path.parts or path.is_absolute() or str(path)!=name.rstrip('/') or any(p in {'.','..'} or p.endswith(('.', ' ')) or
            re.search(r'[<>:"|?*]',p) or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',p) for p in path.parts):
        raise ValueError('Archive path traversal, reserved name, or invalid Windows component.')
    return path.as_posix()


def archive_index(data):
    if len(data)>64*1024*1024:
        raise ValueError('Gerber ZIP exceeds 64MiB.')
    archive=zipfile.ZipFile(io.BytesIO(data))
    infos=archive.infolist()
    if not infos or len(infos)>MAX_FILES:
        raise ValueError('Archive must have 1..256 entries.')
    seen=set();files=[];total=0
    for info in infos:
        if info.orig_filename!=info.filename:
            raise ValueError('Archive raw filename was normalized or truncated by the ZIP reader.')
        name=path_name(info.filename);key=name.casefold()
        mode=stat.S_IFMT(info.external_attr>>16)
        if key in seen or info.flag_bits & 1 or mode not in {0,stat.S_IFREG,stat.S_IFDIR}:
            raise ValueError('Duplicate, encrypted, or non-regular archive entry.')
        seen.add(key)
        if info.is_dir():
            continue
        if info.file_size>MAX_ENTRY or info.file_size<0:
            raise ValueError('Archive entry exceeds 16MiB.')
        total+=info.file_size
        if total>MAX_TOTAL:
            raise ValueError('Expanded archive exceeds 128MiB.')
        files.append(info)
    if not files:
        raise ValueError('Archive contains no files.')
    return archive,files


def read_entry(archive,info):
    data=archive.read(info)
    if len(data)!=info.file_size or len(data)>MAX_ENTRY:
        raise ValueError('Archive size differs from its directory.')
    return data


def function_hint(text):
    functions=re.findall(r'(?:%TF\.FileFunction,|;\s*#@!\s*TF\.FileFunction,)([^*\r\n%]+)',text)
    functions=[tuple(v.strip() for v in value.strip().split(',')) for value in functions]
    if len(set(functions))>1:
        raise ValueError('Conflicting FileFunction metadata.')
    return list(functions[0]) if functions else None


def suggest(name,head):
    try:
        text=head.decode('utf-8-sig')
    except UnicodeError:
        return dict(suggestedMapping=None,evidence='binary-or-unknown-encoding')
    function=function_hint(text)
    mapping=None
    if function:
        role=function[0].casefold()
        side=next(('top' if p=='Top' else 'bottom' for p in function[1:] if p in {'Top','Bot'}),None)
        if role=='copper' and len(function)>1 and re.fullmatch(r'L\d+',function[1]):
            mapping=dict(role='copper',copperLayer=int(function[1][1:]))
        elif role in {'soldermask','paste','solderpaste','legend'} and side:
            mapping=dict(role={'soldermask':'mask','paste':'paste','solderpaste':'paste','legend':'legend'}[role],side=side)
        elif role=='profile':mapping=dict(role='profile')
        elif role in {'plated','nonplated'}:mapping=dict(role='drill',plating='plated' if role=='plated' else 'non-plated')
    if mapping is None and name.lower().endswith('.gbrjob'):
        mapping=dict(role='job')
    return dict(suggestedMapping=mapping,evidence='file-metadata' if function else 'filename-only' if mapping else 'unidentified',fileFunction=function,
                confirmationRequired=True)


def inspect_zip(data):
    archive,files=archive_index(data)
    with archive:
        entries=[]
        for info in files:
            with archive.open(info) as stream:
                head=stream.read(8192)
            entries.append(dict(path=info.filename,bytes=info.file_size,**suggest(info.filename,head)))
    return dict(entries=entries,entryCount=len(entries),expandedBytes=sum(i.file_size for i in files),
                sourceSha256=hashlib.sha256(data).hexdigest(),suggestionsAreNotConfirmed=True)
