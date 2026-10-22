const state = {
  notes: [], note: null, currentItem: null, pinnedItem: null,
  settings: null, toastTimer: null, countTimer: null,
  zoom: 1, currentPage: 1, itemBusy: new Set(), lastChecked: 0,
  matches: [], suggestionIndex: 0, selectionVersion: 0,
  pendingCounts: new Map(), countTimers: new Map(), countTasks: new Map(), countQueue: Promise.resolve()
};
const $ = id => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Erro ${response.status}`);
  return data;
}

function escapeHtml(value) {
  const el = document.createElement("span");
  el.textContent = String(value ?? "");
  return el.innerHTML;
}

function fmt(value) {
  return Number(value || 0).toLocaleString("pt-BR", { maximumFractionDigits: 2 });
}

function toast(message, type = "") {
  const el = $("toast");
  el.textContent = message;
  el.className = `toast ${type}`;
  el.hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { el.hidden = true; }, 3300);
}

function currentOperator() {
  return $("operator").value || "Anderson";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("hl-theme", theme);
  const lightNext = theme === "dark";
  $("themeBtn").innerHTML = lightNext ? "&#9728;" : "&#9790;";
  $("themeBtn").title = lightNext ? "Usar tema claro" : "Usar tema escuro";
}

async function loadSettings() {
  state.settings = await api("/api/settings");
  $("watchDir").value = state.settings.watch_dir;
  $("printer").innerHTML = state.settings.printers.map(name => `<option ${name === state.settings.printer ? "selected" : ""}>${escapeHtml(name)}</option>`).join("");
  const remembered = localStorage.getItem("hl-operator");
  const names = state.settings.operators.map(item => item.name);
  $("operator").innerHTML = names.map(name => `<option ${name === remembered ? "selected" : ""}>${escapeHtml(name)}</option>`).join("") + '<option value="__new__">+ Outro operador</option>';
  if (!$("operator").value || $("operator").value === "__new__") $("operator").value = names[0] || "Anderson";
}

async function loadNotes(selectFirst = true) {
  const selected = state.note?.id;
  state.notes = (await api("/api/notes")).notes;
  $("notesCount").textContent = `${state.notes.length} arquivo${state.notes.length === 1 ? "" : "s"}`;
  $("notesList").innerHTML = state.notes.map(note => `
    <button class="note-row ${note.id === selected ? "active" : ""}" data-note="${note.id}">
      <div class="note-row-top"><strong>NF ${escapeHtml(note.number || "sem numero")}</strong><span>${fmt(note.progress)}%</span></div>
      <p title="${escapeHtml(note.filename)}">${escapeHtml(note.filename)}</p>
      <div class="mini-track"><span style="width:${note.progress}%"></span></div>
    </button>`).join("");
  document.querySelectorAll("[data-note]").forEach(button => button.onclick = () => selectNote(button.dataset.note));
  if (!state.note && selectFirst && state.notes.length) await selectNote(state.notes[0].id);
}

async function selectNote(id) {
  const version = ++state.selectionVersion;
  await flushCounts();
  const note = await api(`/api/notes/${id}`);
  if (version !== state.selectionVersion) return;
  state.note = note;
  state.currentItem = null;
  state.pinnedItem = null;
  state.currentPage = 1;
  state.zoom = 1;
  state.matches = [];
  state.suggestionIndex = 0;
  $("skuSearch").value = "";
  $("suggestions").hidden = true;
  state.lastChecked = state.note.items.filter(item => item.checked).length;
  renderNote(true);
  await loadNotes(false);
  $("skuSearch").focus();
}

function renderNote(rebuildPages = false) {
  if (!state.note) return;
  $("downloadFinal").href = `/api/notes/${encodeURIComponent(state.note.id)}/final.pdf?download=1`;
  $("printFinal").href = `/api/notes/${encodeURIComponent(state.note.id)}/final.pdf`;
  $("downloadFinal").removeAttribute("aria-disabled");
  $("printFinal").removeAttribute("aria-disabled");
  $("emptyState").hidden = true;
  $("pagesContainer").hidden = false;
  $("noteTitle").textContent = `NF ${state.note.number || "sem numero"} - ${state.note.filename}`;
  $("lastAction").textContent = state.note.last_action
    ? `Ultimo: ${state.note.last_action.operator || "sem operador"} - ${new Date(state.note.last_action.created_at).toLocaleString("pt-BR")}`
    : "Nenhuma acao registrada";
  if (rebuildPages || !$("pagesContainer").children.length) renderPages();
  else updateHighlights();
  renderItems();
  renderProgress();
  updateViewerTools();
}

function renderPages() {
  $("pagesContainer").innerHTML = Array.from({ length: state.note.pages }, (_, index) => {
    const page = index + 1;
    return `<section class="page-stage" data-page-stage="${page}">
      <img src="/api/notes/${state.note.id}/pages/${page}" alt="Pagina ${page} da nota fiscal" draggable="false" loading="${page === 1 ? 'eager' : 'lazy'}" decoding="async">
      <div class="highlights" data-highlights="${page}"></div><span class="page-number">${page}</span>
    </section>`;
  }).join("");
  requestAnimationFrame(fitWidth);
  updateHighlights();
  observePages();
}

function updateHighlights() {
  document.querySelectorAll("[data-highlights]").forEach(layer => {
    const page = Number(layer.dataset.highlights);
    layer.innerHTML = state.note.items.filter(item => item.page === page).map(item => {
      const b = item.bbox;
      const mismatch = item.checked && Number(item.counted) !== Number(item.expected);
      return `<div class="highlight ${item.checked && !mismatch ? "checked" : ""} ${mismatch ? "mismatch" : ""} ${item.id === state.currentItem ? "current" : ""}" data-highlight="${item.id}" title="${escapeHtml(item.sku)}" style="left:${b.x0 / b.page_width * 100}%;top:${b.top / b.page_height * 100}%;width:${(b.x1 - b.x0) / b.page_width * 100}%;height:${(b.bottom - b.top) / b.page_height * 100}%"></div>`;
    }).join("");
  });
  document.querySelectorAll("[data-highlight]").forEach(bindHighlight);
}

function sortedItems() {
  const items = [...state.note.items];
  if (!state.pinnedItem) return items;
  const pinned = items.find(item => item.id === state.pinnedItem);
  return pinned ? [pinned, ...items.filter(item => item.id !== state.pinnedItem)] : items;
}

function itemStatus(item) {
  if (!item.checked) return { label: "PENDENTE", className: "" };
  if (Number(item.counted) !== Number(item.expected)) return { label: "DIVERGENTE", className: "diff" };
  return { label: "CONFERIDO", className: "done" };
}

function renderItems() {
  $("itemsList").innerHTML = sortedItems().map(item => {
    const status = itemStatus(item);
    const mismatch = item.checked && Number(item.counted) !== Number(item.expected);
    const busy = state.itemBusy.has(item.id);
    return `<article class="item-row ${item.checked && !mismatch ? "checked" : ""} ${mismatch ? "mismatch" : ""} ${item.id === state.currentItem ? "current" : ""} ${busy ? "printing" : ""}" data-item="${item.id}">
      <div class="item-top"><strong class="item-sku">${escapeHtml(item.sku)}</strong><span class="status-badge ${status.className}">${busy ? "IMPRIMINDO" : status.label}</span></div>
      <p class="item-description">${escapeHtml(item.description)}</p>
      <div class="quantity" title="Role sobre o numero para ajustar">
        <button data-step="-1" title="Diminuir quantidade">&#8722;</button>
        <input data-count type="number" min="0" step="1" value="${item.counted}" aria-label="Quantidade de ${escapeHtml(item.sku)}">
        <button data-step="1" title="Aumentar quantidade">+</button>
        <span class="expected">esperado ${fmt(item.expected)}</span>
      </div>
      <div class="item-meta"><span>Pagina ${item.page}</span><span class="print-state">${item.printed_count ? `${fmt(item.printed_count)} impressas` : ""}</span></div>
    </article>`;
  }).join("");
  document.querySelectorAll("[data-item]").forEach(bindItem);
}

function bindHighlight(mark) {
  const id = Number(mark.dataset.highlight);
  mark.onclick = async event => {
    event.stopPropagation();
    selectItem(id, false);
    await toggleItem(id);
  };
  mark.oncontextmenu = event => {
    event.preventDefault();
    event.stopPropagation();
    selectItem(id, false);
    printItem(id);
  };
}

function bindItem(row) {
  const id = Number(row.dataset.item);
  row.onclick = event => {
    if (event.target.closest(".quantity")) return;
    selectItem(id, true);
  };
  row.ondblclick = async event => {
    if (event.target.closest(".quantity")) return;
    await toggleItem(id);
  };
  row.oncontextmenu = event => {
    event.preventDefault();
    selectItem(id, false);
    printItem(id);
  };
  row.querySelectorAll("[data-step]").forEach(button => button.onclick = event => {
    event.stopPropagation();
    const item = findItem(id);
    setCount(id, Math.max(0, Number(item.counted) + Number(button.dataset.step)));
  });
  const input = row.querySelector("[data-count]");
  input.onclick = event => event.stopPropagation();
  input.onchange = () => setCount(id, Math.max(0, Number(input.value)));
  input.onwheel = event => {
    event.preventDefault();
    event.stopPropagation();
    const item = findItem(id);
    setCount(id, Math.max(0, Number(item.counted) + (event.deltaY < 0 ? 1 : -1)));
  };
}

function findItem(id) {
  return state.note.items.find(item => item.id === id);
}

function selectItem(id, scrollToLine = true) {
  const item = findItem(id);
  state.currentItem = id;
  state.pinnedItem = id;
  state.currentPage = item.page;
  updateHighlights();
  renderItems();
  updateViewerTools();
  $("itemsList").scrollTop = 0;
  if (scrollToLine) requestAnimationFrame(() => {
    document.querySelector(`[data-highlight="${id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
  });
}

