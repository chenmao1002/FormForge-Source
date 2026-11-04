"""Local, explicit project-default presets; usage comes from successful project records."""
import copy
import uuid
from pydantic import Field, field_validator
from .schemas import StrictModel
from .module_settings import MODELS
from .store import DomainError, now


class SaveRulePreset(StrictModel):
    expectedSettingsVersion: str
    name: str = Field(min_length=1,max_length=120)
    description: str = Field(default='',max_length=2000)
    modules: dict = Field(min_length=1,max_length=5)

    @field_validator('modules')
    @classmethod
    def valid_modules(cls,value):
        result={}
        for key,fields in value.items():
            if key not in MODELS or not isinstance(fields,dict) or not fields:
                raise ValueError('预设须包含已支持模块的明确字段。')
            result[key]=MODELS[key].model_validate(fields).model_dump(include=set(fields))
        return result


class ApplyRulePreset(StrictModel):
    presetId: str
    expectedProjectVersion: int = Field(ge=1)


class RulePresets:
    def __init__(self,service):
        self.service=service
        self.settings=service.settings

    def read(self):
        configuration=self.settings._read_configuration()
        rows=copy.deepcopy(configuration.get('rulePresets',[]))
        counts={}
        for project in self.service.store.list('projects'):
            for record in project.get('rulePresetApplications',[]):
                key=record.get('presetId');counts[key]=counts.get(key,0)+1
        for row in rows:row['usageCount']=counts.get(row['id'],0)
        rows.sort(key=lambda r:(-r['usageCount'],r['name'],r['id']))
        return dict(settingsVersion=configuration['version'],presets=rows,
            countSource='Successful explicit applications recorded in local project application history; not views or estimated popularity.',
            appliesTo='Future tasks; existing PCB rules require a separate checked PCB edit.')

    def save(self,request):
        request=SaveRulePreset.model_validate(request) if isinstance(request,dict) else request
        with self.settings._configuration_lock:
            configuration=self.settings._read_configuration()
            self.settings._ensure_version(configuration,request.expectedSettingsVersion)
            rows=configuration.setdefault('rulePresets',[])
            if any(r['name']==request.name for r in rows):
                raise DomainError('version_conflict','已有同名预设，请使用明确的新名称。',409)
            row=dict(id='rp_'+uuid.uuid4().hex[:16],name=request.name,description=request.description,
                modules=request.modules,createdAt=now())
            rows.append(row)
            self.settings._write_configuration(configuration)
        return row

    def apply(self,project_id,request):
        request=ApplyRulePreset.model_validate(request) if isinstance(request,dict) else request
        rows=self.settings._read_configuration().get('rulePresets',[])
        row=next((r for r in rows if r['id']==request.presetId),None)
        if row is None:raise DomainError('not_found','没有找到规则预设。',404)
        store=self.service.store
        with store.tx() as db:
            project=store._get(db,'projects',project_id)
            if project['version']!=request.expectedProjectVersion:
                raise DomainError('version_conflict','工程已被更新，请刷新后重新应用预设。',409)
            preferences=copy.deepcopy(project.get('preferences',{}))
            overrides=preferences.setdefault('moduleDefaults',{})
            for module,values in row['modules'].items():
                overrides[module]={**overrides.get(module,{}),**values}
            # Audit history stays outside engineering preferences: applying the
            # same values again must not invalidate a design solely due to a counter.
            project.setdefault('rulePresetApplications',[]).append(dict(presetId=row['id'],name=row['name'],
                appliedAt=now(),modules=copy.deepcopy(row['modules'])))
            project.update(preferences=preferences,version=project['version']+1,updatedAt=now())
            store._put(db,'projects',project)
            store._event(db,'project.updated',project_id,{'version':project['version'],'rulePresetId':row['id']})
            updated=project
        return dict(projectId=project_id,projectVersion=updated['version'],presetId=row['id'],
            moduleSettings=self.settings.project_modules(project_id),appliesTo='future-tasks-only')
