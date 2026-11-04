"""Module defaults frozen into projects and resolved before task validation."""
from copy import deepcopy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SchematicDefaults(Record):
    paper: Literal['A4', 'A3', 'A2', 'A1', 'A0'] = 'A3'
    maxSymbolsPerSheet: int = Field(default=40,ge=1,le=500)
    layoutStrategy: Literal['readable','balanced','fast'] = 'readable'
    timeoutSeconds: int = Field(default=180, ge=15, le=900)


class PcbZoneDefaults(Record):
    clearance: float = Field(default=.2,ge=.1,le=5,allow_inf_nan=False)
    minimumThickness: float = Field(default=.2,ge=.1,le=2,allow_inf_nan=False)
    thermalGap: float = Field(default=.2,ge=.1,le=5,allow_inf_nan=False)
    thermalSpoke: float = Field(default=.2,ge=.1,le=5,allow_inf_nan=False)


class MechanicalDefaults(Record):
    interferencePolicy: Literal['model','check'] = 'model'
    screwColor: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')
    timeoutSeconds: int = Field(default=300, ge=30, le=10800)


class FirmwareDefaults(Record):
    template: Literal['blink','uart_echo','i2c_probe_uart'] = 'blink'
    periodMs: int = Field(default=1000, ge=2, le=60000)
    baud: int = Field(default=115200, ge=1200, le=1000000)
    timeoutSeconds: int = Field(default=180, ge=10, le=600)


class ManufacturingDefaults(Record):
    material: str | None = Field(default=None,min_length=1,max_length=100)
    thicknessMm: float | None = Field(default=None,gt=0,le=10,allow_inf_nan=False)
    finish: str | None = Field(default=None,min_length=1,max_length=100)
    solderMask: str | None = Field(default=None,min_length=1,max_length=100)
    boardQuantity: int | None = Field(default=None, ge=1, le=1000000)
    timeoutSeconds: int = Field(default=180, ge=30, le=600)


MODELS = dict(pcb=PcbZoneDefaults, schematic=SchematicDefaults, mechanical=MechanicalDefaults,
              firmware=FirmwareDefaults, manufacturing=ManufacturingDefaults)
TASK_MODULES = {'pcb.edit':'pcb', 'schematic.initialize': 'schematic', 'mechanical.design': 'mechanical',
                'firmware.design': 'firmware', 'manufacturing.prepare': 'manufacturing'}


class ModuleDefaults(Record):
    pcb: PcbZoneDefaults = Field(default_factory=PcbZoneDefaults)
    schematic: SchematicDefaults = Field(default_factory=SchematicDefaults)
    mechanical: MechanicalDefaults = Field(default_factory=MechanicalDefaults)
    firmware: FirmwareDefaults = Field(default_factory=FirmwareDefaults)
    manufacturing: ManufacturingDefaults = Field(default_factory=ManufacturingDefaults)


def effective(preferences, module):
    frozen = preferences.get('personalDefaultsSnapshot', {}).get('moduleDefaults', {}).get(module, {})
    override = preferences.get('moduleDefaults', {}).get(module, {})
    return MODELS[module].model_validate({**frozen, **override}).model_dump()


def request_params(request, kind):
    """Preserve explicit request values; schema defaults must not mask settings."""
    params = request.model_dump(exclude={'requestId'})
    for field in MODELS[TASK_MODULES[kind]].model_fields:
        if field not in request.model_fields_set:
            params.pop(field, None)
    if kind=='pcb.edit':
        for raw,parsed in zip(params.get('operations',[]),request.operations):
            if parsed.kind=='add_zone':
                for field in PcbZoneDefaults.model_fields:
                    if field not in parsed.model_fields_set:raw.pop(field,None)
    if kind=='manufacturing.prepare' and 'fabrication' not in request.model_fields_set:params.pop('fabrication',None)
    if kind=='manufacturing.prepare' and 'fabrication' in request.model_fields_set:
        params['fabrication']=request.fabrication.model_dump(exclude_unset=True)
    return params


def resolve_task(preferences, kind, params):
    if kind=='schematic.edit' and any(o['kind']=='auto_layout' for o in params.get('operations',[])):
        result=deepcopy(params);defaults=effective(preferences,'schematic')
        for op in result['operations']:
            if op['kind']=='auto_layout':
                for key in ['paper','maxSymbolsPerSheet','layoutStrategy']:
                    if op.get(key) is None:op[key]=defaults[key]
        return result
    if kind not in TASK_MODULES:
        return params
    module = TASK_MODULES[kind]
    defaults = effective(preferences, module)
    result = deepcopy(params)
    if 'timeoutSeconds' in defaults:result.setdefault('timeoutSeconds', defaults['timeoutSeconds'])
    if module=='pcb':
        for op in result.get('operations',[]):
            if op['kind']=='add_zone':
                for field,value in defaults.items():op.setdefault(field,value)
    elif module == 'schematic':
        result.setdefault('paper', defaults['paper'])
    elif module == 'mechanical':
        design = result.get('design', {})
        if design.get('kind')=='feature_model' and defaults['interferencePolicy']=='check':
            design.setdefault('check_interference',True)
        if design.get('kind') in {'feature_model', 'assembly'} and defaults['screwColor'] is not None:
            color = defaults['screwColor'].lstrip('#')
            design.setdefault('screw_color', [int(color[i:i+2], 16)/255 for i in (0, 2, 4)])
    elif module == 'firmware' and result.get('mode') == 'template':
        if result.get('template') is None:result['template']=defaults['template']
        field = 'periodMs' if result.get('template') == 'blink' else 'baud'
        if result.get(field) is None:
            result[field] = defaults[field]
        from .store import DomainError
        if result['template']=='blink' and result.get('baud') is not None:
            raise DomainError('invalid_input','闪灯模板不消费baud；请调整模板或参数。')
        if result['template']!='blink' and result.get('periodMs') is not None:
            raise DomainError('invalid_input','串口模板不消费periodMs；请调整模板或参数。')
    elif module == 'manufacturing':
        result.setdefault('boardQuantity', defaults['boardQuantity'])
        fabrication={key:defaults[key] for key in ['material','thicknessMm','finish','solderMask'] if defaults[key] is not None}
        result['fabrication']={**fabrication,**result.get('fabrication',{})}
    return result