async function toggleItem(id) {
  const noteId = state.note.id;
  const wasChecked = findItem(id).checked;
  try {
    await flushCounts();
    const note = await api(`/api/notes/${noteId}/items/${id}`, { method: "POST", body: JSON.stringify({ action: "toggle", operator: currentOperator() }) });
    if (state.note?.id !== noteId) return;
    state.note = note;
    renderNote(false);
    if (!wasChecked && findItem(id).checked) celebrate(false);
    await loadNotes(false);
  } catch (error) { toast(error.message, "error"); }
}

function setCount(id, value) {
  findItem(id).counted = value;
  renderItems();
  renderProgress();
  updateHighlights();
  state.pendingCounts.set(id, {value, noteId:state.note.id, operator:currentOperator()});
  clearTimeout(state.countTimers.get(id));
  state.countTimers.set(id, setTimeout(() => flushCount(id).catch(error => toast(error.message, 'error')), 180));
}

function flushCount(id) {
  clearTimeout(state.countTimers.get(id));
  state.countTimers.delete(id);
  const pending = state.pendingCounts.get(id);
  if (!pending) return state.countTasks.get(id) || Promise.resolve();
  if (pending.task) return pending.task;
  // Respostas incluem a nota inteira; serializar evita restaurar outro SKU com dados antigos.
  const previous = state.countQueue;
  const task = previous.catch(() => {}).then(async () => {
    const note = await api(`/api/notes/${pending.noteId}/items/${id}`, {method:'POST',body:JSON.stringify({action:'count',value:pending.value,operator:pending.operator})});
    if (state.pendingCounts.get(id) === pending) state.pendingCounts.delete(id);
    if (state.note?.id !== pending.noteId) return;
    for (const [itemId, update] of state.pendingCounts) {
      const item = note.items.find(item => item.id === itemId);
      if (item && update.noteId === note.id) item.counted = update.value;
    }
    state.note = note;
    renderNote(false);
  });
  pending.task = task;
  state.countQueue = task;
  state.countTasks.set(id, task);
  task.finally(() => {
    if (state.countTasks.get(id) === task) state.countTasks.delete(id);
    if (state.pendingCounts.get(id) === pending) delete pending.task;
  }).catch(() => {});
  return task;
}

