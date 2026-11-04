"""Bounded child-process trees. No shell, no host execution of built firmware.

Windows children start suspended, enter a kill-on-close job, then resume. This
closes the driver/cc1/assembler/linker cancellation race; it is not an OS sandbox.
"""
import ctypes
import os
import signal
import subprocess
import time
from pathlib import Path
from .store import DomainError


class WindowsTree:
    def __init__(self, process):
        from ctypes import wintypes as w
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);self.kernel=kernel;self.handle=None
        class BASIC(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),('LimitFlags',w.DWORD),
                      ('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',w.DWORD),
                      ('Affinity',ctypes.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(n,ctypes.c_ulonglong) for n in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
        class EXTENDED(ctypes.Structure):
            _fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        class THREAD(ctypes.Structure):
            _fields_=[('dwSize',w.DWORD),('cntUsage',w.DWORD),('th32ThreadID',w.DWORD),('th32OwnerProcessID',w.DWORD),('tpBasePri',w.LONG),('tpDeltaPri',w.LONG),('dwFlags',w.DWORD)]
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];kernel.CreateJobObjectW.restype=w.HANDLE
        kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];kernel.SetInformationJobObject.restype=w.BOOL
        kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];kernel.AssignProcessToJobObject.restype=w.BOOL
        kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle.restype=w.BOOL
        kernel.CreateToolhelp32Snapshot.argtypes=[w.DWORD,w.DWORD];kernel.CreateToolhelp32Snapshot.restype=w.HANDLE
        kernel.Thread32First.argtypes=[w.HANDLE,ctypes.POINTER(THREAD)];kernel.Thread32Next.argtypes=kernel.Thread32First.argtypes
        kernel.OpenThread.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenThread.restype=w.HANDLE
        kernel.ResumeThread.argtypes=[w.HANDLE];kernel.ResumeThread.restype=w.DWORD
        try:
            self.handle=kernel.CreateJobObjectW(None,None)
            if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
            limits=EXTENDED();limits.BasicLimitInformation.LimitFlags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.AssignProcessToJobObject(self.handle,int(process._handle)):raise ctypes.WinError(ctypes.get_last_error())
            snapshot=kernel.CreateToolhelp32Snapshot(4,0)
            if snapshot==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
            try:
                entry=THREAD();entry.dwSize=ctypes.sizeof(entry);more=kernel.Thread32First(snapshot,ctypes.byref(entry));resumed=False
                while more:
                    if entry.th32OwnerProcessID==process.pid:
                        handle=kernel.OpenThread(2,False,entry.th32ThreadID)
                        if not handle:raise ctypes.WinError(ctypes.get_last_error())
                        try:
                            if kernel.ResumeThread(handle)==0xffffffff:raise ctypes.WinError(ctypes.get_last_error())
                            resumed=True
                        finally:kernel.CloseHandle(handle)
                    more=kernel.Thread32Next(snapshot,ctypes.byref(entry))
                if not resumed:raise RuntimeError('No suspended process thread found')
            finally:kernel.CloseHandle(snapshot)
        except BaseException:
            self.close();process.kill();process.wait(timeout=10);raise

    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None


def run_process(argv, cwd:Path, log:Path, env, cancel, deadline, max_log_bytes=4*1024*1024):
    cancel();log.parent.mkdir(parents=True,exist_ok=True)
    kwargs=dict(cwd=cwd,env=env,stdout=None,stderr=subprocess.STDOUT)
    if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW|0x00000004 # CREATE_SUSPENDED
    else:kwargs['start_new_session']=True
    started=time.monotonic();tree=None
    with log.open('wb') as stream:
        kwargs['stdout']=stream
        process=subprocess.Popen(list(map(str,argv)),**kwargs)
        try:
            if os.name=='nt':tree=WindowsTree(process)
            while process.poll() is None:
                cancel()
                if time.monotonic()>=deadline:raise DomainError('tool_failed','固件构建达到时间预算。')
                if log.stat().st_size>max_log_bytes:raise DomainError('tool_failed','构建日志超过本轮额度。')
                time.sleep(.05)
        finally:
            if tree:tree.close()
            elif os.name!='nt':
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            elif process.poll() is None:process.kill()
            process.wait(timeout=10)
    cancel()
    if log.stat().st_size>max_log_bytes:raise DomainError('tool_failed','构建日志超过本轮额度。')
    return dict(argv=list(map(str,argv)),exitCode=process.returncode,elapsedSeconds=round(time.monotonic()-started,3),log=log.name,processTreeManaged=True)
