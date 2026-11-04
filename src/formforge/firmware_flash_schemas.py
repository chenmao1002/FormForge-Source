"""Offline flash contracts. None of these requests authorizes device I/O."""
from typing import Literal
from pydantic import Field, model_validator
from .schemas import StrictModel
from .source_schemas import RegisteredSourceRef
from .adapters.firmware_targets import STM32Target, TARGETS
from typing import Union

FlashTarget = Union[STM32Target, Literal['esp32','esp32s3','esp32c3']]


class FlashImage(StrictModel):
    source: RegisteredSourceRef
    member: str | None = Field(default=None, max_length=500)
    format: Literal['bin', 'hex', 'elf']
    role: Literal['firmware', 'bootloader', 'application', 'partition-table', 'data', 'merged'] = 'firmware'
    address: int | None = Field(default=None, ge=0, le=0xffffffff)


class FlashBundleCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    target: FlashTarget
    flashSize: int | None = Field(default=None, ge=1024*1024, le=32*1024*1024)
    images: list[FlashImage] = Field(default_factory=list, max_length=32)
    archive: RegisteredSourceRef | None = None
    manifest: str = Field(default='flasher_args.json', max_length=500)
    requestId: str = Field(min_length=1, max_length=160)

    @model_validator(mode='after')
    def shape(self):
        if bool(self.images) == bool(self.archive):
            raise ValueError('选择明确的文件列表或带清单的 ZIP，两者不能同时使用。')
        if self.target in TARGETS and self.flashSize is not None:
            raise ValueError('STM32 Flash 容量由精确型号固定。')
        if self.target not in TARGETS and self.flashSize is None:
            raise ValueError('ESP 离线计划需要声明 Flash 容量，实物容量仍待连接核对。')
        if self.flashSize and self.flashSize & (self.flashSize - 1):
            raise ValueError('ESP Flash 容量须为 1/2/4/8/16/32 MiB。')
        return self


class FlashPlanCreate(StrictModel):
    bundleId: str = Field(pattern=r'^fb_[0-9a-f]{16}$')
    expectedBundleSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    uncoveredErasePolicy: Literal['reject', 'erase-declared-sectors'] = 'reject'
    eraseReason: str | None = Field(default=None, min_length=1, max_length=1000)
    requestId: str = Field(min_length=1, max_length=160)

    @model_validator(mode='after')
    def reason(self):
        if self.uncoveredErasePolicy != 'reject' and not (self.eraseReason or '').strip():
            raise ValueError('扩大到完整擦除扇区须记录原因；这不授权实物擦除。')
        return self


class FlashInternalCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    revisionId: str = Field(pattern=r'^r_[0-9a-f]{16}$')
    buildJobId: str = Field(pattern=r'^j_[0-9a-f]{16}$')
    artifactId: str = Field(pattern=r'^a_[0-9a-f]{16}$')
    expectedSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    dependencyReviewReason: str | None = Field(default=None, max_length=1000)
    requestId: str = Field(min_length=1, max_length=160)


class FlashDeviceRequest(StrictModel):
    action: Literal['discover', 'identify', 'program', 'verify']
    backend: Literal['pyocd', 'esptool']
    # Explicitly required even for discovery: never perform device I/O on page load.
    deviceAccessConfirmed: Literal[True]
    target: FlashTarget
    selector: str | None = Field(default=None, min_length=1, max_length=200)
    identificationJobId: str | None = Field(default=None, pattern=r'^j_[0-9a-f]{16}$')
    expectedIdentitySha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    planId: str | None = Field(default=None, pattern=r'^fp_[0-9a-f]{16}$')
    expectedPlanSha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    resetAfter: bool = False
    requestId: str = Field(min_length=1, max_length=160)

    @model_validator(mode='after')
    def operation(self):
        if (self.target in TARGETS) != (self.backend == 'pyocd'):
            raise ValueError('芯片与烧录后端不匹配。')
        if self.action != 'discover' and not self.selector:
            raise ValueError('必须选择明确的探针序号或串口。')
        if self.action in ('program', 'verify') and not all((self.identificationJobId, self.expectedIdentitySha256, self.planId, self.expectedPlanSha256)):
            raise ValueError('烧录/校验必须绑定识别结果和离线计划。')
        if self.resetAfter and self.action != 'program':
            raise ValueError('仅允许在烧录校验成功后选择复位。')
        return self