function flushCounts() {
  return Promise.all([...state.pendingCounts.keys()].map(flushCount));
}

async function printItem(id) {
  if (state.itemBusy.has(id)) return;
  const noteId = state.note.id;
  state.itemBusy.add(id);
  renderItems();
  const item = findItem(id);
  toast(`Enviando ${fmt(item.counted)} etiqueta(s) de ${item.sku}...`);
  try {
    await flushCounts();
    const result = await api(`/api/notes/${noteId}/items/${id}`, { method: "POST", body: JSON.stringify({ action: "print", operator: currentOperator() }) });
    toast(`${result.quantity} etiqueta(s) enviada(s) para ${result.printer}`, "success");
    const note = await api(`/api/notes/${noteId}`);
    if (state.note?.id === noteId) state.note = note;
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.itemBusy.delete(id);
    renderNote(false);
  }
}

function renderProgress() {
  const total = state.note.items.length;
  const checked = state.note.items.filter(item => item.checked).length;
  const expected = state.note.items.reduce((sum, item) => sum + Number(item.expected), 0);
  const counted = state.note.items.reduce((sum, item) => sum + Number(item.counted), 0);
  const progress = total ? checked / total * 100 : 0;
  $("progressValue").textContent = `${Math.round(progress)}%`;
  $("progressBar").style.width = `${progress}%`;
  $("streak").textContent = `${checked} / ${total} itens`;
  $("scoreValue").textContent = `${checked * 100} XP`;
  $("unitStatus").textContent = `${fmt(counted)} de ${fmt(expected)} unidades`;
  const diff = counted - expected;
  $("differenceStatus").textContent = diff === 0 ? "quantidade exata" : `saldo ${fmt(diff)}`;
  $("differenceStatus").style.color = diff === 0 ? "var(--green)" : "var(--amber)";
  if (checked === total && total && state.lastChecked !== checked) celebrate(true);
  state.lastChecked = checked;
}

