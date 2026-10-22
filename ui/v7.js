/* Icones e feedback locais; nenhuma requisicao ou regra de API. */
(() => {
  const icons = {
    btnBuscar:['search',true], btnTema:['sun',true], btnTemaHeatmap:['sun',true],
    btnAuditoria:['scan-search',false], btnAuditoriaMobile:['scan-search',false], btnHeatmap:['layout-grid',false],
    btnHistorico:['history',false], btnSyncErp:['refresh-cw',false],
    btnLogTiny:['list',false], btnResumoVendas:['chart-no-axes-combined',false],
    btnAutorizarTiny:['key-round',false], btnBackupHeader:['download',false],
    btnEstrutura:['settings-2',false], btnZoomMenos:['minus',true],btnZoomMais:['plus',true],
    btnMostrarCadastro:['plus',false], btnSalvar:['check',false],
    btnAtualizarHeatmap:['refresh-cw',false],btnBuscarAuditoria:['search',false],
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
  for(const [id,args] of Object.entries(icons))decorate(id,...args);
  document.getElementById('btnAuditoriaMobile')?.addEventListener('click',()=>document.getElementById('btnAuditoria').click());
  refreshThemeIcons();
  new MutationObserver(refreshThemeIcons).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  document.addEventListener('click',event=>{
    const button=event.target.closest('button');
    if(!button || button.disabled)return;
    button.classList.add('v7-press');
    setTimeout(()=>button.classList.remove('v7-press'),120);
  },{passive:true});
})();
