const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
function orderState(api) {
  if(!api.checkpoint.board)throw new Error('采购执行器未传入实际板参数；请先正常停启更新执行器，未创建订单资料。');
  const input={projectId:api.checkpoint.projectId,projectVersion:api.checkpoint.projectVersion,
    quantity:api.checkpoint.quantity,gerberSha256:api.gerber.sha256,files:api.files.map(f=>f.sha256),parts:api.parts};
  for(const key of ['assemblyQuantity','assemblyScope'])if(api.checkpoint[key]!==undefined)input[key]=api.checkpoint[key];
  const hash=value=>crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
  const legacyBinding=hash(input);
  const legacyFile=path.join(api.local,'orders',legacyBinding,'state.json');
  let binding,emptyLegacy;
  if(fs.existsSync(legacyFile)) {
    const legacy=JSON.parse(fs.readFileSync(legacyFile,'utf8'));
    const empty=legacy.phase==='preparing' && !legacy.input?.board &&
      Object.keys(legacy).every(k=>['binding','input','createdAt','fileName','phase'].includes(k));
    if(empty) {
      // A stale worker previously dropped board parameters before reaching the
      // order form. Preserve its empty preparation, then bind the actual board.
      emptyLegacy=legacyBinding;input.board=api.checkpoint.board;binding=hash(input);
    } else {
    // Existing pre-parameterization orders were exclusively 5 x 26x32mm.
    const b=api.checkpoint.board;
    if(api.checkpoint.quantity!==5 || !b || b.widthMm!==26 || b.heightMm!==32 || b.layers!==2 || b.thicknessMm!==1.6 || b.side!=='top')
      throw new Error('已有旧版订单绑定，尺寸/工艺不符；须先查旧单，不能创建替代订单。');
    binding=legacyBinding;
    }
  } else {
    input.board=api.checkpoint.board;
    binding=hash(input);
  }
  const directory=path.join(api.local,'orders',binding);fs.mkdirSync(directory,{recursive:true});
  const file=path.join(directory,'state.json');
  const state=fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):
    {binding,input,createdAt:new Date().toISOString(),fileName:path.basename(api.gerber.path).replace(/\.zip$/i,''),phase:'preparing',...(emptyLegacy?{supersedesEmptyPreparation:emptyLegacy}:{})};
  const save=()=>{fs.writeFileSync(file,JSON.stringify(state,null,2));api.checkpoint.orderBinding=binding;api.save();};
  const begin=kind=>{
    if(state[kind]?.status==='submission-started')throw new Error('上次提交结果未知，必须先查单；不重复提交 '+kind+'。');
    if(state[kind]?.displayNumber || state[kind]?.status==='submitted' || state[kind]?.createResponse)throw new Error('已存在 '+kind+' 订单，必须读取既有状态；不重复提交。');
    const previous=state[kind];
    const history=previous?[...(previous.history||[]),{...previous,history:undefined}]:[];
    state[kind]={status:'submission-started',startedAt:new Date().toISOString(),history};save();
  };
  save();return {state,save,begin,directory};
}
module.exports={orderState};