function celebrate(big) {
  const layer = document.createElement("div");
  layer.className = "celebration";
  const colors = ["#9d5cff", "#35d39a", "#4ad7e8", "#ffbd4a"];
  const count = big ? 42 : 12;
  for (let i = 0; i < count; i++) {
    const part = document.createElement("i");
    part.className = "particle";
    part.style.background = colors[i % colors.length];
    part.style.setProperty("--dx", `${(Math.random() - .5) * (big ? 900 : 300)}px`);
    part.style.setProperty("--dy", `${(Math.random() - .65) * (big ? 650 : 260)}px`);
    layer.appendChild(part);
  }
  document.body.appendChild(layer);
  document.querySelector(".workspace")?.classList.add("level-flash");
  setTimeout(() => { layer.remove(); document.querySelector(".workspace")?.classList.remove("level-flash"); }, 850);
}

function renderSuggestions() {
  const box = $("suggestions");
  box.hidden = !state.matches.length;
  box.innerHTML = state.matches.map((item, index) => `<button class="suggestion ${index === state.suggestionIndex ? "active" : ""}" data-match="${item.id}"><strong>${escapeHtml(item.sku)}</strong><span>${escapeHtml(item.description)}</span><small>p.${item.page}</small></button>`).join("");
  document.querySelectorAll("[data-match]").forEach(button => button.onclick = () => chooseMatch(Number(button.dataset.match)));
  box.querySelector(".active")?.scrollIntoView({ block: "nearest" });
}

function pinSuggestion(index) {
  if (!state.matches.length) return;
  state.suggestionIndex = (index + state.matches.length) % state.matches.length;
  state.pinnedItem = state.matches[state.suggestionIndex].id;
  renderSuggestions();
  renderItems();
  $("itemsList").scrollTop = 0;
}

function searchItems(query) {
  const q = query.trim().toUpperCase();
  state.matches = !state.note || !q ? [] : state.note.items.filter(item => item.sku.toUpperCase().includes(q)).slice(0, 8);
  state.suggestionIndex = 0;
  renderSuggestions();
  if (state.matches.length) {
    state.pinnedItem = state.matches[0].id;
    renderItems();
    $("itemsList").scrollTop = 0;
  }
  return state.matches;
}

function chooseMatch(id) {
  selectItem(id, true);
  $("skuSearch").value = "";
  $("suggestions").hidden = true;
  state.matches = [];
  $("itemsList").scrollTop = 0;
}

