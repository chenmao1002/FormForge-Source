"""Fixed-host official JOP client. Whitelist contains only query/price POST APIs."""
from dataclasses import dataclass
from decimal import Decimal
import base64
import hashlib
import hmac
import json
import re
import secrets
import string
import time

import httpx
from ..store import DomainError,now

ENDPOINT='https://open-api.jlc.com'
SMT_COMPONENT_PATH='/smtOpenApi/smtComponent/selectComponentInfoByCodes'
READ_PATHS={'/pcb/order/getOrderPageList','/pcb/order/get','/pcb/order/getOrderWebPriceDetails','/pcb/onlinePrice/get',SMT_COMPONENT_PATH}


def json_bytes(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')


def signature(secret_key,path,body,timestamp,nonce):
    if not path.startswith('/') or '\n' in path or type(timestamp)!=int or timestamp<0 or not re.fullmatch(r'[A-Za-z0-9]{32}',nonce):
        raise ValueError('Invalid canonical request')
    canonical=b'POST\n'+path.encode('ascii')+b'\n'+str(timestamp).encode('ascii')+b'\n'+nonce.encode('ascii')+b'\n'+body+b'\n'
    return base64.b64encode(hmac.new(secret_key.encode('utf8'),canonical,hashlib.sha256).digest()).decode('ascii')


def authorization(credential,path,body,*,timestamp=None,nonce=None):
    if path not in READ_PATHS:raise DomainError('unsupported_capability','当前只开放官方查单、计价和SMT器件查询接口。')
    timestamp=int(time.time()) if timestamp is None else timestamp
    nonce=''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)) if nonce is None else nonce
    if type(timestamp)!=int or timestamp<0 or not re.fullmatch(r'[A-Za-z0-9]{32}',nonce):raise ValueError('Invalid timestamp or nonce')
    signed=signature(credential.secretKey,path,body,timestamp,nonce)
    return f'JOP appid="{credential.appId}",accesskey="{credential.accessKey}",timestamp="{timestamp}",nonce="{nonce}",signature="{signed}"'


def reject_constant(value):raise ValueError('Non-finite JSON')


def unique_object(pairs):
    value={}
    for k,v in pairs:
        if k in value:raise ValueError('Duplicate JSON key')
        value[k]=v
    return value


@dataclass(frozen=True)
class JlcReply:
    path:str
    request:dict
    response:dict
    responseSha256:str
    requestSha256:str
    traceId:str|None
    receivedAt:str


class JlcClient:
    def __init__(self,credential,vault,*,transport=None,cancel=lambda:None,deadline=None):
        self.credential,self.vault,self.cancel=credential,vault,cancel
        self.deadline=deadline or time.monotonic()+45
        self.client=httpx.Client(base_url=ENDPOINT,transport=transport,follow_redirects=False,trust_env=False,
                                 timeout=httpx.Timeout(10,connect=5,pool=5))
        self.replies=[]
        self.attempts=[]

    def close(self):self.client.close()

    def post(self,path,payload):
        self.cancel();self.vault.unchanged(self.credential)
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise DomainError('provider_timeout','官方接口操作达到时间预算。')
        body=json_bytes(payload);auth=authorization(self.credential,path,body)
        attempt=dict(path=path,startedAt=now(),finishedAt=None,httpStatus=None,businessCode=None,providerSuccess=None,
            requestSha256=hashlib.sha256(body).hexdigest(),responseSha256=None,traceId=None,
            responseComplete=False,operationSucceeded=False,failureCode=None)
        try:
            return self._post(path,body,auth,remaining,attempt)
        except DomainError as error:
            attempt['failureCode']=error.code
            raise
        finally:
            attempt['finishedAt']=now()
            self.attempts.append(attempt)

    def _post(self,path,body,auth,remaining,attempt):
        try:
            with self.client.stream('POST',path,content=body,headers={'Authorization':auth,'Content-Type':'application/json','Accept':'application/json'},timeout=min(10,remaining)) as response:
                attempt['httpStatus']=response.status_code
                trace=response.headers.get('J-Trace-ID')
                if trace is not None and (not re.fullmatch(r'[A-Za-z0-9:_-]{1,180}',trace) or
                    any(value in trace for value in (self.credential.appId,self.credential.accessKey,self.credential.secretKey))):trace=None
                attempt['traceId']=trace
                if response.status_code!=200:
                    raise DomainError('provider_http_error',f'官方接口HTTP {response.status_code}；请核对密钥、权限和IP白名单。')
                if response.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':
                    raise DomainError('provider_response_invalid','官方接口没有返回JSON。')
                chunks=[];size=0
                for chunk in response.iter_bytes():
                    self.cancel();size+=len(chunk)
                    if size>2*1024*1024:raise DomainError('provider_response_invalid','官方接口响应超过2MiB限制。')
                    if time.monotonic()>self.deadline:raise DomainError('provider_timeout','官方接口读取达到时间预算。')
                    chunks.append(chunk)
                raw=b''.join(chunks)
                attempt.update(responseComplete=True,responseSha256=hashlib.sha256(raw).hexdigest())
        except httpx.TimeoutException:raise DomainError('provider_timeout','官方接口请求超时；未自动重试。') from None
        except httpx.HTTPError:raise DomainError('provider_unavailable','官方接口连接失败；未输出鉴权信息或自动重试。') from None
        self.cancel();self.vault.unchanged(self.credential)
        try:value=json.loads(raw.decode('utf8'),parse_float=Decimal,parse_constant=reject_constant,object_pairs_hook=unique_object)
        except (ValueError,UnicodeError,RecursionError):raise DomainError('provider_response_invalid','官方接口JSON格式无效。') from None
        # Only this exact SMT read endpoint permits its documented alternate field.
        success_key='successful' if path==SMT_COMPONENT_PATH and isinstance(value,dict) and 'successful' in value else 'success'
        if isinstance(value,dict):
            code=value.get('code')
            if type(code)==int and -999999<=code<=999999:attempt['businessCode']=code
            if type(value.get(success_key))==bool:attempt['providerSuccess']=value[success_key]
        contradictory=isinstance(value,dict) and path==SMT_COMPONENT_PATH and 'successful' in value and 'success' in value and value['successful'] is not value['success']
        if not isinstance(value,dict) or value.get(success_key) is not True or type(value.get('code'))!=int or value['code']!=200 or contradictory:
            code=attempt['businessCode'] if attempt['businessCode'] is not None else '未知'
            raise DomainError('provider_business_error',f'官方接口业务未成功（代码{code}）；未回显响应中的私人资料。')
        reply=JlcReply(path,json.loads(body),value,hashlib.sha256(raw).hexdigest(),hashlib.sha256(body).hexdigest(),trace,now())
        attempt['operationSucceeded']=True
        self.replies.append(reply)
        return reply
