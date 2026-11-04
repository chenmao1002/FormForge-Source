"""Bounded JLC page adapter. No public selectors, scripts, credentials or payment actions.

Unverified multi-step supplier forms deliberately return a manual checkpoint, not
a successful upload/configuration/submission. The browser window remains usable.
"""
import re
from .store import DomainError, now


CONTROLS=('PCB下单','在线下单','上传Gerber','上传文件','上传BOM','上传坐标','器件匹配','提交订单','确认提交','等待您支付','待付款')


class JlcWebAdapter:
    def __init__(self,browser):
        self.browser=browser;self.owner=None;self.page=None

    def profile(self):
        return dict(adapter='jlc-owned-browser/1',actions=['open','inspect','reconcile'],
                    upload='manual-checkpoint',configure='manual-checkpoint',match='manual-checkpoint',
                    review='manual-checkpoint',submit=False,payment=False,productionRelease=False,
                    reason='当前真实平台已核对登录和订单列表；上传、工艺、匹配及提交表单尚未建立并验收稳定控件契约。')

    async def execute(self,attempt,action):
        async def operation():
            b=self.browser
            if action=='open':
                await b.ensure()
                # New orders also start at a known, read-only list. Never guess a
                # create URL or select an existing order's edit/resubmit action.
                await b.navigate(b.routes['smt' if attempt['provider']=='jlc-smt' else 'login'])
                await b.page.bring_to_front();self.owner=attempt['id'];self.page=b.page
            if not b.context:
                return dict(status='needs-login',message='先打开FormForge专用浏览器。',observedAt=now())
            await b.inspect()
            session=b.profile()
            if session['loginStatus']!='authenticated':
                return dict(status='needs-login',message='请在专用浏览器完成登录并检查状态。',observedAt=now())
            if attempt.get('accountFingerprint') and session['accountFingerprint']!=attempt['accountFingerprint']:
                raise DomainError('account_changed','当前平台账号与本次执行绑定账号不同；未操作订单。',409)
            if self.owner!=attempt['id']:
                return dict(status='needs-open',message='服务重启或浏览器工作区已切换；请先打开此任务，重新核对平台状态。',observedAt=now())
            page=self.page
            if page.is_closed() or not b.allowed_url(page.url):
                return dict(status='needs-open',message='专用工作页已关闭或离开支持的站点。',observedAt=now())
            number=(attempt.get('existingOrder') or {}).get('displayNumber')
            pattern=re.compile(r'(?<![A-Za-z0-9_-])'+re.escape(number)+r'(?![A-Za-z0-9_-])') if number else None
            if pattern:
                # An account header does not imply that the asynchronously
                # loaded order list has arrived. Wait for the exact order token,
                # with a hard bound; missing/filtered orders remain unknown.
                try:await page.get_by_text(pattern).first.wait_for(state='visible',timeout=7000)
                except Exception:pass
            # Read DOM text privately and emit only allowlisted business facts.
            text=(await page.locator('body').inner_text(timeout=7000))[:400000]
            visible=bool(number and re.search(r'(?<![A-Za-z0-9_-])'+re.escape(number)+r'(?![A-Za-z0-9_-])',text))
            snapshot=dict(observedAt=now(),evidenceLevel='formforge-owned-browser-visible-dom',
                accountLabel=session['accountLabel'],accountFingerprint=session['accountFingerprint'],
                pageKind='smt-order-list' if '/smt_list' in page.url else 'pcb-order-list' if 'pcbOrderList' in page.url else 'unverified-vendor-page',
                orderNumber=number,orderNumberVisible=visible,providerBytesVerified=False,
                recognizedControls=[label for label in CONTROLS if label in text],
                paymentStatus='unknown',productionStatus='unknown',dfmStatus='unknown',directionStatus='unknown')
            if visible:
                # Only a uniquely bounded row may contribute a status. A label
                # elsewhere on the page must never be attributed to this order.
                rows=page.locator('tr').filter(has_text=re.compile(r'(?<![A-Za-z0-9_-])'+re.escape(number)+r'(?![A-Za-z0-9_-])'))
                if await rows.count()==1:
                    row=await rows.inner_text()
                    unpaid=next((x for x in ('等待您支付','待付款','未支付') if x in row),None)
                    if unpaid:snapshot.update(paymentStatus='unpaid',paymentStatusLabel=unpaid)
                    snapshot['statusScope']='unique-table-row'
                else:snapshot['statusScope']='number-visible-only'
            if action in {'upload','configure','match','review','submit'}:
                return dict(status='manual-required',message=self.profile()['reason'],snapshot=snapshot,
                            providerActionExecuted=False,observedAt=now())
            status='existing-order-found' if visible else 'existing-order-not-visible' if number else 'ready-for-manual-form'
            return dict(status=status,message='只读核对完成；未改动平台订单。' if number else '已打开专用浏览器；下一步表单需要人工接管。',
                        snapshot=snapshot,providerActionExecuted=False,observedAt=now())
        return await self.browser.run(operation)
