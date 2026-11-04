const messages:Record<string,string>={
  '数据保持':'Data preservation','数据保持报告':'Data preservation report',
  '排版候选':'Layout candidates','选择候选':'Select candidate','选择排版候选':'Select layout candidate','待审阅':'Awaiting review','已检查':'Checked','审阅后已采纳':'Adopted after review','等待审阅':'Awaiting review','历史候选':'Historical candidate',
  '对比同一任务的原生前后图，检查通过后再采纳。':'Compare native before and after drawings from the same task, then adopt a checked candidate.',
  '尚无排版候选。Agent 提交候选后，可在这里对比并采纳。':'No layout candidates yet. Review and adopt them here after the Agent submits one.',
  '正在读取候选与同版检查…':'Loading candidate and matching checks…','关键变化':'Key changes','尚无可用的修改结果。':'No changes are available yet.',
  '按已提交的布局意图整理图页。':'Organize sheets using the submitted layout intent.','已声明的原理图修改，详见同版报告。':'Declared schematic changes; see the matching report.',
  '同版检查':'Matching checks','原生连接':'Native connectivity','检查尚未完成。':'Checks have not finished.','本任务未产生可用的原生检查结果。':'This task produced no available native check result.',
  '原生检查不能代替图面可读性与硬件验证。':'Native checks do not establish drawing readability or hardware performance.',
  '未满足项与待复核内容':'Unmet items and review notes','未报告阻止采纳的检查项；仍请复核原生图。':'No blocking check findings were reported; review the native drawing.',
  '详见完整检查报告。':'See the full check report.','原生前后图':'Native before and after drawings','适应图页':'Fit sheet','放大图纸':'Zoom in',
  '优化前':'Before','候选图':'Candidate','尚无候选版本':'No candidate version yet','此任务未导出修改前原生图。':'This task did not export a native before drawing.','尚无原生候选图。':'No native candidate drawing is available.',
  '优化前原生图':'Native before drawing','候选图原生图':'Native candidate drawing','优化前图页':'Before sheet','候选图图页':'Candidate sheet',
  '完整检查报告':'Full check report','连接核对报告':'Connectivity report','文件变化报告':'File changes report','原生规则报告':'Native rule report',
  '此候选已采纳':'Candidate adopted','采纳此排版':'Adopt layout','采纳将更新当前原理图版本，下游设计需重新核对。':'Adoption updates the current schematic; downstream designs need review.',
  '正在核对候选与当前工程…':'Checking candidate against the current project…','候选已采纳。':'Candidate adopted.',
  '原生检查通过，尚未采纳；请结合前后图复核可读性。':'Native checks passed; not yet adopted. Compare the drawings for readability.',
  '此候选已采纳；原生任务和检查记录保留。':'Candidate adopted. The original native task and check records are retained.',
  '候选正在生成与检查。':'Generating and checking the candidate.','没有可供采纳的候选。':'No candidate is available for adoption.',
  '工程已变化，请基于当前版本重新生成候选。':'The project has changed. Generate a new candidate from the current revision.',
  '此工程还有任务正在执行，请完成后重读候选。':'A project task is running. Read the candidate again when it finishes.',
  '候选未通过原生检查、完整连接目标或数据保持检查。':'The candidate failed native checks, the complete connectivity target, or data preservation.',
  '原生检查通过，候选等待审阅采纳。':'Native checks passed; candidate awaits review and adoption.',
  '候选 · 待审阅':'Candidate · Awaiting review','候选已就绪，等待审阅采纳':'Candidate ready for review and adoption','优化前图纸，放大后可滚动':'Before drawing; scroll after zooming','候选图图纸，放大后可滚动':'Candidate drawing; scroll after zooming','候选诊断图（未通过检查）':'Candidate diagnostics (checks failed)'
};
export function translateSchematicText(text:string):string|undefined{
  if(messages[text]!==undefined)return messages[text];let m:RegExpMatchArray|null;
  if((m=text.match(/^修订 ([a-f0-9]+)$/)))return `Revision ${m[1]}`;
  if((m=text.match(/^整理 (\d+) 张原有图页中的 (\d+) 个完整功能块；块内布局与连接保持。$/)))return `Organize ${m[2]} complete blocks in ${m[1]} existing sheets; preserve internal layout and connections.`;
  if((m=text.match(/^整理 (\d+) 条声明支路与相关文字。$/)))return `Organize ${m[1]} declared branches and their text.`;
  if((m=text.match(/^自动安排 (\d+) 个功能页，跨页代价 ([\d.]+) → ([\d.]+)（估计）。$/)))return `Planned ${m[1]} functional sheets; estimated cross-sheet cost ${m[2]} → ${m[3]}.`;
  if((m=text.match(/^按功能页组织：(.+)$/)))return `Functional sheets: ${m[1]}`;
  if((m=text.match(/^本次涉及 (\d+) 个原生文件。$/)))return `${m[1]} native files changed.`;
  if((m=text.match(/^有 (\d+) 项文字或未覆盖对象需复核，详见同版报告。$/)))return `${m[1]} text or uncovered-object items need review; see the matching report.`;
  if((m=text.match(/^(ERC|原生连接|数据保持) · (通过|未通过) · (\d+) 错误 · (\d+) 警告$/)))return `${m[1]==='ERC'?'ERC':m[1]==='原生连接'?'Native connectivity':'Data preservation'} · ${m[2]==='通过'?'Passed':'Failed'} · ${m[3]} errors · ${m[4]} warnings`;
  if((m=text.match(/^(.+) · (待审阅|历史候选|已采纳|检查中|已检查|需处理)$/)))return `${m[1]} · ${{待审阅:'Awaiting review',历史候选:'Historical candidate',已采纳:'Adopted',检查中:'Checking',已检查:'Checked',需处理:'Needs attention'}[m[2]]}`;
  return undefined;
}
