/* Icones e feedback locais; nenhuma requisicao ou regra de API. */
(() => {
  const icons = {
    btnBuscar:['search',true], btnTema:['sun',true], btnTemaHeatmap:['sun',true],
    btnAuditoria:['scan-search',false], btnAuditoriaMobile:['scan-search',false], btnHeatmap:['layout-grid',false], btnSalesMap:['chart-no-axes-combined',false],
    btnHistorico:['history',false], btnSyncErp:['refresh-cw',false],
    btnLogTiny:['list',false], btnResumoVendas:['chart-no-axes-combined',false],btnSalesDiagnostic:['download',false],
    btnAutorizarTiny:['key-round',false], btnBackupHeader:['download',false],
    btnEstrutura:['settings-2',false], btnZoomMenos:['minus',true],btnZoomMais:['plus',true],
    btnMostrarCadastro:['plus',false], btnSalvar:['check',false],
    btnAtualizarHeatmap:['refresh-cw',false],btnAtualizarUnidades:['boxes',false],btnBuscarAuditoria:['search',false],
    btnFecharAuditoria:['x',true],btnFecharImportacao:['x',true],
    scanBtn:['refresh-cw',true],themeBtn:['sun',true],settingsBtn:['settings-2',true],
    previousPage:['arrow-up',true],nextPage:['arrow-down',true],zoomOut:['minus',true],zoomIn:['plus',true],
    undoBtn:['undo-2',true],historyBtn:['history',false],resetBtn:['rotate-ccw',false]
  };
  function decorate(id,icon,iconOnly) {
    const el=document.getElementById(id);
    if(!el || !window.lucide)return;
    const label=el.getAttribute('aria-label') || el.textContent.trim() || el.title;
    el.setAttribute('aria-label',label);el.title ||= label;
    el.classList.add('v7-icon-label');
    el.innerHTML=`<i data-lucide="${icon}"></i>${iconOnly?'':`<span>${label}</span>`}`;
    window.lucide.createIcons({root:el});
  }
  function refreshThemeIcons(){
    for(const id of ['btnTema','btnTemaHeatmap','themeBtn']) decorate(id,document.documentElement.dataset.theme==='light'?'moon':'sun',true);
  }
  let motionPreference=null;
  try{motionPreference=localStorage.getItem('hl_motion');}catch{}
  const motionQuery=window.matchMedia('(prefers-reduced-motion: reduce)');
  const reduced=()=>motionPreference==='off'||(motionPreference!=='on'&&motionQuery.matches);
  function syncMotion(){
    document.documentElement.dataset.motion=motionPreference||'system';
    const toggle=document.getElementById('motionToggle');
    if(toggle)toggle.checked=!reduced();
  }
  syncMotion();
  motionQuery.addEventListener('change',syncMotion);
  document.getElementById('motionToggle')?.addEventListener('change',event=>{
    motionPreference=event.target.checked?'on':'off';
    try{localStorage.setItem('hl_motion',motionPreference);}catch{}
    syncMotion();
    if(!reduced())reveal(document.getElementById('painelZoomWrap'));
  });
  function reveal(container){
    if(!container||reduced())return;
    const rows=[...container.querySelectorAll('.prat-row,.drawer-sku-row,.heatmap-cell')].slice(0,12);
    const targets=rows.length?rows:[container];
    targets.forEach((row,index)=>row.animate?.([{opacity:.15,transform:'translateY(7px)'},{opacity:1,transform:'translateY(0)'}],{duration:260,delay:Math.min(index,6)*18,easing:'cubic-bezier(.16,1,.3,1)'}));
  }
  function number(el,value,format){
    const before=el._hlNumber??0;
    el._hlNumber=Number(value);
    cancelAnimationFrame(el._hlFrame);
    if(reduced()||before===Number(value)){el.textContent=format(value);return;}
    const start=performance.now();
    const tick=now=>{
      const t=Math.max(0,Math.min(1,(now-start)/220)),p=1-Math.pow(1-t,3);
      el.textContent=format(t===1?value:Math.round(before+(value-before)*p));
      if(t<1&&el.isConnected)el._hlFrame=requestAnimationFrame(tick);
    };
    el._hlFrame=requestAnimationFrame(tick);
  }
  function success(container){
    if(reduced()||!container)return;
    container.animate?.([{opacity:.65},{opacity:1}],{duration:380,easing:'ease-out'});
    const figure=container.querySelector('.drawer-unidades');
    figure?.animate?.([{transform:'scale(.94)'},{transform:'scale(1.045)',offset:.5},{transform:'scale(1)'}],{duration:320,easing:'ease-out'});
  }
  window.HL_UI={reveal,number,success};
  for(const [id,args] of Object.entries(icons))decorate(id,...args);
  document.getElementById('btnAuditoriaMobile')?.addEventListener('click',()=>document.getElementById('btnAuditoria').click());
  refreshThemeIcons();
  for(const menu of document.querySelectorAll('#headerMenu'))menu.addEventListener('toggle',()=>{
    if(!menu.open||reduced())return;
    [...menu.querySelectorAll('.header-menu-pop>*')].forEach((item,index)=>item.animate?.([{opacity:0,transform:'translateX(6px)'},{opacity:1,transform:'translateX(0)'}],{duration:230,delay:Math.min(index,5)*14,easing:'ease-out'}));
  });
  new MutationObserver(refreshThemeIcons).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  document.addEventListener('click',event=>{
    const button=event.target.closest('button');
    if(!button || button.disabled)return;
    if(!reduced())button.animate?.([{transform:'scale(1)'},{transform:'scale(.96)',offset:.35},{transform:'scale(1)'}],{duration:210,easing:'ease-out'});
    button.classList.add('v7-press');
    setTimeout(()=>button.classList.remove('v7-press'),120);
  },{passive:true});
})();
