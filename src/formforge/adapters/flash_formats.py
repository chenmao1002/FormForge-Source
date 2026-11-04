"""Bounded, data-only flash image and archive parsing; no device libraries."""
import hashlib
import io
import json
import re
import shlex
import stat
import struct
import zipfile
from pathlib import PurePosixPath
from ..store import DomainError

LIMIT = 64 * 1024 * 1024


def fail(message):
    raise DomainError('invalid_flash_input', message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def member_name(name):
    p = PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or p.is_absolute() or
            any(x in ('', '.', '..') or x.rstrip(' .') != x for x in name.split('/')) or
            any(ord(x) < 32 for x in name)):
        fail('ZIP 或清单文件路径无效。')
    return p.as_posix()


def archive_files(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 256:
                fail('固件 ZIP 文件数超过 256。')
            result, seen, total = {}, set(), 0
            for info in archive.infolist():
                if '\\' in info.orig_filename or '\x00' in info.orig_filename:
                    fail('ZIP 包含不规范路径。')
                name = member_name(info.filename.rstrip('/') if info.is_dir() else info.filename)
                if name.casefold() in seen or stat.S_ISLNK(info.external_attr >> 16):
                    fail('ZIP 包含重复路径或符号链接。')
                seen.add(name.casefold())
                if info.flag_bits & 1:
                    fail('不接受加密 ZIP。')
                total += info.file_size
                if total > LIMIT:
                    fail('固件 ZIP 解压总量超过 64 MiB。')
                if not info.is_dir():
                    result[name] = archive.read(info)
            return result
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as error:
        fail('无法读取固件 ZIP：' + str(error))


def unique_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: fail('清单包含重复字段：' + key)
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=pairs)
    except (ValueError, UnicodeError):
        fail('固件清单不是有效 JSON。')


def manifest_images(files, name, target):
    name = member_name(name)
    if name not in files: fail('ZIP 缺少指定清单。')
    parent = PurePosixPath(name).parent
    def path(value):
        if not isinstance(value, str): fail('清单文件路径必须是字符串。')
        return member_name((parent / member_name(value)).as_posix())
    def address(value):
        try: return int(value, 0) if isinstance(value, str) else int(value)
        except (ValueError, TypeError): fail('清单烧录地址无效。')
    if name.endswith('.json'):
        value = unique_json(files[name])
        if not isinstance(value, dict): fail('清单必须是对象。')
        if value.get('schemaVersion') == 'formforge.flash-manifest/1':
            if set(value) - {'schemaVersion', 'target', 'images'} or value.get('target') != target:
                fail('固件清单目标或字段不匹配。')
            rows = value.get('images')
            if not isinstance(rows, list): fail('清单 images 必须为列表。')
            result = []
            for row in rows:
                if not isinstance(row, dict) or set(row) - {'file', 'format', 'role', 'address'}:
                    fail('固件清单条目无效。')
                result.append(dict(member=path(row.get('file')), format=row.get('format', 'bin'),
                                   role=row.get('role', 'firmware'), address=address(row['address']) if row.get('address') is not None else None))
            return result, {}
        allowed = {'write_flash_args', 'flash_settings', 'flash_files', 'extra_esptool_args',
                   'bootloader', 'partition-table', 'app', 'otadata'}
        if set(value) - allowed: fail('ESP-IDF 清单存在尚不支持的字段。请使用规范清单。')
        extra = value.get('extra_esptool_args', {})
        if not isinstance(extra, dict) or set(extra) - {'chip', 'before', 'after', 'stub'}:
            fail('ESP-IDF 连接参数不受支持。')
        if extra.get('chip') != target: fail('ESP-IDF 清单芯片与选择目标不符。')
        settings = value.get('flash_settings', {})
        if not isinstance(settings, dict) or set(settings) - {'flash_mode', 'flash_freq', 'flash_size'}:
            fail('ESP Flash 参数不受支持。')
        args = value.get('write_flash_args', [])
        parsed = parse_options(args)
        for key, val in settings.items():
            if key in parsed and parsed[key] != val: fail('ESP Flash 参数相互冲突。')
            parsed[key] = val
        rows = value.get('flash_files')
        if not isinstance(rows, dict): fail('ESP-IDF 清单缺少 flash_files。')
        roles = {}
        for label, role in [('bootloader', 'bootloader'), ('partition-table', 'partition-table'), ('app', 'application'), ('otadata', 'data')]:
            item = value.get(label)
            if item is not None:
                if not isinstance(item, dict) or set(item) - {'offset', 'file', 'encrypted'} or item.get('encrypted') not in (False, None):
                    fail('不支持加密或未知 ESP 文件条目。')
                roles[(path(item.get('file')), address(item.get('offset')))] = role
        if not set(roles).issubset({(path(file), address(addr)) for addr,file in rows.items()}):
            fail('ESP-IDF 文件角色与 flash_files 地址不一致。')
        return [dict(member=path(file), format='bin', role=roles.get((path(file), address(addr)), 'firmware'), address=address(addr))
                for addr, file in rows.items()], parsed
    try: tokens = shlex.split(files[name].decode('utf8'), posix=True)
    except (ValueError, UnicodeError): fail('flash_args 文本无效。')
    options, result = {}, []
    while tokens:
        token = tokens.pop(0)
        if token.startswith('--'):
            if not tokens: fail('flash_args 缺少参数值。')
            parsed = parse_options([token, tokens.pop(0)])
            if options.keys() & parsed.keys(): fail('flash_args 参数重复。')
            options.update(parsed)
        else:
            if not tokens: fail('flash_args 缺少文件名。')
            result.append(dict(member=path(tokens.pop(0)), format='bin', role='firmware', address=address(token)))
    return result, options


