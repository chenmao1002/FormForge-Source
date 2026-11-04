// The supplier displays passive values instead of manufacturer part numbers.
// Bind rows to the frozen source code and references, then check the selected code.
function originalRow(cells,part) {
  const refs=(cells[1]||'').match(/\b[A-Z]+\d+\b/g)||[];
  if(JSON.stringify(refs.sort())!==JSON.stringify([...part.designators].sort()) || part.perBoard!==refs.length)
    throw new Error('分组位号或用量不符：'+part.supplierCode);
  const codes=(cells[5]||'').split(/\s+/).filter(s=>/^C\d+$/.test(s));
  if(codes[0]!==part.supplierCode)throw new Error('原BOM料号不符：'+part.supplierCode);
  return {refs,codes};
}
function selectedRow(cells,part,quantity) {
  const result=originalRow(cells,part);
  if(result.codes.length!==2 || result.codes.some(c=>c!==part.supplierCode))
    throw new Error('选定料号与原BOM不符：'+part.supplierCode);
  const stock=Number((cells[7]||'').match(/库存\s*[:：]\s*([\d,]+)/)?.[1]?.replace(/,/g,''));
  if(!Number.isFinite(stock) || stock<part.perBoard*quantity)throw new Error('选定料号库存不足或不可核对：'+part.supplierCode);
  return {...result,stock};
}
function packageEquivalent(source,selected,part) {
  if(!source || !selected)return false;
  if(source===selected)return true;
  const normalized=selected.replace(/[^a-z0-9]/gi,'').toUpperCase();
  const map=[[/:(?:[CR]|LED)_0603_1608Metric$/,'0603'],[/:(?:[CR]|LED)_0805_2012Metric$/,'0805'],[/:D_SMA$/,'SMADO214AC'],[/:SOT-23$/,'SOT23'],[/:TSSOP-20_4\.4x6\.5mm_P0\.65mm$/,'TSSOP20']];
  if(map.some(([pattern,value])=>pattern.test(source)&&normalized===value))return true;
  // This catalog item and its dedicated PCM12 footprint were checked together.
  return source==='Button_Switch_SMD:SW_SPDT_PCM12' && selected==='SMD' && part.supplierCode==='C221841' && part.mpn==='PCM12SMTR';
}
function bomRow(page,part) {
  return page.locator('tr:visible').filter({has:page.getByText(part.supplierCode,{exact:true})});
}
function verifySettlementQuantity(body,expected){
  const actual=Number(body.data?.allPatchNum);
  if(body.code!==200 || !Number.isInteger(actual) || actual!==expected)throw new Error('供应商结算贴片数量不符，未正式提交SMT。');
  return actual;
}
module.exports={originalRow,selectedRow,packageEquivalent,bomRow,verifySettlementQuantity};
