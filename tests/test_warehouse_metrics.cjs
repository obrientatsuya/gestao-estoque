const {test}=require('node:test');
const assert=require('node:assert/strict');
const M=require('../ui/warehouse-metrics.js');
const stamp='2026-10-22T15:00:00Z';
const row=(sku,n)=>({sku,unidades_estoque:n,unidades_atualizadas_em:stamp,setor:'1S',prateleira:'P1',caixa:'C1'});
test('one SKU with many units occupies more than many low-stock SKUs',()=>{
  assert.ok(M.inventory([row('A',1000000)]).units>M.inventory(Array.from({length:20},(_,i)=>row(String(i),1))).units);
});
test('missing stock is not zero',()=>{
  const r=M.inventory([row('A',0),row('B',null)]);
  assert.equal(r.complete,false);assert.equal(r.known,1);assert.equal(r.units,0);
  assert.equal(M.level(null,100),'desconhecida');
});
test('stock without a consultation timestamp stays unknown',()=>assert.equal(M.inventory([{sku:'A',unidades_estoque:0}]).known,0));
test('zero and empty boxes are valid totals',()=>{
  assert.equal(M.inventory([row('A',0)]).complete,true);
  assert.deepEqual(M.inventory([]),{units:0,known:0,total:0,complete:true});
  assert.equal(M.reference(new Map()),0);
});
test('deduplicates SKUs and sums decimal unit values',()=>{
  assert.equal(M.inventory([row('A',3),row('a',3),row('B','2.5')]).units,5.5);
});
test('negative, nonfinite and invalid unit values cannot inflate occupancy',()=>{
  assert.equal(M.quantity(-50),0);for(const v of [null,'',true,'abc',Infinity])assert.equal(M.quantity(v),null);
});
test('sales scale is linear from red to green',()=>{
  for(const max of [10,1000,1000000])for(let i=0;i<=10;i++)assert.equal(M.color(i*max/10,max,true).hue,12*i);
  assert.equal(M.color(0,100,true).hue,0);assert.equal(M.color(100,100,true).hue,120);
});
test('occupancy scale is linear from green to red and clamps extremes',()=>{
  assert.equal(M.color(0,100).hue,120);assert.equal(M.color(50,100).hue,60);assert.equal(M.color(1000,100).hue,0);
  assert.equal(M.color(0,0,true).hue,0);
});
test('global scale excludes incomplete boxes and preserves membership',()=>{
  const groups=M.boxes([row('A',100),{...row('B',null),caixa:'C2'}]);
  assert.equal(M.reference(groups),100);assert.equal(groups.get('1S|P1|C2').complete,false);
});
