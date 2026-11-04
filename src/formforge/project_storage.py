"""Transparent, lossless storage defaults for newly created project directories.

Only the project-root directory's inheritance attribute is changed. Existing
project directories/files, the database and tool environments are not traversed.
Reference: https://learn.microsoft.com/windows/win32/api/winioctl/ni-winioctl-fsctl_set_compression
"""
import ctypes
from functools import lru_cache
import os
from pathlib import Path
import stat


@lru_cache(maxsize=1)
def _windows():
    from ctypes import wintypes as w
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    api.CreateFileW.restype = w.HANDLE
    api.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, w.LPVOID, w.DWORD, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID]
    api.DeviceIoControl.restype = w.BOOL
    api.CloseHandle.argtypes = [w.HANDLE]
    api.CloseHandle.restype = w.BOOL
    api.GetCompressedFileSizeW.argtypes = [w.LPCWSTR, ctypes.POINTER(w.DWORD)]
    api.GetCompressedFileSizeW.restype = w.DWORD
    return api


def _native_path(path):
    value = str(path.absolute())
    if value.startswith('\\\\?\\'):
        return value
    return ('\\\\?\\UNC\\'+value[2:]) if value.startswith('\\\\') else ('\\\\?\\'+value)


def _enable_compression(path):
    from ctypes import wintypes as w
    api = _windows()
    # OPEN_EXISTING + FILE_FLAG_BACKUP_SEMANTICS opens a directory without creating files.
    handle = api.CreateFileW(_native_path(path), 0xC0000000, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        compression = ctypes.c_ushort(1)  # COMPRESSION_FORMAT_DEFAULT (LZNT1)
        returned = w.DWORD()
        if not api.DeviceIoControl(handle, 0x9C040, ctypes.byref(compression), ctypes.sizeof(compression),
                                   None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        api.CloseHandle(handle)


def prepare_project_storage(root: Path, target: Path | None = None):
    target = target or root/'data/projects'
    policy = dict(schemaVersion=1, enabled=False, mode='standard', scope='new-projects',
                  existingFilesChanged=False, directory=str(target), reason=None)
    try:
        # Only configure directories within this workspace, without following links.
        relative = target.absolute().relative_to(root.absolute())
        if not relative.parts or '..' in relative.parts:
            raise OSError('Compression target must be a workspace child directory.')
        current = root
        for component in relative.parts:
            path = current/component
            current = path
            if path.exists():
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise OSError('Project storage directory is a link or reparse point.')
        target.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt':
            policy['reason'] = 'Transparent NTFS compression is unavailable on this platform.'
            return policy
        if not target.stat().st_file_attributes & 0x800:
            _enable_compression(target)
        if not target.stat().st_file_attributes & 0x800:
            raise OSError('Project directory compression could not be verified.')
        policy.update(enabled=True, mode='ntfs-transparent')
    except (OSError, ValueError) as error:
        # Ordinary storage remains usable; surface the real availability in settings.
        policy['reason'] = str(error)
    return policy


def allocated_file_bytes(path: Path, info=None):
    """OS-reported stored file bytes, without reading file contents or following links."""
    info = info or path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise OSError('Stored size is not measured through links.')
    if os.name == 'nt':
        from ctypes import wintypes as w
        high = w.DWORD()
        ctypes.set_last_error(0)
        low = _windows().GetCompressedFileSizeW(_native_path(path), ctypes.byref(high))
        error = ctypes.get_last_error()
        if low == 0xFFFFFFFF and error:
            raise ctypes.WinError(error)
        return (high.value << 32) | low
    blocks = getattr(info, 'st_blocks', None)
    return blocks*512 if blocks is not None else None
