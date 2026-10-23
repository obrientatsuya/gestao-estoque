(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WarehouseMetrics=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  function quantity(value){
    if(value==null||value===''||typeof value==='boolean')return null;
    const n=Number(value);
    return Number.isFinite(n)?Math.max(0,n):null;
  }
  function inventory(rows){
    const unique=new Map(rows.map(row=>[String(row.sku).trim().toUpperCase(),row]));
    let units=0,known=0;
    for(const row of unique.values()){
      const n=quantity(row.unidades_estoque);
      if(n!==null&&row.unidades_atualizadas_em){units+=n;known++;}
    }
    return {units,known,total:unique.size,complete:known===unique.size};
  }
  function key(row){return [row.setor,row.prateleira,row.caixa].join('|');}
  function boxes(rows){
    const grouped=new Map();
    for(const row of rows){const k=key(row);if(!grouped.has(k))grouped.set(k,[]);grouped.get(k).push(row);}
    return new Map([...grouped].map(([k,items])=>[k,inventory(items)]));
  }
  function reference(groups){return Math.max(0,...[...groups.values()].filter(g=>g.complete).map(g=>g.units));}
  function ratio(value,max){return Math.min(1,Math.max(0,Number(value)/Math.max(1,Number(max)||1)));}
  function level(value,max){
    if(value==null)return 'desconhecida';
    if(value<=0)return 'vazio';
    const p=ratio(value,max);
    return p<=1/3?'pouca':p<=2/3?'media':'quase';
  }
  function color(value,max,sales=false,light=false){
    const p=ratio(value,max),hue=120*(sales?p:1-p);
    return {hue,ratio:p,border:`hsl(${hue.toFixed(3)} 68% ${light?38:58}%)`,background:`hsl(${hue.toFixed(3)} 68% 45% / ${light?'.14':'.17'})`};
  }
  return {quantity,inventory,key,boxes,reference,ratio,level,color};
});
