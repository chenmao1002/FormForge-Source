const {expect}=require('playwright/test');
function validateParameters(body) {
  if(!Number.isInteger(body.quantity)||body.quantity<1||body.quantity>10000)throw new Error('数量必须为1至10000的整数。');
  const pcbOnly=body.assemblyScope==='pcb-only';
  if(pcbOnly&&(body.action!=='prepare-order'||body.assemblyQuantity!==undefined||body.files?.length||body.parts?.length))throw new Error('裸板仅支持准备，不能携带SMT资料或提交。');
  const assembled=assemblyCount(body);
  if(!pcbOnly&&(!Number.isInteger(assembled)||assembled<1||assembled>body.quantity))throw new Error('SMT数量必须为1至PCB数量。');
  if(body.assemblyScope && !['all','smd-only','pcb-only'].includes(body.assemblyScope))throw new Error('装配范围无效。');
  if(body.assemblyScope==='smd-only' && body.parts?.some(p=>p.mounting!=='smd'))throw new Error('仅SMT装配不能包含通孔物料。');
  const board=body.board;
  if(body.action!=='quote'&&!board)throw new Error('正式PCB流程必须提供实际板尺寸和工艺。');
  if(board && (![board.widthMm,board.heightMm].every(n=>Number.isFinite(n)&&n>0&&n<=500)
    ||board.layers!==2||board.thicknessMm!==1.6||board.side!=='top'))throw new Error('仅支持明确尺寸的双层1.6mm顶面装配。');
}
function dimensionsMatch(actual,board) {
  return actual.length===2 && actual.every((n,i)=>Number.isFinite(n)&&Math.abs(n-[board.widthMm,board.heightMm][i])<0.01);
}
function verifyReview(text,quantity,board) {
  const count=Number(text.match(/板子数量\s+(\d+)\b/)?.[1]);
  const layers=Number(text.match(/板子层数\s+(\d+)\b/)?.[1]);
  const size=text.match(/([\d.]+)\s*CM\s*[x×]\s*([\d.]+)\s*CM/i);
  if(count!==quantity||layers!==board.layers||!size||!dimensionsMatch(size.slice(1).map(n=>Number(n)*10),board))
    throw new Error('原生复核数量、层数或板框与绑定请求不符，未提交。');
}
async function selectAllAssembly(page,quantity) {
  const all=page.getByLabel(`${quantity}片全贴`,{exact:true});
  if(!await all.isChecked())await page.getByText(`${quantity}片全贴`,{exact:true}).click();
  await expect(all).toBeChecked();
}
function verifyBarePcbReview(text,quantity,board,fileName){
  if(/去填写|[1-9]\d*\s*项未填写|[1-9]\d*\s*项待检查/.test(text))throw new Error('原生订单检查仍有未完成参数，未提交。');
  verifyReview(text,quantity,board);
  const actualName=text.match(/文件名[：:]\s*([^\r\n]+)/)?.[1].trim();
  if(!fileName||actualName!==fileName)throw new Error('原生复核文件名与绑定Gerber不符，未提交。');
  if(!/板材类别[\s：:]+FR-4/.test(text)||!/成品板厚[\s：:]+1\.6\s*MM/i.test(text)||!/焊盘喷镀[\s：:]+无铅喷锡/.test(text))throw new Error('原生复核材料、板厚或表面工艺不符，未提交。');
  if(!/确认订单方式[\s：:]+手动确认订单/.test(text))throw new Error('原生复核未确认手动确认订单，未提交。');
  if(!/是否需要SMT[\s：:]+不需要/.test(text)||!/是否需要钢网[\s：:]+不需要/.test(text))throw new Error('原生复核未确认无SMT及无钢网，未提交。');
  return {nativeReviewComplete:true,requiresSmt:false,separateStencil:false,orderSubmitted:false};
}
function assemblyCount(body){return body.assemblyScope==='pcb-only'?0:body.assemblyQuantity??body.quantity;}
async function verifyAssembly(page,pcbQuantity,smtQuantity=pcbQuantity){
  if(smtQuantity===pcbQuantity){await expect(page.getByLabel(`${pcbQuantity}片全贴`,{exact:true})).toBeChecked();return;}
  await expect(page.getByLabel('其他数量',{exact:true})).toBeChecked();
  await expect(page.locator('#numInput')).toHaveValue(String(smtQuantity));
}
async function selectAssembly(page,pcbQuantity,smtQuantity=pcbQuantity){
  if(smtQuantity===pcbQuantity){await selectAllAssembly(page,pcbQuantity);return;}
  if(!await page.getByLabel('其他数量',{exact:true}).isChecked())await page.getByText('其他数量',{exact:true}).click();
  const input=page.locator('#numInput');await expect(input).toBeEnabled();
  await input.fill(String(smtQuantity));await input.press('Tab');
  await verifyAssembly(page,pcbQuantity,smtQuantity);
}
module.exports={validateParameters,dimensionsMatch,verifyReview,verifyBarePcbReview,selectAllAssembly,assemblyCount,selectAssembly,verifyAssembly};
