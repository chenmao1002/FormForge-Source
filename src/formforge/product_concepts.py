"""Versioned reference research and concept choice, separate from engineering CAD."""
import copy
import hashlib
from pydantic import Field, HttpUrl, model_validator
from PIL import Image
from .schemas import StrictModel
from .store import DomainError, now
from .agent_workflow import design_binding, requirements_binding

INSTRUCTIONS = '产品定方案先搜索同类产品图片，保存来源、借鉴点与不适用点，再由外部Agent生成2至3个概念方向并登记原提示词与图片SHA。读取productConcepts，等待用户选定；选定只表示造型方向，不证明CAD、隐藏结构或制造可行。进入CAD后对照选定图复核轮廓、操作、背面与藏线，变更需求需重审。服务不自动搜索或调用收费模型。'

class ConceptImage(StrictModel):
    sourceId: str
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

class ProductReference(StrictModel):
    id: str = Field(min_length=1,max_length=80)
    title: str = Field(min_length=1,max_length=200)
    url: HttpUrl
    image: ConceptImage
    observed: str = Field(min_length=1,max_length=3000)
    adopt: str = Field(min_length=1,max_length=3000)
    avoid: str = Field(min_length=1,max_length=3000)

class ProductConcept(StrictModel):
    id: str = Field(min_length=1,max_length=80)
    title: str = Field(min_length=1,max_length=200)
    image: ConceptImage
    prompt: str = Field(min_length=1,max_length=16000)
    referenceIds: list[str] = Field(min_length=1,max_length=20)
    benefits: str = Field(min_length=1,max_length=3000)
    risks: str = Field(min_length=1,max_length=3000)

class ConceptUpdate(StrictModel):
    expectedVersion: int = Field(ge=0)
    expectedDesignBinding: str
    brief: str = Field(min_length=1,max_length=6000)
    constraints: list[str] = Field(min_length=1,max_length=30)
    references: list[ProductReference] = Field(default_factory=list,max_length=20)
    concepts: list[ProductConcept] = Field(default_factory=list,max_length=3)

    @model_validator(mode='after')
    def validate_refs(self):
        for rows in [self.references,self.concepts]:
            if len({r.id for r in rows})!=len(rows): raise ValueError('Duplicate concept/reference ID')
        refs={r.id for r in self.references}
        if any(set(c.referenceIds)-refs for c in self.concepts):raise ValueError('Unknown reference ID')
        if self.concepts and len(self.concepts)<2:raise ValueError('Provide 2 or 3 concept directions')
        return self

class ConceptSelection(StrictModel):
    expectedVersion: int = Field(ge=0)
    expectedDesignBinding: str
    conceptId: str
    sourceText: str = Field(min_length=1,max_length=3000)

def concept_context(project):
    value=copy.deepcopy(project.get('productConcepts',{}))
    value.setdefault('version',0)
    value.setdefault('references',[]);value.setdefault('concepts',[])
    value['designBinding']=design_binding(project)
    value['stale']=bool(value.get('requirementsBinding') and value['requirementsBinding']!=requirements_binding(project))
    value['stage']='needs_review' if value['stale'] else 'selected' if value.get('selection') else 'awaiting_selection' if value['concepts'] else 'research' if value['version'] else 'not_started'
    value['cadReady']=value['stage']=='selected'
    value['engineeringVerified']=False;value['agentInstructions']=INSTRUCTIONS
    return value

class ProductConcepts:
    def __init__(self,store,sources):self.store,self.sources=store,sources

    def read(self,pid):
        p=self.store.get('projects',pid);p['headRevisionId']=self.store.head_revision(pid)
        return concept_context(p)

    def require_selection(self,pid):
        state=self.read(pid)
        if state['version'] and not state['cadReady']:
            raise DomainError('invalid_input','当前工程正在概念定案阶段；请先完成参考调研并由用户选定方案，再提交新的机械CAD。已有CAD仍可查看。')

    def update(self,pid,request):
        req=ConceptUpdate.model_validate(request)
        for image in [r.image for r in req.references]+[c.image for c in req.concepts]:
            row,path=self.sources.get_record(pid,image.sourceId)
            if row['sha256']!=image.sha256 or hashlib.sha256(path.read_bytes()).hexdigest()!=image.sha256:
                raise DomainError('invalid_input','概念图片SHA与登记资料不一致。')
            try:
                with Image.open(path) as im:
                    if im.format not in ['PNG','JPEG'] or im.width*im.height>16000000:raise ValueError('Unsupported image')
                    im.verify()
            except Exception as exc:raise DomainError('invalid_input','概念参考须为有效PNG/JPEG图片。') from exc
        return self._save(pid,req,False)

    def select(self,pid,request):return self._save(pid,ConceptSelection.model_validate(request),True)

    def _save(self,pid,req,selection):
        with self.store.tx() as db:
            p=self.store._get(db,'projects',pid);had='headRevisionId' in p;p['headRevisionId']=self.store._head(db,p)
            old=p.get('productConcepts',{});current=concept_context(p)
            if req.expectedVersion!=current['version'] or req.expectedDesignBinding!=current['designBinding']:
                raise DomainError('version_conflict','方案或设计已变化，请重读后合并。',409)
            if selection:
                if current['stale'] or req.conceptId not in {c['id'] for c in current['concepts']}:
                    raise DomainError('invalid_input','只能选定当前需求下的有效概念。')
                updated=copy.deepcopy(old);updated['selection']=dict(conceptId=req.conceptId,sourceText=req.sourceText,source='user',selectedAt=now())
            else:
                updated=req.model_dump(mode='json',exclude={'expectedVersion','expectedDesignBinding'})
                updated.update(selection=None,requirementsBinding=requirements_binding(p),baselineDesignBinding=design_binding(p),baselineHeads=p.get('moduleHeads',{}))
            updated.update(version=current['version']+1,updatedAt=now())
            p['productConcepts']=updated
            if not had:p.pop('headRevisionId',None)
            self.store._put(db,'projects',p)
            self.store._event(db,'project.concepts_updated',pid,dict(previous=old,current=updated))
            p['headRevisionId']=self.store._head(db,p)
            return concept_context(p)