def parse_options(tokens):
    if not isinstance(tokens, list) or len(tokens) % 2: fail('ESP 写入参数格式无效。')
    result = {}
    for key, value in zip(tokens[::2], tokens[1::2]):
        key = key.replace('-', '_').lstrip('_') if isinstance(key, str) else ''
        if key not in {'flash_mode', 'flash_freq', 'flash_size'} or key in result or not isinstance(value, str):
            fail('ESP 写入参数不受支持或重复。')
        result[key] = value
    return result


def sections(data, fmt, address=None):
    if not data or len(data) > LIMIT: fail('固件为空或超过 64 MiB。')
    if fmt == 'bin':
        if address is None: fail('BIN 必须明确烧录地址。')
        return [(address, data)]
    if address is not None: fail('ELF/HEX 使用原生装载地址，不接受地址覆盖。')
    if fmt == 'hex':
        result, base, ended = [], 0, False
        try:
            for line in data.decode('ascii').splitlines():
                if not line.strip(): continue
                if ended or not re.fullmatch(r':[0-9a-fA-F]+', line): fail('HEX 记录或结束位置无效。')
                raw = bytes.fromhex(line[1:])
                if len(raw) < 5 or len(raw) != raw[0] + 5 or sum(raw) % 256: fail('HEX 长度或校验和错误。')
                count, offset, kind = raw[0], int.from_bytes(raw[1:3], 'big'), raw[3]
                body = raw[4:-1]
                if kind == 0:
                    if offset + count > 65536: fail('HEX 数据跨越地址窗口。')
                    if body: result.append((base + offset, body))
                elif kind == 1:
                    if count or offset: fail('HEX 结束记录无效。')
                    ended = True
                elif kind in (2, 4):
                    if count != 2 or offset: fail('HEX 扩展地址无效。')
                    base = int.from_bytes(body, 'big') << (4 if kind == 2 else 16)
                elif kind in (3, 5):
                    if count != 4 or offset: fail('HEX 入口记录无效。')
                else: fail('HEX 记录类型不受支持。')
            if not ended: fail('HEX 缺少结束记录。')
            return merge_sections(result)
        except (ValueError, UnicodeError): fail('HEX 编码无效。')
    if fmt != 'elf': fail('不支持的固件格式。')
    if len(data) < 52 or data[:7] != b'\x7fELF\x01\x01\x01': fail('仅支持 ARM ELF32 小端镜像。')
    header = struct.unpack_from('<HHIIIIIHHHHHH', data, 16)
    kind, machine, version, entry, phoff, shoff, flags, ehsize, phsize, phnum, *_ = header
    if kind != 2 or machine != 40 or version != 1 or ehsize != 52 or phsize != 32 or phnum > 1024:
        fail('ELF 不是支持的 ARM 可执行装载镜像。')
    if phoff + phnum * phsize > len(data): fail('ELF 程序头越界。')
    result = []
    for i in range(phnum):
        ptype, offset, vaddr, paddr, size, memsize, _, alignment = struct.unpack_from('<IIIIIIII', data, phoff + i * phsize)
        if ptype != 1: continue
        if size > memsize or offset + size > len(data): fail('ELF 装载段越界。')
        if size: result.append((paddr, data[offset:offset + size]))
    return merge_sections(result)


def merge_sections(rows):
    result = []
    for address, data in sorted(rows):
        if address < 0 or address + len(data) > 0x100000000: fail('固件地址越界。')
        if result and address < result[-1][0] + len(result[-1][1]): fail('固件装载区段重叠。')
        if result and address == result[-1][0] + len(result[-1][1]):
            result[-1][1].extend(data)
        else: result.append((address, bytearray(data)))
    if not result: fail('固件没有可烧录的数据。')
    return [(address, bytes(data)) for address, data in result]
