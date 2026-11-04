"""Source bindings, units and strict identity checks, separate from HTTP transport."""
from decimal import Decimal, InvalidOperation
from datetime import datetime
import hashlib
import json
import math
from pathlib import PurePosixPath
import zipfile

from ..store import DomainError
from ..jlc_schemas import JlcPcbChoices


def fail(message):raise DomainError('provider_identity_mismatch',message,409)


def number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float,Decimal)):
        raise DomainError('provider_response_invalid','官方接口缺少明确的数值字段。')
    try:result=Decimal(str(value))
    except InvalidOperation:raise DomainError('provider_response_invalid','官方接口数值格式无效。') from None
    if not result.is_finite():raise DomainError('provider_response_invalid','官方接口数值不是有限值。')
    return result


def integer_id(value):
    if type(value)!=int or not 0<value<2**63:
        raise DomainError('provider_response_invalid','官方订单数值ID无效。')
    return str(value)


def quote_payload(choices):
    c=JlcPcbChoices.model_validate(choices)
    technical=dict(plateType=1,stencilLayer=c.copperLayers,stencilLength=float(c.widthMm/10),stencilWidth=float(c.heightMm/10),
        stencilCounts=c.boardQuantity,stencilPly=float(c.thicknessMm),cuprumThickness=c.outerCopperOz,stencilNumber=1,stencilType='stencilOne',
        adornColor=c.solderMask,charFontColor=c.legend,adornPut=c.finish,minHoleServiceCode=c.minimumHoleCode,adornBestrow=c.viaCover,
        testProduct=c.test,acheiveTimeType=c.delivery,madeSteel=False,intialMadeSmtFlag=False,madeSmt=False,
        guaranteeService=False,confirmProductionFile=True,confirmProductionFileType=c.productionProof)
    if c.innerCopperOz is not None:technical['insideCuprumThickness']=c.innerCopperOz
    if c.goldThicknessU is not None:technical['adornPutGoldThickness']=c.goldThicknessU
    return dict(invoice=c.invoice,orderTechnical=technical)


def quote_basis(orders,project,params):
    binding,_,job,artifacts=orders._binding(project,params['manufacturingJobId'],params['expectedGerberSha256'])
    choices=JlcPcbChoices.model_validate(params['choices']) if params.get('choices') else None
    if job['kind']=='manufacturing.external_review':
        report=next(a for a in artifacts if a['kind']=='external_cam_report')
        from .importer import within
        body=json.loads(within(orders.config.data,report['relativePath']).read_text('utf8'))
        geometry=dict(widthMm=body['outline']['widthMm'],heightMm=body['outline']['heightMm'],copperLayers=job['params']['copperLayerCount'])
    else:
        package,path=orders._artifact(project,job,'manufacturing_project')
        with zipfile.ZipFile(path) as z:
            manifest=json.loads(z.read('manufacturing-files.json'))
            rows=[r for r in manifest if r['path']=='native/board.json']
            if len(rows)!=1 or z.getinfo('native/board.json').file_size>16*1024*1024:
                raise DomainError('input_changed','原生制造包缺少唯一板几何。',409)
            raw=z.read('native/board.json')
            if hashlib.sha256(raw).hexdigest()!=rows[0]['sha256']:raise DomainError('input_changed','原生板几何SHA不符。',409)
            native=json.loads(raw)
        edges=native['edges']
        if native['units']!='mm' or not edges or any(e['shape']!=0 for e in edges):
            raise DomainError('unsupported_capability','此原生轮廓尚无完整计价尺寸提取，请先运行独立CAM审核。')
        points=[p for e in edges for p in [e['start'],e['end']]]
        if any(not math.isfinite(v) for p in points for v in p):raise DomainError('input_changed','板轮廓包含无效坐标。',409)
        geometry=dict(widthMm=max(p[0] for p in points)-min(p[0] for p in points),heightMm=max(p[1] for p in points)-min(p[1] for p in points),
            copperLayers=len(native['copperLayers']),thicknessMm=native['nominalThicknessMm'])
        binding['geometryPackage']=dict(artifactId=package['id'],sha256=package['sha256'],nativeSha256=rows[0]['sha256'])
    if choices and (choices.copperLayers!=geometry['copperLayers'] or any(abs(float(getattr(choices,k))-geometry[k])>.01 for k in ['widthMm','heightMm'])):
        fail('计价的层数/尺寸与固定制造资料不一致。')
    if choices and 'thicknessMm' in geometry and abs(float(choices.thicknessMm)-geometry['thicknessMm'])>1e-6:fail('计价板厚与原生名义板厚不一致。')
    binding['geometry']=geometry
    return binding


