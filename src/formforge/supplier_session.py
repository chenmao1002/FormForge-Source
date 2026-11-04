"""Owned local browser, private current-user session cache, no credential MCP API."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

from .adapters.importer import reparse
from .jlc_credentials import protect
from .store import DomainError, now


JLC_ROUTES = dict(login='https://www.jlc.com/newOrder/#/pcb/pcbOrderList',
                  pcb='https://www.jlc.com/newOrder/#/pcb',
                  smt='https://member.jlc.com/center/smt/home/order/smt_list/')
VENDOR_DOMAINS = ('jlc.com', 'jlc-smt.com')


def vendor_url(url):
    p=urlsplit(url);host=(p.hostname or '').lower()
    return p.scheme=='https' and not p.username and not p.password and any(host==d or host.endswith('.'+d) for d in VENDOR_DOMAINS)


def browser_channel():
    choices=[('msedge',Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')),
             ('msedge',Path(os.environ.get('PROGRAMFILES',r'C:\Program Files'))/'Microsoft/Edge/Application/msedge.exe'),
             ('chrome',Path(os.environ.get('PROGRAMFILES',r'C:\Program Files'))/'Google/Chrome/Application/chrome.exe')]
    return next((name for name,path in choices if path.is_file()),None)


class SessionVault:
    def __init__(self,config):
        self.config=config;self.root=config.local/'supplier-browser';self.path=self.root/'session.dpapi'
        self.metadata_path=self.root/'session-info.json'

    def safe(self,path):
        if not path.resolve().is_relative_to(self.config.local.resolve()):raise DomainError('invalid_input','浏览器缓存必须留在FormForge本机目录。')
        for p in [path,*path.parents]:
            if p==self.config.root:break
            if p.exists() and reparse(p):raise DomainError('invalid_input','浏览器会话目录不接受重解析路径。')

    def prepare(self):
        self.safe(self.root);self.root.mkdir(parents=True,exist_ok=True)
        if os.name!='nt':raise DomainError('dependency_missing','本版浏览器会话需要Windows当前用户保护。')
        import win32api,win32con,win32security
        token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
        sid=win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
        result=subprocess.run(['icacls',str(self.root),'/inheritance:r','/grant:r',f'*{sid}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],
            capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:raise DomainError('session_unavailable','无法为专用会话目录设置本机用户权限。')

    def info(self):
        self.safe(self.path);self.safe(self.metadata_path)
        if not self.path.is_file():return dict(cacheSaved=False,savedAt=None,accountLabel=None)
        try:
            m=json.loads(self.metadata_path.read_text('utf8'))
            return dict(cacheSaved=True,savedAt=m['savedAt'],accountLabel=m.get('accountLabel'))
        except (OSError,ValueError,KeyError):return dict(cacheSaved=True,savedAt=None,accountLabel=None)

    def load(self):
        self.safe(self.path)
        if not self.path.exists():return None
        try:
            raw=self.path.read_bytes()
            if not 1<=len(raw)<=16*1024*1024:raise ValueError()
            value=json.loads(protect(raw,True).decode('utf8'))
            if value['kind']!='formforge.supplier-session/1':raise ValueError()
            return value['state']
        except DomainError:raise
        except (OSError,ValueError,KeyError,UnicodeError):raise DomainError('session_unavailable','保存的浏览器会话无法读取，请重新登录；未回显会话内容。') from None

    def save(self,state,account_label):
        self.prepare()
        value=dict(kind='formforge.supplier-session/1',state=state,savedAt=now())
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf8')
        if len(raw)>12*1024*1024:raise DomainError('session_unavailable','浏览器会话过大，未保存。')
        temporary=self.root/('session-'+uuid.uuid4().hex+'.tmp');self.safe(temporary)
        try:
            temporary.write_bytes(protect(raw));os.replace(temporary,self.path)
            self.metadata_path.write_text(json.dumps(dict(savedAt=value['savedAt'],accountLabel=account_label),ensure_ascii=False),encoding='utf8')
        finally:temporary.unlink(missing_ok=True)
        return self.info()


class SupplierBrowser:
    """All Playwright objects stay on one owned Proactor loop on Windows."""
    def __init__(self,config,on_change=None,*,routes=None,allowed_url=None,headless=False):
        self.config=config;self.vault=SessionVault(config);self.on_change=on_change
        # Private injection is for isolated local fixtures, never HTTP/MCP input.
        self.routes=routes or JLC_ROUTES;self.allowed_url=allowed_url or vendor_url;self.headless=headless
        self.loop=None;self.thread=None;self.start_lock=threading.Lock();self.state_lock=threading.RLock()
        self._state=dict(browserRunning=False,loginStatus='not-checked',lastCheckedAt=None,accountLabel=None,accountFingerprint=None,issue=None)
        self.pw=None;self.context=None;self.page=None;self.monitor=None;self.operation_lock=None;self.last_save=0.;self.closing=False

    def profile(self):
        with self.state_lock:state=dict(self._state)
        try:cache=self.vault.info()
        except DomainError:cache=dict(cacheSaved=False,savedAt=None,accountLabel=None)
        state.update(available=bool(importlib.util.find_spec('playwright')) and bool(browser_channel()) and os.name=='nt',
            browser=browser_channel(),cacheSaved=cache['cacheSaved'],savedAt=cache['savedAt'],
            cachedAccountLabel=cache['accountLabel'],storage='current-user-directory-and-dpapi-snapshot',
            passwordsReadable=False,sessionReadable=False,automaticPayment=False,productionRelease=False)
        return state

    def _update(self,**values):
        with self.state_lock:
            before={k:self._state.get(k) for k in values if k!='lastCheckedAt'}
            self._state.update(values)
            changed=any(before[k]!=values[k] for k in before)
        if changed and self.on_change:self.on_change(self.profile())

    def _start_loop(self):
        with self.start_lock:
            if self.loop and self.thread and self.thread.is_alive():return
            ready=threading.Event()
            def run():
                loop=asyncio.ProactorEventLoop() if os.name=='nt' else asyncio.new_event_loop()
                asyncio.set_event_loop(loop);self.loop=loop;self.operation_lock=asyncio.Lock();ready.set();loop.run_forever();loop.close()
            self.thread=threading.Thread(target=run,name='FormForge-supplier-browser',daemon=True);self.thread.start()
            if not ready.wait(3):raise DomainError('session_unavailable','浏览器工作线程未就绪。')

    async def run(self,operation):
        self._start_loop()
        async def locked():
            async with self.operation_lock:
                return await operation()
        future=asyncio.run_coroutine_threadsafe(locked(),self.loop)
        return await asyncio.wrap_future(future)

    async def command(self,action):
        async def execute():
            try:
                if action=='open-login':
                    await self.ensure();await self.navigate(self.routes['login']);await self.page.bring_to_front();await self.inspect()
                elif action=='check':
                    if self.context:await self.inspect()
                    else:self._update(loginStatus='browser-closed',issue=None)
                elif action=='save':
                    if not self.context:raise DomainError('session_unavailable','请先打开专用浏览器并登录。')
                    await self.inspect()
                    if self._state['loginStatus']!='authenticated':raise DomainError('login_required','尚未从平台页面核实登录，请完成登录后再保存。')
                    await self.save()
                elif action=='close':await self.close_context()
                elif action=='forget':
                    await self.close_context(save=False)
                    self.vault.safe(self.vault.root)
                    # Only this owned profile/cache is removed, never another browser profile.
                    if self.vault.root.exists():shutil.rmtree(self.vault.root)
                    self._update(loginStatus='not-checked',accountLabel=None,accountFingerprint=None,issue=None)
                return self.profile()
            except DomainError:raise
            except Exception:
                self._update(issue='浏览器操作未完成；可重新打开登录页或检查窗口。')
                raise DomainError('browser_failed','专用浏览器操作未完成；没有回显页面、地址参数或会话。') from None
        return await self.run(execute)

    async def ensure(self):
        if self.context:
            try:
                if self.context.pages:
                    if not self.page or self.page.is_closed():self.page=self.context.pages[-1]
                    return
            except Exception:pass
            self.context=None;self.page=None
            if self.pw:await self.pw.stop();self.pw=None
        if not importlib.util.find_spec('playwright') or not browser_channel():raise DomainError('dependency_missing','需要Playwright和本机Edge或Chrome；请先完成环境安装。')
        from playwright.async_api import async_playwright
        self.vault.prepare();profile=self.vault.root/'profile';self.vault.safe(profile)
        self.pw=await async_playwright().start()
        try:
            self.context=await self.pw.chromium.launch_persistent_context(str(profile),channel=browser_channel(),headless=self.headless,
                viewport=dict(width=1280,height=900),locale='zh-CN',accept_downloads=True,timeout=30000,
                args=['--no-first-run','--no-default-browser-check','--disable-features=msEdgeSidebarV2'])
            self.context.set_default_timeout(7000);self.context.set_default_navigation_timeout(25000)
            await self.context.route('**/*',self._route)
            state=self.vault.load()
            if state:
                await self.context.add_cookies(state.get('cookies',[]))
                # Profile normally preserves local storage/IndexedDB. Cookies are restored from the protected snapshot too.
            self.page=self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.context.on('close',lambda *_:self._update(browserRunning=False,loginStatus='browser-closed'))
            self._update(browserRunning=True,loginStatus='not-checked',issue=None)
            if not self.monitor or self.monitor.done():self.monitor=asyncio.create_task(self._monitor())
        except Exception:
            if self.pw:await self.pw.stop()
            self.pw=None;self.context=None;raise

    async def _route(self,route):
        request=route.request
        if request.is_navigation_request() and request.frame==request.frame.page.main_frame and not self.allowed_url(request.url) and request.url!='about:blank':
            self._update(issue='浏览器遇到未支持的站点跳转，已停止该导航。');await route.abort();return
        await route.continue_()

    async def navigate(self,url):
        if not self.allowed_url(url):raise DomainError('unsupported_capability','不支持的供应商页面。')
        await self.ensure();await self.page.goto(url,wait_until='domcontentloaded')
        # Supplier SPA headers and SSO redirects settle after DOMContentLoaded.
        # A bounded DOM-only wait avoids reporting a transient empty header as
        # expired login. No order action is performed during these checks.
        for index in range(13):
            await self.inspect()
            if self._state['loginStatus']=='authenticated':break
            if index<12:await asyncio.sleep(.5)

    async def inspect(self):
        if not self.context:return self.profile()
        if not self.context.pages:
            self._update(browserRunning=False,loginStatus='browser-closed');return self.profile()
        pages=[p for p in self.context.pages if not p.is_closed() and self.allowed_url(p.url)]
        code=None;login=False
        for page in reversed(pages):
            try:
                text=(await page.locator('body').inner_text(timeout=2500))[:300000]
                match=re.search(r'(?:客编|客户编码|客户编号)\s*[:：]?\s*([A-Za-z0-9_-]{4,80})',text)
                if match:code=match[1];self.page=page;break
                login=login or bool(re.search(r'短信登录|密码登录|扫码登录|登录后|手机号.*验证码',text,re.S))
            except Exception:continue
        if code:
            label='客编 ****'+code[-4:];fp=hashlib.sha256(code.encode()).hexdigest()
            self._update(loginStatus='authenticated',lastCheckedAt=now(),accountLabel=label,accountFingerprint=fp,issue=None)
            if time.monotonic()-self.last_save>60:await self.save()
        elif login:self._update(loginStatus='login-required',lastCheckedAt=now(),accountLabel=None,accountFingerprint=None,issue=None)
        else:self._update(loginStatus='not-checked',lastCheckedAt=now())
        return self.profile()

    async def save(self):
        if not self.context:return
        state=await self.context.storage_state(indexed_db=True)
        state['cookies']=[c for c in state.get('cookies',[]) if self.allowed_url('https://'+c['domain'].lstrip('.')+'/')]
        state['origins']=[o for o in state.get('origins',[]) if self.allowed_url(o['origin'])]
        self.vault.save(state,self._state.get('accountLabel'));self.last_save=time.monotonic()
        if self.on_change:self.on_change(self.profile())

    async def _monitor(self):
        while self.context and not self.closing:
            await asyncio.sleep(3)
            if self.operation_lock.locked():continue
            async with self.operation_lock:
                try:await self.inspect()
                except Exception:pass

    async def close_context(self,save=True):
        if self.context:
            if save and self._state.get('loginStatus')=='authenticated':await self.save()
            await self.context.close();self.context=None;self.page=None
        if self.pw:await self.pw.stop();self.pw=None
        self._update(browserRunning=False,loginStatus='browser-closed',issue=None)

    async def shutdown(self):
        if not self.loop:return
        async def close():
            self.closing=True
            if self.monitor:self.monitor.cancel();await asyncio.gather(self.monitor,return_exceptions=True)
            await self.close_context()
        try:await self.run(close)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            await asyncio.to_thread(self.thread.join,3)
            self.loop=None;self.thread=None
