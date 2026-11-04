from typing import Annotated,Literal,Union
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .adapters.firmware_targets import STM32Target, get_target


class Strict(BaseModel):model_config=ConfigDict(extra='forbid')


class FirmwareSource(Strict):
    id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    path:str=Field(min_length=1,max_length=2000)
    sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    uri:str=Field(min_length=1,max_length=2000)
    version:str=Field(min_length=1,max_length=200)
    license:str=Field(min_length=1,max_length=1000)
    note:str=Field(min_length=1,max_length=2000)


class ReplaceFile(Strict):
    kind:Literal['replace_file']
    file:str=Field(min_length=1,max_length=200)
    expectedSha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    source:FirmwareSource


class AddFile(Strict):
    kind:Literal['add_file']
    file:str=Field(min_length=1,max_length=200)
    source:FirmwareSource


class RemoveFile(Strict):
    kind:Literal['remove_file']
    file:str=Field(min_length=1,max_length=200)
    expectedSha256:str=Field(pattern=r'^[0-9a-f]{64}$')


FileOperation=Annotated[Union[ReplaceFile,AddFile,RemoveFile],Field(discriminator='kind')]


class HardwareEvidence(Strict):
    schemaVersion:Literal[1]
    checkJobId:str=Field(pattern=r'^j_[0-9a-f]{16}$')
    revisionId:str=Field(pattern=r'^r_[0-9a-f]{16}$')
    inputManifestHash:str=Field(pattern=r'^[0-9a-f]{64}$')
    netlistArtifactId:str=Field(pattern=r'^a_[0-9a-f]{16}$')
    expectedNetlistSha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    ercArtifactId:str=Field(pattern=r'^a_[0-9a-f]{16}$')
    expectedErcSha256:str=Field(pattern=r'^[0-9a-f]{64}$')


class FirmwareDesign(Strict):
    expectedProjectVersion:int=Field(ge=1)
    expectedFirmwareRevisionId:str|None
    hardwareRevisionId:str
    expectedHardwareHash:str=Field(pattern=r'^[0-9a-f]{64}$')
    hardwareEvidence:HardwareEvidence
    targetReference:str=Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    profile:STM32Target='STM32G030F6P6'
    mode:Literal['template','edit']
    template:Literal['blink','uart_echo','i2c_probe_uart']|None=None
    periodMs:int|None=Field(default=None,ge=2,le=60000)
    baud:int|None=Field(default=None,ge=1200,le=1000000)
    pinBindings:dict[Literal['led','tx','rx','scl','sda'],str]=Field(min_length=1,max_length=5)
    ioVoltage:Literal[3.3]=3.3
    resetEntry:Literal['normal-reset-internal-flash-option-bytes-not-read']
    clock:Literal['nominal-hsi16-16mhz-no-bootloader-handover','nominal-hsi8-8mhz-no-bootloader-handover']
    requirements:str=Field(min_length=1,max_length=20000)
    assumptions:list[str]=Field(min_length=1,max_length=30)
    operations:list[FileOperation]=Field(default_factory=list,max_length=80)
    message:str=Field(min_length=1,max_length=1000)
    timeoutSeconds:int=Field(default=180,ge=10,le=600)
    requestId:str=Field(min_length=1,max_length=160)

    @model_validator(mode='after')
    def shape(self):
        target=get_target(self.profile)
        if self.clock!=target['clock']:raise ValueError('时钟配置与所选精确型号不匹配。')
        if self.template is not None and self.template not in target['templates']:raise ValueError('所选型号尚未适配此模板。')
        if self.mode=='template':
            if self.operations:raise ValueError('模板模式须选模板且不同时提交源码操作；重建模板会显式替换应用源码。')
            if self.template=='blink' and self.baud is not None:raise ValueError('闪灯模板不消费baud。')
            if self.template is not None and self.template!='blink' and self.periodMs is not None:raise ValueError('此模板不消费periodMs。')
        elif self.template is not None or self.periodMs is not None or self.baud is not None or not self.expectedFirmwareRevisionId or not self.operations:
            raise ValueError('源码编辑需要当前固件版本与明确文件操作，参数由源码决定，不接受模板参数。')
        return self