def match_order(data,order,request,customer_id):
    if not isinstance(data,dict):raise DomainError('provider_response_invalid','官方订单响应必须是对象。')
    if integer_id(data.get('customerOrderId'))!=customer_id:fail('官方响应的客户订单ID与查询目标不一致。')
    produce_id=integer_id(data.get('produceOrderId'))
    if data.get('produceCode')!=order['identity']['displayNumber']:fail('官方生产编号与本地订单不一致。')
    previous=order.get('apiIdentity')
    if previous and (previous['customerOrderId']!=customer_id or previous['orderType']!=request['orderType'] or previous['produceOrderId']!=produce_id):
        fail('官方数值身份与既有API关联不一致。')
    filename=data.get('orderPcbFileName')
    expected=order['latestObservation'].get('fileNameObserved')
    if not expected or not isinstance(filename,str) or PurePosixPath(filename).name!=filename or filename!=expected:
        fail('官方文件名与本地已观察文件名不一致；不会据此创建新订单。')
    p=order['parameters'];factor=10 if request['responseLengthUnit']=='cm' else 1
    if any(abs(number(data.get(api))*factor-Decimal(str(p[key])))>Decimal('.01') for api,key in [('stencilLength','widthMm'),('stencilWidth','heightMm')]):
        fail('官方尺寸与本地订单不符，请核对查询响应单位。')
    if number(data.get('stencilLayer'))!=p['copperLayers'] or number(data.get('stencilPly'))!=Decimal(str(p['thicknessMm'])) or number(data.get('stencilCounts'))!=p['boardQuantity']:
        fail('官方层数、数量或板厚与本地订单不一致。')
    date=data.get('orderDate')
    if not isinstance(date,str) or not request['orderDateBegin']<=date<=request['orderDateEnd']:fail('官方下单时间不在明确查询窗口。')
    try:valid_date=datetime.strptime(date,'%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M:%S')==date
    except ValueError:valid_date=False
    if not valid_date:fail('官方下单时间格式未按文档返回。')
    status=data.get('orderStatus')
    if type(status)!=int or status not in range(6):raise DomainError('provider_response_invalid','官方返回了未识别的订单状态，保留既有观察。')
    return dict(customerOrderId=customer_id,produceOrderId=produce_id,orderType=request['orderType'],produceCode=data['produceCode'],
        fileName=filename,orderStatus=status,orderDate=date,copperLayers=p['copperLayers'],widthMm=p['widthMm'],heightMm=p['heightMm'],
        boardQuantity=p['boardQuantity'],thicknessMm=p['thicknessMm'],responseLengthUnit=request['responseLengthUnit'])


def quote_fields(data):
    if not isinstance(data,dict) or data.get('internetPayTotalMoney') is None:raise DomainError('provider_response_invalid','官方计价未提供明确网络计价金额。')
    fields={}
    allowed={'expressMoney','internetPayTotalMoney','stencilMoney','spellMoney','adornPutMoney','projectMoney','testMoney','fillMoney',
        'achieveMoney','charFontColor','cuprumThicknessMoney','bigBoardMoney','specialMoney','specialProcessMoney','orderWeight',
        'adornBestrowMoney','surchargeMoney','stencilDeductMoney','halfHoleMoney','hdCharMoeny','lowResistancetestMoeny',
        'specifyBoardMoney','guaranteeMoney','confirmFileMoney','edgeGrindingMoney','impedanceMoney','taxMoney'}
    for key,value in data.items():
        if key in allowed:
            fields[key]=None if value is None else str(number(value))
    services=data.get('servicePriceList') or []
    if not isinstance(services,list) or len(services)>100:raise DomainError('provider_response_invalid','官方费用条目结构或数量无效。')
    safe=[]
    for row in services:
        if not isinstance(row,dict) or not all(isinstance(row.get(k),str) and len(row[k])<=200 for k in ['serviceCode','serviceName']):
            raise DomainError('provider_response_invalid','官方服务费用名称无效。')
        safe.append(dict(serviceCode=row['serviceCode'],serviceName=row['serviceName'],serviceMoney=None if row.get('serviceMoney') is None else str(number(row['serviceMoney']))))
    return dict(amount=fields['internetPayTotalMoney'],currency='CNY',currencyBasis='本次中国站工艺计价约定',fields=fields,services=safe,
        shippingAmount=None,paid=False,scope='工艺在线计价；未上传原件、不含全部旧订单选项，不覆盖原订单金额或生产资格')
