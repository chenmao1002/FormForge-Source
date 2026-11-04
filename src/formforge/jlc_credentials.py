"""Current-user Windows DPAPI vault, never an MCP secret store or plaintext file."""
from dataclasses import dataclass, field
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

from pydantic import ValidationError
from .adapters.importer import reparse
from .jlc_schemas import JlcConfiguration
from .store import DomainError, now


def protect(data:bytes, decrypt=False):
    if os.name!='nt':raise DomainError('dependency_missing','官方接口密钥当前需要Windows用户加密存储。')
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    source_buffer=ctypes.create_string_buffer(data);entropy_buffer=ctypes.create_string_buffer(b'FormForge/JLC/DPAPI/v1')
    source=Blob(len(data),ctypes.cast(source_buffer,ctypes.POINTER(ctypes.c_ubyte)))
    entropy=Blob(len(entropy_buffer.value),ctypes.cast(entropy_buffer,ctypes.POINTER(ctypes.c_ubyte)));output=Blob()
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL;kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not fn(ctypes.byref(source),None,ctypes.byref(entropy),None,None,1,ctypes.byref(output)):
        raise DomainError('credential_unavailable','Windows用户加密存储读取/保存失败，请在本机重新配置。')
    try:return ctypes.string_at(output.pbData,output.cbData)
    finally:kernel.LocalFree(ctypes.cast(output.pbData,ctypes.c_void_p))


@dataclass(frozen=True)
class JlcCredential:
    version: str
    applicationName: str
    appId: str = field(repr=False)
    accessKey: str = field(repr=False)
    secretKey: str = field(repr=False)
    fileSha256: str = field(repr=False)

    @property
    def applicationFingerprint(self):return hashlib.sha256(self.appId.encode('utf8')).hexdigest()


class JlcVault:
    def __init__(self,config):
        self.root=config.root;self.path=config.local/'jlc-api.dpapi';self.lock=threading.RLock()
        self.state_path=config.local/'jlc-api-verification.json'

    def _safe(self,path):
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise DomainError('invalid_input','官方接口配置路径必须在本项目中。')
        for p in [path,*path.parents]:
            if p==self.root:break
            if p.exists() and reparse(p):raise DomainError('invalid_input','官方接口配置不接受重解析路径。')

    def load(self):
        with self.lock:
            self._safe(self.path)
            if not self.path.is_file():raise DomainError('credential_missing','请先在网页“官方接口”中配置本机应用密钥。')
            try:
                cipher=self.path.read_bytes()
                if not 1<=len(cipher)<=16384:raise ValueError()
                value=json.loads(protect(cipher,True).decode('utf8'))
                config=JlcConfiguration.model_validate(value['configuration'])
                return JlcCredential(value['version'],config.applicationName,config.appId,
                    config.accessKey.get_secret_value(),config.secretKey.get_secret_value(),hashlib.sha256(cipher).hexdigest())
            except DomainError:raise
            except (ValueError,KeyError,UnicodeError,ValidationError):
                raise DomainError('credential_unavailable','本机接口配置无效，请重新配置；未回显密钥。') from None

    def unchanged(self,credential):
        self._safe(self.path)
        if not self.path.is_file() or hashlib.sha256(self.path.read_bytes()).hexdigest()!=credential.fileSha256:
            raise DomainError('version_conflict','操作期间本机接口配置已变化，请重新执行。',409)

    def profile(self):
        result=dict(configured=False,secureStorage='windows-current-user-dpapi',configurationAvailable=os.name=='nt',
                    configVersion=None,applicationName=None,authenticated=False,lastVerifiedAt=None,secretsReadable=False)
        try:
            credential=self.load();result.update(configured=True,configVersion=credential.version,applicationName=credential.applicationName)
            self._safe(self.state_path)
            if self.state_path.is_file():
                state=json.loads(self.state_path.read_text('utf8'))
                if state.get('configVersion')==credential.version:
                    result.update(authenticated=True,lastVerifiedAt=state['verifiedAt'])
        except DomainError as e:
            if e.code!='credential_missing':result['issue']=e.message
        except (OSError,ValueError,KeyError):result['issue']='本机接口验证状态需要重新核对。'
        return result

    def save(self,request):
        try:config=JlcConfiguration.model_validate(request)
        except (ValueError,ValidationError):raise DomainError('invalid_input','应用配置格式不正确，请检查编号和密钥；不会回显输入。') from None
        with self.lock:
            self._safe(self.path)
            current=self.load().version if self.path.is_file() else None
            if current!=config.expectedConfigVersion:raise DomainError('version_conflict','接口配置已改变，请刷新后再保存。',409)
            version='cfg_'+uuid.uuid4().hex
            value=dict(version=version,createdAt=now(),configuration=dict(expectedConfigVersion=None,applicationName=config.applicationName,
                appId=config.appId,accessKey=config.accessKey.get_secret_value(),secretKey=config.secretKey.get_secret_value()))
            cipher=protect(json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf8'))
            temporary=self.path.with_name('jlc-api-'+uuid.uuid4().hex+'.tmp');self._safe(temporary)
            try:
                temporary.write_bytes(cipher);os.replace(temporary,self.path)
            finally:temporary.unlink(missing_ok=True)
            return self.profile()

    def verified(self,credential):
        with self.lock:
            self.unchanged(credential);self._safe(self.state_path)
            self.state_path.write_text(json.dumps(dict(configVersion=credential.version,verifiedAt=now())),encoding='utf8')
