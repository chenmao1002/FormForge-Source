"""A small native document bootstrap; circuit choices stay with the calling Agent."""
import json
import uuid
from datetime import datetime

from ..store import now, uid
from .connectivity import fingerprint
from .importer import digest
from .schematic import safe_string, DEFAULT_WIRE_WIDTH_MILS
from .schematic_policy import defaults as presentation_defaults


def initialize_schematic(config, job, check_cancel):
    params = job['params']
    revision_id = uid('r')
    source = config.data / 'projects' / job['projectId'] / 'revisions' / revision_id / 'source'
    source.mkdir(parents=True)
    root_id = str(uuid.uuid4())
    name = params['name']
    text = f'''(kicad_sch (version 20250114) (generator "formforge")
      (uuid "{root_id}") (paper "{params.get('paper', 'A3')}")
      (title_block (title {safe_string(params['title'])}) (date "{datetime.now().astimezone().date().isoformat()}") (rev "1"))
      (lib_symbols)
      (sheet_instances (path "/" (page "1")))
      (embedded_fonts no))'''
    (source / (name + '.kicad_sch')).write_text(text, encoding='utf8')
    project = dict(net_settings=dict(classes=[dict(name='Default', wire_width=DEFAULT_WIRE_WIDTH_MILS)]))
    (source / (name + '.kicad_pro')).write_text(json.dumps(project, indent=2), encoding='utf8')
    specification = dict(schemaVersion=1, title=params['title'], requirements=params['requirements'],
                         schematicPresentation=presentation_defaults(),
                         decisions=params['decisions'], createdAt=now(), createdByJobId=job['id'],
                         source='explicit-agent-request; no-existing-schematic-imported')
    spec_path = source / 'formforge-specification.json'
    spec_path.write_text(json.dumps(specification, ensure_ascii=False, indent=2), encoding='utf8')
    check_cancel()
    manifest = [dict(path=p.name,sha256=digest(p),size=p.stat().st_size) for p in sorted(source.iterdir())]
    return dict(id=revision_id, projectId=job['projectId'], backend='kicad10', origin='initialized',
        parentRevisionId=None, createdByJobId=job['id'], sourceDirectory=None,
        relativePath=source.relative_to(config.data).as_posix(), entrypoints=dict(schematic=name+'.kicad_sch',board=None),
        manifest=manifest,totalBytes=sum(p['size'] for p in manifest),createdAt=now(),message=params['message'],
        changes=[dict(kind='initialize_schematic',before=None,after=dict(entrypoint=name+'.kicad_sch',requirementsHash=fingerprint(specification)))],
        designSpecification=specification, symbolLibraries=[], validation={'status':'pending'},connectivityIntent=None,
        downstreamRequiresReview=['pcb','mechanical','firmware','manufacturing'])
