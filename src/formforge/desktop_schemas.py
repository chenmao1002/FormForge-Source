"""Quote-only commands for the FormForge Electron supplier view."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .source_schemas import Strict


class DesktopUploadFile(Strict):
    path: str = Field(min_length=1, max_length=2000)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class DesktopBrowserRequest(Strict):
    action: Literal['open', 'navigate', 'inspect', 'click', 'fill', 'select', 'upload',
                    'handoff', 'resume', 'back', 'reload', 'focus-supplier', 'split-views']
    provider: Literal['jlc-pcb', 'jlc-smt', 'jlc-3dp'] | None = None
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    elementId: str | None = Field(default=None, pattern=r'^v[0-9]+-e[0-9]+$')
    value: str | None = Field(default=None, max_length=2000)
    files: list[DesktopUploadFile] | None = Field(default=None, min_length=1, max_length=8)
    requestId: str = Field(min_length=1, max_length=160)

    @model_validator(mode='after')
    def action_fields(self):
        if (self.action == 'open') != (self.provider is not None):
            raise ValueError('仅打开供应商页面时必须指定provider。')
        if (self.action == 'navigate') != (self.url is not None):
            raise ValueError('仅navigate接受url。')
        if self.url:
            url = urlsplit(self.url)
            host = (url.hostname or '').lower()
            if (url.scheme != 'https' or url.username or url.password or url.port not in (None, 443)
                    or not any(host == h or host.endswith('.' + h)
                               for h in ('jlc.com', 'jlc-smt.com', 'jlc-3dp.cn'))):
                raise ValueError('仅支持已核对的供应商HTTPS域名。')
        if (self.action in {'click', 'fill', 'select', 'upload'}) != (self.elementId is not None):
            raise ValueError('点击、填写、选择和上传必须指定当前快照中的elementId。')
        if (self.action in {'fill', 'select'}) != (self.value is not None):
            raise ValueError('仅填写或选择操作接受value。')
        if (self.action == 'upload') != (self.files is not None):
            raise ValueError('仅上传操作接受files。')
        return self
