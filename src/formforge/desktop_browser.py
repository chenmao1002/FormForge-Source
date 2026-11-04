"""Authenticated loopback bridge to the quote-only Electron supplier view.

The desktop runtime owns supplier cookies. This bridge never reads its profile
and never retries a browser mutation after an uncertain result.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from .adapters.importer import digest, reparse
from .desktop_schemas import DesktopBrowserRequest
from .store import DomainError, now
from .stl_topology import inspect as inspect_stl


UPLOAD_SUFFIXES = {'.zip', '.csv', '.xlsx', '.step', '.stp', '.stl'}
READ_ACTIONS = {'inspect'}
PRIVATE_KEYS = {'token', 'accesstoken', 'authorization', 'cookie', 'cookies', 'password',
                'passwords', 'credentials', 'storagestate', 'localstorage', 'sessionstorage', 'secret'}
# Only these literal diagnostics originate in our Electron controller. Do not
# relay arbitrary supplier text or JavaScript exception messages to HTTP/MCP.
CONTROL_REASONS = {
    '不填写或修改供应商备注；平台既有内容保持原样。',
    '页面渲染准备超时，请重新观察。',
    '无法定位该控件，请重新观察。',
    '登录和个人资料由用户接管填写。',
    '该控件不可编辑，或需要用户接管。',
    '该确认控件的报价用途不明确，请人工接管。',
    '该控件不能填写。',
    '请使用上传动作选择已核对的制造文件。',
    '未识别控件用途，请人工接管。',
    '控件不在可操作区域，请重新观察或人工接管。',
    '控件被其他内容遮挡，请重新观察或人工接管。',
    '该选项不存在。',
    '该控件不是文件上传入口。',
}


def public_result(value):
    """Keep the local runtime's credential-bearing transport fields private."""
    if isinstance(value, dict):
        return {k: public_result(v) for k, v in value.items()
                if k.lower().replace('_', '') not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [public_result(v) for v in value]
    return value


class DesktopBrowser:
    def __init__(self, config):
        self.config = config
        self.directory = config.local / 'desktop'
        self.descriptor = self.directory / 'runtime.json'
        self.audit = self.directory / 'bridge-audit.jsonl'
        self.lock = asyncio.Lock()
        self._used = None

    def profile(self):
        # Presence is not a live browser, supplier login, or completed quote.
        return dict(adapter='electron-supplier-view/1',
                    runtimeDescriptorPresent=self.descriptor.is_file(),
                    quoteOnly=True, orderingEnabled=False, paymentEnabled=False,
                    productionReleaseEnabled=False,
                    statusEndpoint='/api/desktop/browser',
                    availableActions=list(DesktopBrowserRequest.model_json_schema()['properties']['action']['enum']),
                    uploadSuffixes=sorted(UPLOAD_SUFFIXES),
                    humanLoginRequired=True, credentialsReadable=False,
                    automaticMutationRetry=False)

    def schema(self):
        return dict(**self.profile(), requestSchema=DesktopBrowserRequest.model_json_schema(),
                    instructions='先打开Electron验证版。inspect返回当前可见控件ID；每次操作后重新inspect。'
                    '人工接管期间不得自动修改。密码、验证码由用户直接输入。'
                    '本轮只获取报价，禁止提交订单、付款和生产放行。'
                    '不填写或修改PCB、SMT、3D供应商备注；平台自动生成内容保持原样。'
                    '结果未知时先检查页面，不重发原修改请求。')

    def _runtime(self):
        try:
            for path in [self.descriptor, self.directory, self.config.local]:
                if reparse(path):
                    raise ValueError()
            if reparse(self.descriptor) or self.descriptor.stat().st_size > 8192:
                raise ValueError()
            runtime = json.loads(self.descriptor.read_text(encoding='utf-8-sig'))
            if (runtime.get('protocol') != 1 or type(runtime.get('port')) is not int
                    or not 1 <= runtime['port'] <= 65535
                    or not isinstance(runtime.get('token'), str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{24,200}', runtime['token'])
                    or type(runtime.get('pid')) is not int or runtime['pid'] < 1):
                raise ValueError()
            return runtime
        except (OSError, ValueError, KeyError, TypeError):
            raise DomainError('desktop_unavailable', 'Electron验证版尚未启动或连接资料不可用，请先打开桌面验证版。', 503) from None

    def _uploads(self, files, allowed_suffixes=None, max_bytes=128*1024*1024, *, check_stl=True):
        result = []
        allowed = [self.config.root / 'deliverables', self.config.data / 'projects',
                   self.config.root / 'validation']
        for item in files:
            original = Path(item['path'])
            original = original if original.is_absolute() else self.config.root / original
            path = original.resolve()
            if (not path.is_file() or path.suffix.lower() not in (UPLOAD_SUFFIXES if allowed_suffixes is None else allowed_suffixes)
                    or not any(path.is_relative_to(root.resolve()) for root in allowed)
                    or path.stat().st_size > max_bytes):
                raise DomainError('invalid_upload', '只允许上传本工程制造、交付或验证目录中的Gerber/BOM/CPL及外壳制造文件。')
            for parent in [original, *original.parents]:
                if parent == self.config.root:
                    break
                if reparse(parent):
                    raise DomainError('invalid_upload', '制造上传不接受符号链接或目录联接。')
            topology=None
            if check_stl and path.suffix.lower()=='.stl':
                try:topology=inspect_stl(path)
                except ValueError as error:
                    raise DomainError('invalid_stl','仅支持非空二进制STL，未发送文件：'+path.name) from error
            if (topology['sha256'] if topology else digest(path)) != item['sha256']:
                raise DomainError('file_mismatch', '上传文件与指定SHA-256不一致，未发送文件。', 409)
            if topology is not None and not topology['passed']:
                raise DomainError('invalid_stl','STL拓扑检查未通过，未发送文件：'+path.name+'；本地检查不代表壁厚或供应商审核。')
            result.append(dict(path=str(path), sha256=item['sha256']))
        return result

    def _load_used(self):
        if self._used is None:
            used = set()
            if self.audit.is_file():
                try:
                    for line in self.audit.read_text(encoding='utf-8').splitlines():
                        row = json.loads(line)
                        if row.get('phase') == 'started' and row.get('action') not in READ_ACTIONS:
                            used.add(row['requestId'])
                except (OSError, ValueError, KeyError):
                    raise DomainError('audit_unavailable', '浏览器执行记录无法读取，未重放任何修改操作。', 409) from None
            self._used = used

    def _append(self, value):
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.audit.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')

    async def _send(self, runtime, method, body=None):
        # Construct the destination ourselves; no URL or host is accepted from
        # the descriptor, MCP arguments, or supplier page.
        url = f"http://127.0.0.1:{runtime['port']}" + ('/status' if method == 'GET' else '/command')
        try:
            async with httpx.AsyncClient(timeout=45, trust_env=False, follow_redirects=False) as client:
                response = await client.request(method, url, json=body,
                    headers={'Authorization': 'Bearer ' + runtime['token']})
            if len(response.content) > 2 * 1024 * 1024:
                raise ValueError()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
        except (httpx.HTTPError, ValueError):
            raise DomainError('desktop_unavailable', '桌面浏览器未返回可核对结果；未自动重试，请检查页面后继续。', 503) from None
        if not response.is_success:
            code = result.get('error')
            messages = {
                'browser_unavailable': '供应商浏览器不可用，请打开桌面验证版。',
                'stale_element': '页面已变化，请重新inspect后选择当前控件。',
                'human_control': '用户正在接管浏览器，请等待用户完成并恢复智能体控制。',
                'quote_only': '本验证版只允许报价，已阻止订单、付款或生产操作。',
                'unsupported_destination': '页面离开已支持的供应商站点，未继续操作。',
                'file_mismatch': '上传文件或摘要已变化，未上传。',
                'unsupported_control': '该控件不支持此操作，未继续执行。',
                'invalid_input': '桌面浏览器拒绝了该请求，请重新检查当前页面和参数。',
                'print_order_mismatch': '3D原生页面的文件、数量、材料、确认方式或费用与复核不符。',
                'print_reconcile_required': '该批3D资料已有提交尝试，须先查已有订单，不能重复创建。',
                'print_unavailable': '当前桌面执行器未加载3D待付款提交能力，需按项目脚本更新后继续。',
            }
            if code not in messages:
                code = 'desktop_unavailable'
            message = messages.get(code, '桌面浏览器未完成操作，请检查当前页面。')
            if (code == 'unsupported_control' and isinstance(result.get('message'), str)
                    and result['message'] in CONTROL_REASONS):
                message = result['message']
            raise DomainError(code, message,
                              response.status_code if 400 <= response.status_code < 600 else 503)
        result = public_result(result)
        # URLs often contain account access identifiers. Page control remains
        # bound inside Electron; the agent only needs the visible route.
        if isinstance(result.get('url'), str):
            p = urlsplit(result['url'])
            result['url'] = urlunsplit((p.scheme, p.netloc, p.path, '', p.fragment.split('?')[0]))
        return result

    async def status(self):
        try:
            result = await self._send(self._runtime(), 'GET')
            return {**result, 'quoteOnly': True, 'orderingEnabled': False,
                    'paymentEnabled': False, 'productionReleaseEnabled': False}
        except DomainError as exc:
            return dict(available=False, error=exc.code, message=exc.message, **self.profile())

    async def control(self, request):
        parsed = request if isinstance(request, DesktopBrowserRequest) else DesktopBrowserRequest.model_validate(request)
        body = parsed.model_dump(mode='json', exclude_none=True)
        async with self.lock:
            started = now()
            clock = time.perf_counter()
            runtime = self._runtime()
            self._load_used()
            if body['action'] not in READ_ACTIONS and body['requestId'] in self._used:
                raise DomainError('request_reused', '该修改请求已执行或结果未知；请先inspect核对，不自动重放。', 409)
            if body['action'] == 'upload':
                body['files'] = await asyncio.to_thread(self._uploads, body['files'])
            audit = dict(requestId=body['requestId'], action=body['action'], startedAt=started,
                         provider=body.get('provider'), elementId=body.get('elementId'))
            if body.get('files'):
                audit['files'] = [dict(name=Path(f['path']).name, sha256=f['sha256']) for f in body['files']]
            self._append({**audit, 'phase': 'started'})
            if body['action'] not in READ_ACTIONS:
                self._used.add(body['requestId'])
            try:
                result = await self._send(runtime, 'POST', body)
            except DomainError as exc:
                self._append({**audit, 'phase': 'failed', 'error': exc.code, 'finishedAt': now(),
                              **({'reason': exc.message} if exc.code == 'unsupported_control' else {}),
                              'elapsedSeconds': time.perf_counter() - clock})
                raise
            self._append({**audit, 'phase': 'completed', 'finishedAt': now(),
                          'elapsedSeconds': time.perf_counter() - clock,
                          'pageVersion': result.get('pageVersion'), 'mode': result.get('mode')})
            return {**result, 'quoteOnly': True, 'orderingEnabled': False,
                    'paymentEnabled': False, 'productionReleaseEnabled': False}
