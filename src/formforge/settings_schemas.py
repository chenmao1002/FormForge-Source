"""Writable workspace-setting contracts with deliberately narrow scopes."""
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .schemas import StrictModel, BoardRules
from .module_settings import ModuleDefaults


class PcbPersonalDefaults(StrictModel):
    numericRules: BoardRules = Field(default_factory=BoardRules)
    boardCorner: Literal['rounded', 'square'] = 'rounded'
    cornerRadiusMm: float = Field(default=3.0, ge=0, le=25, allow_inf_nan=False)
    squareCornerReason: str | None = Field(default=None, max_length=300)
    routingStyle: Literal['balanced', 'tidy', 'shortest'] = 'balanced'
    placementDensity: Literal['balanced', 'compact', 'serviceable'] = 'balanced'
    silkscreenStyle: Literal['functional', 'minimal', 'detailed'] = 'functional'
    viaPreference: Literal['project-rules', 'conservative'] = 'project-rules'

    @model_validator(mode='after')
    def corner_policy(self):
        if self.boardCorner == 'rounded' and self.cornerRadiusMm <= 0:
            raise ValueError('圆角默认值必须大于0。')
        if self.boardCorner == 'square' and not (self.squareCornerReason or '').strip():
            raise ValueError('直角偏好必须记录明确原因。')
        if self.boardCorner == 'square' and self.cornerRadiusMm != 0:
            raise ValueError('直角偏好必须显式保存 cornerRadiusMm=0。')
        if self.boardCorner == 'rounded' and self.squareCornerReason is not None:
            raise ValueError('圆角偏好不能同时保存直角原因。')
        return self


class ManufacturingPersonalDefaults(StrictModel):
    provider: Literal['jlc'] = 'jlc'
    shippingSource: Literal['provider-default', 'confirm-at-checkout'] = 'confirm-at-checkout'
    shippingProfileLabel: str = Field(default='结算时选择并核对', min_length=1, max_length=80)
    invoiceSource: Literal['provider-default', 'confirm-at-checkout'] = 'confirm-at-checkout'
    invoiceType: Literal['confirm-at-checkout', 'none', 'personal', 'enterprise-general', 'enterprise-special'] = 'confirm-at-checkout'
    invoiceProfileLabel: str = Field(default='结算时选择并核对', min_length=1, max_length=80)

    @field_validator('shippingProfileLabel', 'invoiceProfileLabel')
    @classmethod
    def aliases_only(cls, value):
        value = value.strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('资料别名不能包含控制字符。')
        import re
        identity_tokens = re.findall(r'(?<![0-9A-Z])[0-9A-Z]{15,20}(?![0-9A-Z])', value.upper())
        if (re.search(r'(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)\d{12,}(?!\d)', value)
                or any(sum(character.isdigit() for character in token) >= 8 for token in identity_tokens)):
            raise ValueError('这里只保存脱敏别名，不能填写电话、邮箱、税号或账号全文。')
        if re.search(r'收件人|联系人|联系电话|手机号|纳税人识别号|税号|银行账号|开户地址', value) or (
                re.search(r'[省市区县]', value) and re.search(r'[路街道号室栋幢单元]', value)):
            raise ValueError('这里只保存脱敏别名，不能填写完整地址或身份资料。')
        return value


class WorkspaceDefaultsUpdate(StrictModel):
    expectedVersion: str = Field(min_length=1, max_length=80)
    pcbDefaults: PcbPersonalDefaults
    manufacturingDefaults: ManufacturingPersonalDefaults
    moduleDefaults: ModuleDefaults | None = None


class ToolchainProfileRequest(StrictModel):
    expectedVersion: str = Field(min_length=1, max_length=80)
    kind: Literal['kicad', 'arm-gcc']
    name: str = Field(min_length=1, max_length=80)
    executable: str = Field(min_length=1, max_length=1000)
    activate: bool = False

    @field_validator('name', 'executable')
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('名称或路径不能包含控制字符。')
        return value


class ToolchainActivationRequest(StrictModel):
    expectedVersion: str = Field(min_length=1, max_length=80)
    kind: Literal['kicad', 'arm-gcc']
    profileId: str | None = Field(default=None, pattern=r'^tp_[a-f0-9]{32}$')