function historyAction(entry) {
  const after = entry.value?.after;
  if (entry.action === "toggle") return String(after) === "1" || after === true ? "Marcou como conferido" : "Removeu a conferencia";
  if (entry.action === "count") return `Ajustou a quantidade para ${fmt(after)}`;
  if (entry.action === "print") return `Imprimiu ${fmt(after)} etiqueta(s)`;
  if (entry.action === "undo") return `Desfez a ultima alteracao`;
  if (entry.action === "reset") return `Resetou todo o progresso da nota`;
  return entry.action;
}

async function openHistory() {
  if (!state.note) return;
  try {
    const entries = (await api(`/api/notes/${state.note.id}/history`)).history;
    $("historySubtitle").textContent = `${entries.length} evento${entries.length === 1 ? "" : "s"} - mais recentes primeiro`;
    $("historyList").innerHTML = entries.length ? entries.map((entry, index) => {
      const date = new Date(entry.created_at);
      return `<div class="history-entry ${entry.undone ? "undone" : ""}">
        <time class="history-time">${date.toLocaleDateString("pt-BR")}<br>${date.toLocaleTimeString("pt-BR")}</time>
        <div class="history-main"><strong>#${entries.length - index} ${escapeHtml(entry.sku || "Nota")}</strong><span>${escapeHtml(historyAction(entry))}</span></div>
        <span class="history-operator">${escapeHtml(entry.operator || "Sem operador")}</span>
      </div>`;
    }).join("") : '<div class="history-empty">Nenhuma conferencia registrada nesta nota.</div>';
    $("historyDialog").showModal();
  } catch (error) { toast(error.message, "error"); }
}

async function undoLast() {
  if (!state.note) return;
  $("undoBtn").disabled = true;
  try {
    await flushCounts();
    const result = await api(`/api/notes/${state.note.id}/undo`, { method: "POST", body: JSON.stringify({ operator: currentOperator() }) });
    state.note = result.note;
    state.currentItem = result.item_id;
    state.pinnedItem = result.item_id;
    renderNote(false);
    await loadNotes(false);
    toast(result.undone_action === "toggle" ? "Ultima conferencia desfeita" : "Ultima quantidade restaurada", "success");
  } catch (error) { toast(error.message, "error"); }
  finally { $("undoBtn").disabled = false; }
}

function openReset() {
  if (!state.note) return;
  $("resetNoteLabel").textContent = `NF ${state.note.number || "sem numero"}`;
  $("resetDialog").showModal();
}

async function resetProgress() {
  $("confirmReset").disabled = true;
  try {
    await flushCounts();
    const result = await api(`/api/notes/${state.note.id}/reset`, { method: "POST", body: JSON.stringify({ operator: currentOperator() }) });
    state.note = result.note;
    state.currentItem = null;
    state.pinnedItem = null;
    state.lastChecked = 0;
    $("resetDialog").close();
    renderNote(false);
    await loadNotes(false);
    toast(`${result.reset_items} item(ns) voltaram ao estado inicial`, "success");
  } catch (error) { toast(error.message, "error"); }
  finally { $("confirmReset").disabled = false; }
}

function applyZoom() {
  document.querySelectorAll(".page-stage").forEach(stage => {
    stage.style.width = `${Math.round(900 * state.zoom)}px`;
    stage.style.maxWidth = "none";
  });
  $("zoomValue").textContent = `${Math.round(state.zoom * 100)}%`;
}

function setZoom(value) {
  state.zoom = Math.min(1.6, Math.max(.3, value));
  applyZoom();
}

function fitWidth() {
  const viewportWidth = $("documentViewport").clientWidth - 50;
  state.zoom = Math.min(1, Math.max(.25, viewportWidth / 900));
  applyZoom();
}

