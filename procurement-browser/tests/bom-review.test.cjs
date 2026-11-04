const {test}=require('node:test'),assert=require('node:assert/strict');
const {originalRow,selectedRow,packageEquivalent,verifySettlementQuantity}=require('../bom-review.cjs');
const part={supplierCode:'C14663',mpn:'CC0603KRX7R9BB104',designators:['C3','C5'],perBoard:2};
const cells=['','C3、C5\n2个器件','','100nF ±10% 50V\n100n','0603\nCapacitor_SMD:C_0603_1608Metric','C14663\nC14663\nC编号匹配','基础库','使用：20\n库存：44,140,078\n单价：￥0.0511'];
test('a saved input of two cannot authorize a supplier settlement of five',()=>{
 assert.equal(verifySettlementQuantity({code:200,data:{allPatchNum:2}},2),2);
 for(const body of [{code:200,data:{allPatchNum:5}},{code:200,data:{}},{code:500,data:{allPatchNum:2}}])assert.throws(()=>verifySettlementQuantity(body,2));
});
test('a passive row uses the original and selected catalog code despite value-only display',()=>{assert.equal(selectedRow(cells,part,2).stock,44140078);});
test('wrong refs, selected code, missing stock and shortage are rejected',()=>{
 for(const [index,value] of [[1,'C3、C4'],[5,'C14663\nC1779\nC编号匹配'],[7,'库存：3'],[7,'库存未知']]){const bad=[...cells];bad[index]=value;assert.throws(()=>selectedRow(bad,part,2));}
 assert.throws(()=>originalRow(cells,{...part,supplierCode:'C146630'}));
});
test('native KiCad footprint names map only to known catalog packages',()=>{
 assert(packageEquivalent('Capacitor_SMD:C_0603_1608Metric','0603',part));
 assert(!packageEquivalent('Capacitor_SMD:C_0603_1608Metric','0805',part));
 assert(packageEquivalent('Diode_SMD:D_SMA','SMA(DO-214AC)',part));
 assert(!packageEquivalent('Package_TO_SOT_SMD:SOT-23','SOT-23-5',part));
 assert(!packageEquivalent('Unknown:generic','SMD',part));
 assert(packageEquivalent('Button_Switch_SMD:SW_SPDT_PCM12','SMD',{supplierCode:'C221841',mpn:'PCM12SMTR'}));
 assert(!packageEquivalent('Button_Switch_SMD:SW_SPDT_PCM12','SMD',part));
});
