"""Typed read/quote contracts. No supplier submission or payment request exists."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from .source_schemas import Strict


class JlcConfiguration(Strict):
    expectedConfigVersion: str | None
    applicationName: str = Field(min_length=1, max_length=80)
    appId: str = Field(pattern=r'^[1-9][0-9]{0,29}$')
    accessKey: SecretStr = Field(min_length=8, max_length=200)
    secretKey: SecretStr = Field(min_length=16, max_length=512)

    @field_validator('accessKey', 'secretKey')
    @classmethod
    def no_control_characters(cls, value):
        raw=value.get_secret_value()
        if raw != raw.strip() or any(ord(c)<32 or ord(c)==127 for c in raw):
            raise ValueError('密钥格式不正确。')
        return value

    @field_validator('accessKey')
    @classmethod
    def header_safe_key(cls, value):
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,200}',value.get_secret_value()):
            raise ValueError('访问标识格式不正确。')
        return value


class JlcPcbChoices(Strict):
    material: Literal['FR4']
    copperLayers: int = Field(strict=True,ge=1,le=32)
    widthMm: Decimal = Field(gt=0,le=2000,decimal_places=1,allow_inf_nan=False)
    heightMm: Decimal = Field(gt=0,le=2000,decimal_places=1,allow_inf_nan=False)
    boardQuantity: int = Field(strict=True,ge=1,le=1000000)
    thicknessMm: Decimal
    outerCopperOz: Literal[1,2]
    innerCopperOz: Literal[0.5,1,2] | None
    solderMask: Literal['绿色','红色','黄色','蓝色','白色','黑色','紫色']
    legend: Literal['白色','黑色']
    finish: Literal['有铅喷锡','无铅喷锡','沉金']
    goldThicknessU: Literal[1,2] | None = None
    minimumHoleCode: Literal[1,2,3,4]
    viaCover: Literal['过孔盖油','过孔开窗','过孔塞油','过孔塞树脂','过孔塞铜浆']
    test: Literal['不测试','抽测','全部测试','测试一半','随机抽测']
    delivery: Literal['normal_one','normal_three','fast_one','fast_two','normal_two','fast_three','normal_four','fast_four']
    invoice: str = Field(min_length=1,max_length=80)
    productionProof: Literal['确认二次','确认多次']
    note: str = Field(min_length=1,max_length=2000)

    @model_validator(mode='after')
    def supported_choices(self):
        if self.copperLayers not in {1,2,*range(4,33,2)}:
            raise ValueError('官方文档未列出此层数。')
        if self.thicknessMm not in {Decimal(v) for v in ['0.4','0.6','0.8','1.0','1.2','1.6','1.8','2.0','2.5','3.0']}:
            raise ValueError('板厚须来自官方可选项。')
        if (self.copperLayers>2) != (self.innerCopperOz is not None):
            raise ValueError('多层板须明确内铜厚；单/双层不选内铜。')
        if (self.finish=='沉金') != (self.goldThicknessU is not None):
            raise ValueError('沉金须明确金厚，其余表面处理不选金厚。')
        delivery={1:{'normal_one','normal_three','fast_one','fast_two'},2:{'normal_one','normal_three','fast_one','fast_two'},
                  4:{'normal_two','fast_two','fast_three'},6:{'normal_four','fast_four'}}
        if self.copperLayers in delivery and self.delivery not in delivery[self.copperLayers]:
            raise ValueError('交期与文档所列层数不匹配。')
        if self.copperLayers>6:
            raise ValueError('高于6层的交期尚未有明确映射，请先补充对应官方契约。')
        return self


class JlcQuoteRequest(Strict):
    expectedProjectVersion: int = Field(strict=True,ge=1)
    manufacturingJobId: str = Field(pattern=r'^j_[0-9a-f]{16}$')
    expectedGerberSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    choices: JlcPcbChoices
    requestId: str = Field(min_length=1,max_length=160)


class JlcSyncRequest(Strict):
    orderId: str = Field(pattern=r'^o_[0-9a-f]{16}$')
    expectedOrderVersion: int = Field(strict=True,ge=1)
    orderType: Literal['examples','batch']
    orderDateBegin: str
    orderDateEnd: str
    responseLengthUnit: Literal['cm','mm']
    requestId: str = Field(min_length=1,max_length=160)

    @model_validator(mode='after')
    def date_window(self):
        try:
            values=[datetime.strptime(v,'%Y-%m-%d %H:%M:%S') for v in [self.orderDateBegin,self.orderDateEnd]]
        except ValueError:raise ValueError('日期须为中国时区YYYY-MM-DD HH:mm:ss。') from None
        if any(d.strftime('%Y-%m-%d %H:%M:%S')!=v for d,v in zip(values,[self.orderDateBegin,self.orderDateEnd])) or not 0<=(values[1]-values[0]).total_seconds()<=365*86400:
            raise ValueError('日期范围须按先后顺序，且不超过365天。')
        return self