function goToPage(page) {
  page = Math.min(state.note.pages, Math.max(1, page));
  state.currentPage = page;
  document.querySelector(`[data-page-stage="${page}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  updateViewerTools();
}

let pageObserver;
function observePages() {
  pageObserver?.disconnect();
  pageObserver = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (visible) {
      state.currentPage = Number(visible.target.dataset.pageStage);
      updateViewerTools();
    }
  }, { root: $("documentViewport"), threshold: [.2, .5, .8] });
  document.querySelectorAll("[data-page-stage]").forEach(stage => pageObserver.observe(stage));
}

function updateViewerTools() {
  const pages = state.note?.pages || 1;
  $("pageIndicator").textContent = `${state.currentPage} / ${pages}`;
  $("previousPage").disabled = state.currentPage <= 1;
  $("nextPage").disabled = state.currentPage >= pages;
  $("zoomValue").textContent = `${Math.round(state.zoom * 100)}%`;
}

$("skuSearch").oninput = event => searchItems(event.target.value);
$("skuSearch").onkeydown = event => {
  if (event.key === "ArrowDown" && state.matches.length) {
    event.preventDefault();
    pinSuggestion(state.suggestionIndex + 1);
    return;
  }
  if (event.key === "ArrowUp" && state.matches.length) {
    event.preventDefault();
    pinSuggestion(state.suggestionIndex - 1);
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    if (state.matches.length) chooseMatch(state.matches[state.suggestionIndex].id);
  }
  if (event.key === "Escape") { event.target.value = ""; state.matches = []; $("suggestions").hidden = true; }
};
$("operator").onchange = async event => {
  if (event.target.value === "__new__") {
    const name = prompt("Nome do operador:")?.trim();
    if (name) {
      await api("/api/operators", { method: "POST", body: JSON.stringify({ name }) });
      localStorage.setItem("hl-operator", name);
      await loadSettings();
    } else await loadSettings();
  } else localStorage.setItem("hl-operator", event.target.value);
};
$("scanBtn").onclick = async () => {
  try { const result = await api("/api/scan", { method: "POST" }); await loadNotes(false); toast(`${result.files} PDF(s) verificados`, "success"); }
  catch (error) { toast(error.message, "error"); }
};
$("settingsBtn").onclick = () => $("settingsDialog").showModal();
$("themeBtn").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
$("restartServer").onclick = async () => {
  const button = $("restartServer");
  button.disabled = true;
  button.textContent = "Reiniciando...";
  try {
    const result = await api("/api/restart", { method: "POST" });
    $("settingsDialog").close();
    $("connection").textContent = "Reiniciando servidor...";
    const waitForServer = async attempts => {
      try {
        const response = await fetch(`/api/health?t=${Date.now()}`, { cache: "no-store" });
        const health = await response.json();
        if (health.ok && health.instance !== result.instance) return window.location.reload();
      } catch (_) {}
      if (attempts < 40) return setTimeout(() => waitForServer(attempts + 1), 500);
      button.disabled = false;
      button.textContent = "Reiniciar servidor";
      toast("O servidor demorou para voltar. Abra o executavel novamente.", "error");
    };
    setTimeout(() => waitForServer(0), 800);
  } catch (error) {
    button.disabled = false;
    button.textContent = "Reiniciar servidor";
    toast(error.message, "error");
  }
};
$("settingsForm").onsubmit = async event => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify({ watch_dir: $("watchDir").value, printer: $("printer").value }) });
    $("settingsDialog").close();
    await api("/api/scan", { method: "POST" });
    await loadNotes(false);
    toast("Configuracoes salvas", "success");
  } catch (error) { toast(error.message, "error"); }
};
$("previousPage").onclick = () => goToPage(state.currentPage - 1);
$("nextPage").onclick = () => goToPage(state.currentPage + 1);
$("pageIndicator").onclick = () => goToPage(state.currentPage);
$("zoomOut").onclick = () => setZoom(state.zoom - .1);
$("zoomIn").onclick = () => setZoom(state.zoom + .1);
$("fitWidth").onclick = fitWidth;
$("undoBtn").onclick = undoLast;
$("historyBtn").onclick = openHistory;
$("closeHistory").onclick = () => $("historyDialog").close();
$("resetBtn").onclick = openReset;
$("resetForm").onsubmit = event => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  resetProgress();
};
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(fitWidth, 120);
});

(async function start() {
  try {
    applyTheme(document.documentElement.dataset.theme || "dark");
    await loadSettings();
    await loadNotes(true);
    $("connection").textContent = "Servidor conectado";
  } catch (error) {
    $("connection").textContent = "Falha na conexao";
    toast(error.message, "error");
  }
})();
