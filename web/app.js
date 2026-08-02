const API = "";
const TOKEN = ""; // cole ATLAS_API_TOKEN se configurado

const state = {
  view: "home",
  sessionId: null,
  journeyId: null,
  journeys: [],
  selectedJourney: null,
  selectedTrack: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function headers(json = true) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  if (TOKEN) h.Authorization = `Bearer ${TOKEN}`;
  return h;
}

async function api(path, opts = {}) {
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: { ...headers(!(opts.body instanceof FormData)), ...opts.headers },
  });
  if (res.status === 204) return null;
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const detail = data?.detail || data?.erro || (typeof data === "string" ? data : null);
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return data;
}

function money(n) {
  return Number(n || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function pct(n) {
  return `${Math.round((n || 0) * 100)}%`;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// ---------- navigation ----------
function setView(name) {
  state.view = name;
  $$(".nav-item").forEach((b) => b.classList.toggle("is-active", b.dataset.view === name));
  $$("[data-view-panel]").forEach((panel) => {
    const on = panel.dataset.viewPanel === name;
    panel.classList.toggle("is-active", on);
    panel.hidden = !on;
  });
  const loaders = {
    home: loadHome,
    ayra: () => { refreshJourneySelect(); showWelcomeIfEmpty(); },
    journeys: loadJourneys,
    education: loadEducation,
    finance: loadFinance,
    cabinet: loadCabinet,
    memory: loadMemory,
    library: loadLibrary,
  };
  loaders[name]?.();
}

$$(".nav-item").forEach((btn) => btn.addEventListener("click", () => setView(btn.dataset.view)));
$(".brand").addEventListener("click", () => setView("home"));

// ---------- chat / Ayra ----------
const logEl = $("#log");
const fontesEl = $("#fontes");
const form = $("#chat-form");
const input = $("#msg");
const send = $("#send");
const sess = $("#sess");
const journeySelect = $("#chat-journey");
const journeyChip = $("#journey-chip");

function showWelcome() {
  logEl.innerHTML = `
    <div class="welcome">
      <h2>Olá. Eu sou a Ayra.</h2>
      <p>Conte um objetivo — aprender, organizar finanças, criar um projeto.
      Eu descubro o destino real e construo a jornada com você.</p>
    </div>`;
}

function showWelcomeIfEmpty() {
  if (!logEl.querySelector(".msg")) showWelcome();
}

function bubble(cls, text = "") {
  const welcome = $(".welcome", logEl);
  if (welcome) welcome.remove();
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.textContent = text;
  logEl.append(el);
  logEl.scrollTop = logEl.scrollHeight;
  return el;
}

function renderFontes(lista) {
  fontesEl.innerHTML = "";
  if (!lista?.length) {
    fontesEl.innerHTML = '<p class="muted">Respondeu sem consultar a memória.</p>';
    return;
  }
  for (const f of lista) {
    const el = document.createElement("div");
    el.className = "fonte";
    el.innerHTML = `<small>${f.tipo}</small>`;
    el.append(f.rotulo);
    fontesEl.append(el);
  }
}

function renderJourneyChip(j) {
  if (!j) {
    journeyChip.hidden = true;
    return;
  }
  journeyChip.hidden = false;
  journeyChip.innerHTML = `
    <strong>Jornada na conversa</strong>
    ${escapeHtml(j.title)} · ${pct(j.progress)}
    <div class="progress" style="margin-top:8px"><span style="width:${(j.progress || 0) * 100}%"></span></div>`;
}

async function refreshJourneySelect() {
  try {
    state.journeys = await api("/journeys");
  } catch {
    state.journeys = [];
  }
  const current = state.journeyId || "";
  journeySelect.innerHTML = '<option value="">sem amarrar</option>' +
    state.journeys.map((j) =>
      `<option value="${j.id}" ${j.id === current ? "selected" : ""}>${escapeHtml(j.title)}</option>`
    ).join("");
}

journeySelect.addEventListener("change", () => {
  state.journeyId = journeySelect.value || null;
});

async function* sseFrom(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split("\n\n");
    buf = frames.pop();
    for (const frame of frames) {
      let event = "message";
      const data = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      yield { event, data: data.join("\n") };
    }
  }
}

async function sendChat(text, { clearInput = true } = {}) {
  if (!text?.trim()) return;
  bubble("user", text);
  if (clearInput) {
    input.value = "";
    input.style.height = "auto";
  }
  send.disabled = true;

  const reply = bubble("ayra");
  reply.classList.add("streaming");

  try {
    const res = await fetch(`${API}/chat`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        message: text,
        session_id: state.sessionId,
        journey_id: state.journeyId || null,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    for await (const { event, data } of sseFrom(res)) {
      if (event === "session") {
        state.sessionId = data;
        sess.textContent = `sessão ${data.slice(0, 8)}`;
      } else if (event === "token") {
        reply.textContent += data;
        logEl.scrollTop = logEl.scrollHeight;
      } else if (event === "sources") {
        renderFontes(JSON.parse(data));
      } else if (event === "journey") {
        const j = JSON.parse(data);
        state.journeyId = j.id;
        journeySelect.value = j.id;
        renderJourneyChip(j);
      } else if (event === "error") {
        bubble("erro", `A Ayra falhou no meio da resposta: ${data}`);
      }
    }
  } catch (err) {
    reply.remove();
    bubble("erro", `Não deu para falar com o servidor (${err.message}).`);
  } finally {
    reply.classList.remove("streaming");
    send.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  await sendChat(input.value.trim());
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

$("#btn-new-session").addEventListener("click", async () => {
  if (state.sessionId) {
    try { await api(`/chat/${state.sessionId}/close`, { method: "POST" }); } catch {}
  }
  state.sessionId = null;
  sess.textContent = "sessão nova";
  showWelcome();
  fontesEl.innerHTML = '<p class="muted">As fontes de cada resposta aparecem aqui.</p>';
  journeyChip.hidden = true;
  setView("ayra");
});

// ---------- modal helper ----------
const modal = $("#modal");
const modalFields = $("#modal-fields");
const modalTitle = $("#modal-title");
let modalResolve = null;

$("#modal-cancel").addEventListener("click", () => {
  modal.close();
  modalResolve?.(null);
});

$("#modal-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData($("#modal-form")).entries());
  delete data.cancel;
  modal.close();
  modalResolve?.(data);
});

function openModal(title, fieldsHtml, { okLabel = "Salvar" } = {}) {
  modalTitle.textContent = title;
  modalFields.innerHTML = fieldsHtml;
  $("#modal-ok").textContent = okLabel;
  return new Promise((resolve) => {
    modalResolve = resolve;
    modal.showModal();
    const first = modalFields.querySelector("input, select, textarea");
    first?.focus();
  });
}

// ---------- home / onboarding ----------
async function loadHome() {
  const box = $("#home-dash");
  const morningBox = $("#home-morning");
  try {
    const [d, brief] = await Promise.all([
      api("/memory/dashboard"),
      api("/ayra/dia-seguinte").catch(() => null),
    ]);
    renderMorning(brief);

    const j = d.jornada_ativa;
    const alerts = (d.alertas || []).map((a) =>
      `<button type="button" class="home-alert ${a.nivel || ""}" data-view="${a.view || "home"}">${escapeHtml(a.texto)}</button>`
    ).join("") || '<p class="muted">Nenhum alerta agora. Bom sinal.</p>';
    box.innerHTML = `
      <div class="home-card span-alerts">
        <h3>Atenção</h3>
        <div class="alert-stack">${alerts}</div>
      </div>
      <div class="home-card">
        <h3>Jornada ativa</h3>
        <b>${j ? escapeHtml(j.title) : "Nenhuma"}</b>
        <p>${j ? `${pct(j.progress)} · ${escapeHtml(j.domain)}` : "Comece com um objetivo abaixo."}</p>
        ${j ? `<div class="progress"><span style="width:${(j.progress || 0) * 100}%"></span></div>
          <div class="actions"><button type="button" class="ghost" id="home-open-j">Abrir</button>
          <button type="button" class="primary" id="home-talk-j">Falar com Ayra</button></div>` : ""}
      </div>
      <div class="home-card">
        <h3>Próximo passo</h3>
        <b>${d.proximo_passo ? escapeHtml(d.proximo_passo.title) : "—"}</b>
        <p>${d.jornadas_ativas} ativa(s) · ${d.jornadas_total} no total</p>
      </div>
      <div class="home-card">
        <h3>Estudos</h3>
        <b>${d.educacao?.trilhas_ativas ?? 0} trilhas</b>
        <p>${d.educacao?.revisoes_vencidas ?? 0} revisões · ${d.educacao?.minutos_semana ?? 0} min/semana</p>
      </div>
      <div class="home-card">
        <h3>Gabinete</h3>
        <b>${d.gabinete?.demandas_abertas ?? 0} abertas</b>
        <p>${d.gabinete?.demandas_urgentes ?? 0} urgentes · ${d.gabinete?.demandas_atrasadas ?? 0} atrasadas</p>
      </div>
      <div class="home-card">
        <h3>Finanças</h3>
        <b>${money(d.financas.patrimonio)}</b>
        <p>Poupança ${pct(d.financas.taxa_poupanca)} · reserva ${d.financas.reserva_meses ?? "n/d"} m</p>
      </div>
      <div class="home-card">
        <h3>Memória</h3>
        <b>${d.memorias}</b>
        <p>${d.conhecimento_nos} nós no grafo</p>
      </div>`;
    $$(".home-alert", box).forEach((btn) => btn.addEventListener("click", () => setView(btn.dataset.view)));
    $("#home-open-j")?.addEventListener("click", () => {
      state.selectedJourney = j.id;
      setView("journeys");
      selectJourney(j.id);
    });
    $("#home-talk-j")?.addEventListener("click", () => {
      state.journeyId = j.id;
      setView("ayra");
      refreshJourneySelect();
    });
  } catch (err) {
    morningBox.hidden = true;
    box.innerHTML = `<p class="muted">Não deu para carregar o painel (${escapeHtml(err.message)}).</p>`;
  }
}

function renderMorning(brief) {
  const box = $("#home-morning");
  if (!brief) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const done = brief.status === "done";
  box.className = "morning-hero" + (done ? " is-done" : "");
  box.innerHTML = `
    <p class="eyebrow">Ayra do dia seguinte · ${escapeHtml(brief.day || "")}</p>
    <h2>${escapeHtml(brief.title || "Foco do dia")}</h2>
    <p class="action-line">${escapeHtml(brief.action_text || "")}</p>
    <p class="why">${escapeHtml(brief.reason || "")}</p>
    <div class="meta-row">
      <span>${escapeHtml(brief.domain || "geral")}</span>
      <span>${brief.minutes || 15} min</span>
      <span>${done ? "feito" : "pendente"}</span>
    </div>
    <div class="actions">
      ${done
        ? `<button type="button" class="primary" id="btn-morning-again">Reabrir com Ayra</button>
           <button type="button" class="ghost" id="btn-morning-refresh">Novo foco</button>`
        : `<button type="button" class="primary" id="btn-morning-go">Fazer agora com a Ayra</button>
           <button type="button" class="ghost" id="btn-morning-done">Marcar feito</button>
           <button type="button" class="ghost" id="btn-morning-skip">Pular</button>
           <button type="button" class="ghost" id="btn-morning-open">Só abrir ${escapeHtml(brief.view || "área")}</button>`}
    </div>`;

  $("#btn-morning-go")?.addEventListener("click", () => startMorningBrief());
  $("#btn-morning-again")?.addEventListener("click", () => startMorningBrief());
  $("#btn-morning-done")?.addEventListener("click", async () => {
    await api("/ayra/dia-seguinte/status", { method: "POST", body: JSON.stringify({ status: "done" }) });
    await loadHome();
  });
  $("#btn-morning-skip")?.addEventListener("click", async () => {
    await api("/ayra/dia-seguinte/status", { method: "POST", body: JSON.stringify({ status: "skipped" }) });
    const next = await api("/ayra/dia-seguinte?force=true");
    renderMorning(next);
  });
  $("#btn-morning-refresh")?.addEventListener("click", async () => {
    const next = await api("/ayra/dia-seguinte?force=true");
    renderMorning(next);
  });
  $("#btn-morning-open")?.addEventListener("click", () => {
    const view = brief.view || "ayra";
    if (view === "ayra") startMorningBrief();
    else setView(view);
  });
}

async function startMorningBrief() {
  const res = await api("/ayra/dia-seguinte/start", { method: "POST" });
  state.sessionId = res.session_id;
  state.journeyId = res.journey_id || null;
  sess.textContent = `sessão ${res.session_id.slice(0, 8)}`;
  setView("ayra");
  await refreshJourneySelect();
  if (res.journey_id) journeySelect.value = res.journey_id;
  showWelcome();
  await sendChat(res.mensagem_sugerida, { clearInput: false });
}

$("#onboard-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const goal = $("#onboard-goal").value.trim();
  const domain = $("#onboard-domain").value;
  if (!goal) return;
  const btn = $("#onboard-go");
  btn.disabled = true;
  btn.textContent = "Planejando…";
  try {
    const data = await api("/chat/onboard", {
      method: "POST",
      body: JSON.stringify({ goal, domain }),
    });
    state.sessionId = data.session_id;
    state.journeyId = data.journey.id;
    state.selectedJourney = data.journey.id;
    sess.textContent = `sessão ${data.session_id.slice(0, 8)}`;
    setView("ayra");
    await refreshJourneySelect();
    journeySelect.value = data.journey.id;
    renderJourneyChip({
      title: data.journey.title,
      progress: data.journey.progress,
    });
    showWelcome();
    await sendChat(data.mensagem_sugerida, { clearInput: false });
    $("#onboard-goal").value = "";
    await loadHome();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Começar";
  }
});

// ---------- journeys ----------
async function loadJourneys() {
  state.journeys = await api("/journeys");
  const list = $("#journey-list");
  list.innerHTML = "";
  if (!state.journeys.length) {
    list.innerHTML = '<p class="muted">Nenhuma jornada ainda. Use o onboarding no Início ou crie aqui.</p>';
    return;
  }
  for (const j of state.journeys) {
    const el = document.createElement("div");
    el.className = "row" + (state.selectedJourney === j.id ? " is-selected" : "");
    el.innerHTML = `
      <h3>${escapeHtml(j.title)}</h3>
      <p>${escapeHtml(j.real_goal || j.stated_goal || "objetivo em descoberta")}</p>
      <div class="meta"><span>${j.domain}</span><span>${j.status}</span><span>${pct(j.progress)}</span></div>
      <div class="progress"><span style="width:${(j.progress || 0) * 100}%"></span></div>`;
    el.addEventListener("click", () => selectJourney(j.id));
    list.append(el);
  }
}

async function selectJourney(id) {
  state.selectedJourney = id;
  const j = await api(`/journeys/${id}`);
  await loadJourneys();
  const pane = $("#journey-detail");
  const steps = (j.steps || []).map((s) => `
    <div class="step">
      <div style="flex:1">
        <strong>${escapeHtml(s.title)}</strong>
        <p class="muted">${escapeHtml(s.description || "")}</p>
        <div class="meta">${s.status}</div>
      </div>
      <button type="button" class="ghost" data-step="${s.id}" data-status="concluida">Concluir</button>
    </div>`).join("") || '<p class="muted">Sem passos ainda. Peça à Ayra para planejar.</p>';

  pane.innerHTML = `
    <h2>${escapeHtml(j.title)}</h2>
    <p class="muted">Declarado: ${escapeHtml(j.stated_goal || "—")}</p>
    <p><strong>Objetivo real:</strong> ${escapeHtml(j.real_goal || "ainda não descoberto")}</p>
    <div class="meta" style="margin:10px 0 18px">
      <span>${j.domain}</span> · <span>${j.status}</span> · <span>${pct(j.progress)}</span>
    </div>
    <div class="actions" style="margin-bottom:16px">
      <button type="button" class="primary" id="btn-plan">Ayra, planejar</button>
      <button type="button" class="ghost" id="btn-talk-journey">Conversar nesta jornada</button>
      <button type="button" class="ghost" id="btn-diagnose">Definir objetivo real</button>
      <button type="button" class="ghost" id="btn-pause-j">Pausar</button>
      <button type="button" class="ghost" id="btn-done-j">Concluir</button>
      <button type="button" class="ghost" id="btn-del-journey">Excluir</button>
    </div>
    <h3 class="section-title">Passos</h3>
    ${steps}`;

  $("#btn-plan")?.addEventListener("click", async () => {
    $("#btn-plan").disabled = true;
    $("#btn-plan").textContent = "Planejando…";
    try {
      await api(`/journeys/${id}/plan`, { method: "POST" });
      await selectJourney(id);
    } catch (err) {
      alert(err.message);
    }
  });
  $("#btn-talk-journey")?.addEventListener("click", () => {
    state.journeyId = id;
    setView("ayra");
    refreshJourneySelect();
  });
  $("#btn-diagnose")?.addEventListener("click", async () => {
    const data = await openModal("Objetivo real", `
      <label>O que você realmente quer?<textarea name="real_goal" rows="3" required
        placeholder="Ex.: conseguir emprego, não só 'aprender Excel'">${escapeHtml(j.real_goal || "")}</textarea></label>`);
    if (!data) return;
    await api(`/journeys/${id}/diagnose`, {
      method: "POST",
      body: JSON.stringify({ real_goal: data.real_goal, diagnosis: { origem: "usuario" } }),
    });
    await selectJourney(id);
  });
  $("#btn-pause-j")?.addEventListener("click", async () => {
    await api(`/journeys/${id}`, { method: "PATCH", body: JSON.stringify({ status: "pausada" }) });
    await selectJourney(id);
  });
  $("#btn-done-j")?.addEventListener("click", async () => {
    await api(`/journeys/${id}`, { method: "PATCH", body: JSON.stringify({ status: "concluida" }) });
    await selectJourney(id);
  });
  $("#btn-del-journey")?.addEventListener("click", async () => {
    if (!confirm("Excluir esta jornada?")) return;
    await api(`/journeys/${id}`, { method: "DELETE" });
    state.selectedJourney = null;
    $("#journey-detail").innerHTML = '<p class="muted">Selecione uma jornada.</p>';
    await loadJourneys();
  });
  $$("[data-step]", pane).forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/journeys/${id}/steps/${btn.dataset.step}?new_status=${btn.dataset.status}`, {
        method: "PATCH",
      });
      await selectJourney(id);
    });
  });
}

$("#btn-new-journey").addEventListener("click", async () => {
  const data = await openModal("Nova jornada", `
    <label>Título<input name="title" required placeholder="Aprender inglês"></label>
    <label>Objetivo declarado<textarea name="stated_goal" rows="2" placeholder="Quero estudar inglês para…"></textarea></label>
    <label>Domínio
      <select name="domain">
        <option value="geral">Geral</option>
        <option value="educacao">Estudos</option>
        <option value="financas">Finanças</option>
        <option value="gabinete">Gabinete</option>
      </select>
    </label>`);
  if (!data) return;
  const j = await api("/journeys", { method: "POST", body: JSON.stringify(data) });
  await loadJourneys();
  await selectJourney(j.id);
});

// ---------- memory ----------
async function loadMemory() {
  const items = await api("/memory/personal");
  const list = $("#memory-list");
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<p class="muted">Nada na memória pessoal ainda. A Ayra consolida fatos duráveis após algumas conversas — ou adicione manualmente.</p>';
    return;
  }
  for (const m of items) {
    const texto = m.content?.texto || JSON.stringify(m.content);
    const el = document.createElement("div");
    el.className = "row";
    el.style.cursor = "default";
    el.innerHTML = `
      <h3>${escapeHtml(m.category)}</h3>
      <p>${escapeHtml(texto)}</p>
      <div class="meta"><span>${m.privacy}</span><span>confiança ${m.confidence}</span></div>
      <div class="actions" style="margin-top:10px">
        <button type="button" class="ghost" data-edit="${m.id}">Editar</button>
        <button type="button" class="ghost" data-del="${m.id}">Remover</button>
      </div>`;
    el.querySelector("[data-edit]").addEventListener("click", async () => {
      const data = await openModal("Editar memória", `
        <label>Conteúdo<textarea name="texto" rows="3" required>${escapeHtml(texto)}</textarea></label>`);
      if (!data) return;
      await api(`/memory/personal/${m.id}`, {
        method: "PATCH",
        body: JSON.stringify({ content: { texto: data.texto } }),
      });
      await loadMemory();
    });
    el.querySelector("[data-del]").addEventListener("click", async () => {
      await api(`/memory/personal/${m.id}`, { method: "DELETE" });
      await loadMemory();
    });
    list.append(el);
  }
}

$("#btn-add-memory").addEventListener("click", async () => {
  const data = await openModal("Nova memória", `
    <label>Categoria
      <select name="category">
        <option value="objetivos">objetivos</option>
        <option value="preferencias">preferencias</option>
        <option value="rotina">rotina</option>
        <option value="contexto">contexto</option>
        <option value="restricoes">restricoes</option>
      </select>
    </label>
    <label>Conteudo<textarea name="texto" rows="3" required></textarea></label>`);
  if (!data) return;
  await api("/memory/personal", {
    method: "POST",
    body: JSON.stringify({ category: data.category, content: { texto: data.texto }, tags: [] }),
  });
  await loadMemory();
});

$("#btn-export").addEventListener("click", async () => {
  const data = await api("/memory/export");
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "atlas-memoria.json";
  a.click();
});

$("#btn-audit").addEventListener("click", async () => {
  const panel = $("#audit-panel");
  panel.hidden = !panel.hidden;
  if (panel.hidden) return;
  const items = await api("/memory/audit?limit=40");
  const box = $("#audit-list");
  box.innerHTML = items.length ? "" : '<p class="muted">Nenhum acesso registrado ainda.</p>';
  for (const a of items) {
    const el = document.createElement("div");
    el.className = "row";
    el.style.cursor = "default";
    el.innerHTML = `
      <h3>${escapeHtml(a.action)} · ${escapeHtml(a.owner_type)}</h3>
      <p>${escapeHtml(a.reason || "—")}</p>
      <div class="meta"><span>${escapeHtml(a.created_at || "")}</span><span>${escapeHtml(a.owner_id?.slice?.(0, 8) || "")}</span></div>`;
    box.append(el);
  }
});

$("#btn-wipe").addEventListener("click", async () => {
  const data = await openModal("Apagar toda a memória", `
    <p class="muted">Isso remove jornadas, finanças, conhecimento e conversas deste usuário. Irreversível.</p>
    <label>Digite <strong>APAGAR TUDO</strong> para confirmar
      <input name="confirm" required autocomplete="off" placeholder="APAGAR TUDO">
    </label>`, { okLabel: "Apagar" });
  if (!data) return;
  if (data.confirm !== "APAGAR TUDO") {
    alert("Confirmação inválida.");
    return;
  }
  await api(`/memory/wipe?confirm=${encodeURIComponent("APAGAR TUDO")}`, { method: "POST" });
  state.sessionId = null;
  state.journeyId = null;
  alert("Memória apagada.");
  await loadMemory();
  $("#audit-panel").hidden = true;
});

// ---------- library ----------
async function loadLibrary() {
  const nodes = await api("/knowledge/nodes?limit=30");
  const box = $("#library-recent");
  box.innerHTML = "<h2 class='section-title'>Documentos recentes</h2>";
  if (!nodes.length) {
    box.insertAdjacentHTML("beforeend", '<p class="muted">Nada no grafo ainda. Ingera um arquivo acima.</p>');
    return;
  }
  for (const n of nodes) {
    const el = document.createElement("div");
    el.className = "row";
    el.innerHTML = `
      <h3>${escapeHtml(n.title)}</h3>
      <p>${escapeHtml((n.description || "").slice(0, 160))}</p>
      <div class="meta"><span>${escapeHtml(n.node_type)}</span></div>
      <div class="actions" style="margin-top:8px">
        <button type="button" class="ghost" data-nei="${n.id}">Vizinhos</button>
        <button type="button" class="ghost" data-del-n="${n.id}">Apagar</button>
      </div>`;
    el.querySelector("[data-nei]").addEventListener("click", () => showNeighbors(n.id));
    el.querySelector("[data-del-n]").addEventListener("click", async () => {
      if (!confirm("Apagar este nó do grafo?")) return;
      await api(`/knowledge/${n.id}`, { method: "DELETE" });
      await loadLibrary();
    });
    box.append(el);
  }
}

async function showNeighbors(nodeId) {
  const data = await api(`/knowledge/${nodeId}/neighbors`);
  const panel = $("#library-neighbors");
  panel.hidden = false;
  const list = (data.neighbors || []).map((n) =>
    `<div class="row" style="cursor:default"><h3>${escapeHtml(n.title)}</h3><p>${escapeHtml(n.node_type)}</p></div>`
  ).join("") || '<p class="muted">Sem vizinhos.</p>';
  panel.innerHTML = `<h2>Vizinhos de «${escapeHtml(data.node?.title || "")}»</h2>${list}`;
}

$("#ingest-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("#ingest-file").files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API}/knowledge/ingest`, {
    method: "POST",
    headers: TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {},
    body: fd,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    alert(`Falha na ingestão: ${data.detail || res.status}`);
    return;
  }
  $("#library-results").innerHTML = `<div class="row"><h3>Processando (${escapeHtml(data.formato || "arquivo")})</h3>
    <p>${escapeHtml(data.titulo)} — ${data.caracteres || "?"} caracteres. O grafo está sendo estruturado em background.</p></div>`;
  $("#ingest-file").value = "";
  setTimeout(loadLibrary, 2500);
});

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#search-q").value.trim();
  if (!q) return;
  const hits = await api(`/knowledge/search?q=${encodeURIComponent(q)}`);
  const box = $("#library-results");
  box.innerHTML = "";
  if (!hits.length) {
    box.innerHTML = '<p class="muted">Nada encontrado no grafo.</p>';
    return;
  }
  for (const h of hits) {
    const el = document.createElement("div");
    el.className = "row";
    el.innerHTML = `
      <h3>${escapeHtml(h.titulo)}</h3>
      <p>${escapeHtml(h.descricao || "")}</p>
      <div class="meta"><span>${h.tipo}</span><span>${h.match}</span><span>score ${h.score}</span></div>
      <div class="actions" style="margin-top:8px">
        <button type="button" class="ghost" data-nei="${h.id}">Vizinhos</button>
      </div>`;
    el.querySelector("[data-nei]").addEventListener("click", () => showNeighbors(h.id));
    box.append(el);
  }
});

// ---------- finance ----------
async function loadFinance() {
  const [health, accounts, goals, txs, report] = await Promise.all([
    api("/finance/health"),
    api("/finance/accounts"),
    api("/finance/goals"),
    api("/finance/transactions?limit=20"),
    api("/finance/report"),
  ]);

  $("#finance-health").innerHTML = [
    ["Patrimônio", money(health.patrimonio)],
    ["Receita / mês", money(health.receita_mes)],
    ["Despesa / mês", money(health.despesa_mes)],
    ["Poupança", pct(health.taxa_poupanca)],
    ["Reserva", health.reserva_meses != null ? `${health.reserva_meses} meses` : "n/d"],
    ["Metas", `${health.metas_ativas} · ${pct(health.progresso_metas)}`],
  ].map(([k, v]) => `<div class="stat"><span>${k}</span><b>${v}</b></div>`).join("");

  const cats = (report.por_categoria || []).map((c) =>
    `<div class="cat-bar"><span>${escapeHtml(c.categoria)}</span>
      <div class="progress is-thick"><span style="width:${c.pct || 0}%"></span></div>
      <b>${money(c.total)}</b></div>`
  ).join("") || '<p class="muted">Sem despesas categorizadas neste mês.</p>';
  $("#finance-report").innerHTML = `
    <h2>Fluxo de ${escapeHtml(report.month)}</h2>
    <p class="muted">Receita ${money(report.receita)} · Despesa ${money(report.despesa)} · Poupança ${money(report.poupanca)} · ${report.lancamentos} lançamentos</p>
    ${cats}`;

  const accBox = $("#finance-accounts");
  accBox.innerHTML = accounts.length ? "" : '<p class="muted">Crie uma conta para começar.</p>';
  for (const a of accounts) {
    const el = document.createElement("div");
    el.className = "row";
    el.style.cursor = "default";
    el.innerHTML = `
      <h3>${escapeHtml(a.name)}</h3>
      <p>${escapeHtml(a.kind)} · ${a.currency}</p>
      <div class="meta"><b>${money(a.balance)}</b>
        <button type="button" class="ghost" data-del-acc="${a.id}">Apagar</button></div>`;
    el.querySelector("[data-del-acc]").addEventListener("click", async () => {
      if (!confirm("Apagar conta e lançamentos?")) return;
      await api(`/finance/accounts/${a.id}`, { method: "DELETE" });
      await loadFinance();
    });
    accBox.append(el);
  }

  const goalBox = $("#finance-goals");
  goalBox.innerHTML = goals.length ? "" : '<p class="muted">Nenhuma meta financeira.</p>';
  for (const g of goals) {
    const el = document.createElement("div");
    el.className = "row";
    el.style.cursor = "default";
    el.innerHTML = `
      <h3>${escapeHtml(g.title)}</h3>
      <p>${money(g.current_amount)} de ${money(g.target_amount)}</p>
      <div class="progress"><span style="width:${(g.progress || 0) * 100}%"></span></div>
      <div class="actions" style="margin-top:10px">
        <button type="button" class="ghost" data-goal="${g.id}">Atualizar</button>
        <button type="button" class="ghost" data-del-goal="${g.id}">Apagar</button>
      </div>`;
    el.querySelector("[data-goal]").addEventListener("click", async () => {
      const data = await openModal("Progresso da meta", `
        <label>Valor atual (R$)<input name="current_amount" type="number" min="0" step="0.01" value="${g.current_amount}" required></label>`);
      if (!data) return;
      await api(`/finance/goals/${g.id}`, {
        method: "PATCH",
        body: JSON.stringify({ current_amount: Number(data.current_amount) }),
      });
      await loadFinance();
    });
    el.querySelector("[data-del-goal]").addEventListener("click", async () => {
      await api(`/finance/goals/${g.id}`, { method: "DELETE" });
      await loadFinance();
    });
    goalBox.append(el);
  }

  const txBox = $("#finance-txs");
  txBox.innerHTML = txs.length ? "" : '<p class="muted">Sem lançamentos.</p>';
  for (const t of txs) {
    const el = document.createElement("div");
    el.className = "row";
    el.style.cursor = "default";
    const cls = t.kind === "receita" ? "amount-pos" : "amount-neg";
    const sign = t.kind === "receita" ? "+" : "−";
    el.innerHTML = `
      <h3>${escapeHtml(t.description || t.category)}</h3>
      <p>${escapeHtml(t.kind)} · ${escapeHtml(t.category)}${t.to_account_id ? " · transferência" : ""}</p>
      <div class="meta"><span class="${cls}">${sign} ${money(t.amount)}</span>
        <button type="button" class="ghost" data-del-tx="${t.id}">Apagar</button></div>`;
    el.querySelector("[data-del-tx]").addEventListener("click", async () => {
      await api(`/finance/transactions/${t.id}`, { method: "DELETE" });
      await loadFinance();
    });
    txBox.append(el);
  }
}

$("#btn-fin-ayra")?.addEventListener("click", async () => {
  const data = await openModal("Consultoria financeira com a Ayra", `
    <label>Objetivo<textarea name="goal" rows="2" required placeholder="Quero montar reserva / sair do vermelho…"></textarea></label>
    <label>Foco
      <select name="focus">
        <option value="organizar">organizar</option>
        <option value="reserva">reserva</option>
        <option value="dividas">dívidas</option>
        <option value="investir">investir</option>
        <option value="orcamento">orçamento</option>
      </select>
    </label>`);
  if (!data) return;
  const res = await api("/finance/start-with-ayra", {
    method: "POST",
    body: JSON.stringify({ goal: data.goal, focus: data.focus }),
  });
  state.sessionId = res.session_id;
  state.journeyId = res.journey.id;
  sess.textContent = `sessão ${res.session_id.slice(0, 8)}`;
  setView("ayra");
  await refreshJourneySelect();
  journeySelect.value = res.journey.id;
  await sendChat(res.mensagem_sugerida, { clearInput: false });
});

$("#btn-add-account").addEventListener("click", async () => {
  const data = await openModal("Nova conta", `
    <label>Nome<input name="name" required placeholder="Conta corrente"></label>
    <label>Tipo
      <select name="kind">
        <option value="corrente">corrente</option>
        <option value="poupanca">poupanca</option>
        <option value="investimento">investimento</option>
        <option value="carteira">carteira</option>
        <option value="cartao">cartao</option>
        <option value="outro">outro</option>
      </select>
    </label>
    <label>Saldo inicial<input name="balance" type="number" step="0.01" value="0"></label>`);
  if (!data) return;
  await api("/finance/accounts", {
    method: "POST",
    body: JSON.stringify({
      name: data.name,
      kind: data.kind,
      balance: Number(data.balance || 0),
    }),
  });
  await loadFinance();
});

$("#btn-add-tx").addEventListener("click", async () => {
  const accounts = await api("/finance/accounts");
  if (!accounts.length) {
    alert("Crie uma conta primeiro.");
    return;
  }
  const options = accounts.map((a) => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join("");
  const data = await openModal("Novo lançamento", `
    <label>Conta (origem)<select name="account_id">${options}</select></label>
    <label>Tipo
      <select name="kind" id="tx-kind">
        <option value="despesa">despesa</option>
        <option value="receita">receita</option>
        <option value="transferencia">transferencia</option>
      </select>
    </label>
    <label>Destino (só transferência)<select name="to_account_id"><option value="">—</option>${options}</select></label>
    <label>Valor<input name="amount" type="number" min="0.01" step="0.01" required></label>
    <label>Categoria<input name="category" value="geral" placeholder="moradia, mercado, salario…"></label>
    <label>Descrição<input name="description" placeholder="Mercado, salário…"></label>`);
  if (!data) return;
  const payload = {
    account_id: data.account_id,
    kind: data.kind,
    amount: Number(data.amount),
    category: data.category || "geral",
    description: data.description || "",
  };
  if (data.kind === "transferencia") {
    if (!data.to_account_id) {
      alert("Escolha a conta destino.");
      return;
    }
    payload.to_account_id = data.to_account_id;
  }
  await api("/finance/transactions", { method: "POST", body: JSON.stringify(payload) });
  await loadFinance();
});

$("#btn-add-goal").addEventListener("click", async () => {
  const data = await openModal("Nova meta financeira", `
    <label>Título<input name="title" required placeholder="Reserva de emergência"></label>
    <label>Valor alvo (R$)<input name="target_amount" type="number" min="0.01" step="0.01" required></label>
    <label>Já juntado (R$)<input name="current_amount" type="number" min="0" step="0.01" value="0"></label>
    <label>Prazo<input name="deadline" type="date"></label>`);
  if (!data) return;
  const body = {
    title: data.title,
    target_amount: Number(data.target_amount),
    current_amount: Number(data.current_amount || 0),
  };
  if (data.deadline) body.deadline = new Date(data.deadline).toISOString();
  await api("/finance/goals", { method: "POST", body: JSON.stringify(body) });
  await loadFinance();
});

// ---------- education (estudo geral + mentora Ayra) ----------
async function loadEducation() {
  const [snap, tracks, comps, notes, due, week] = await Promise.all([
    api("/education/snapshot"),
    api("/education/tracks"),
    api("/education/competencies"),
    api("/education/notes?limit=30"),
    api("/education/reviews/due?limit=20"),
    api("/education/weekly-plan"),
  ]);
  $("#edu-summary").innerHTML = [
    ["Trilhas", snap.tracks_ativas?.length ?? 0],
    ["Capítulos pendentes", snap.capitulos_pendentes ?? 0],
    ["Revisões vencidas", snap.revisoes_vencidas ?? due.length ?? 0],
    ["Minutos / semana", snap.minutos_semana ?? 0],
  ].map(([k, v]) => `<div class="stat"><span>${k}</span><b>${escapeHtml(String(v))}</b></div>`).join("");

  renderWeeklyPlan(week);
  renderDueReviews(due);

  const tBox = $("#edu-tracks");
  tBox.innerHTML = tracks.length ? "" : '<p class="muted">Nenhuma trilha ainda. Digite o que quer aprender acima.</p>';
  for (const t of tracks) {
    const el = document.createElement("div");
    el.className = "row" + (state.selectedTrack === t.id ? " is-selected" : "");
    el.innerHTML = `
      <h3>${escapeHtml(t.title)}</h3>
      <p>${escapeHtml(t.subject_area)} · ${escapeHtml(t.level)}</p>
      <div class="meta"><span>${escapeHtml(t.goal || "aprender com compreensão")}</span></div>
      <div class="actions" style="margin-top:10px">
        <button type="button" class="ghost" data-open="${t.id}">Abrir</button>
        <button type="button" class="primary" data-mentor="${t.id}">Continuar com Ayra</button>
      </div>`;
    el.querySelector("[data-open]").addEventListener("click", (ev) => {
      ev.stopPropagation();
      openTrack(t);
    });
    el.querySelector("[data-mentor]").addEventListener("click", (ev) => {
      ev.stopPropagation();
      continueWithAyra(t);
    });
    el.addEventListener("click", () => openTrack(t));
    tBox.append(el);
  }

  const nBox = $("#edu-notes");
  nBox.innerHTML = notes.length ? "" : '<p class="muted">Seu caderno está vazio. Depois de estudar com a Ayra, anote o que entendeu.</p>';
  for (const n of notes) {
    const el = document.createElement("div");
    el.className = "row";
    el.style.cursor = "default";
    el.innerHTML = `
      <h3>${escapeHtml(n.title || n.topic || "Aprendizado")}</h3>
      <p>${escapeHtml(n.content)}</p>
      <div class="meta"><span>${escapeHtml(n.topic || "")}</span>
        <button type="button" class="ghost" data-del-note="${n.id}">Apagar</button></div>`;
    el.querySelector("[data-del-note]").addEventListener("click", async () => {
      await api(`/education/notes/${n.id}`, { method: "DELETE" });
      await loadEducation();
    });
    nBox.append(el);
  }

  const cBox = $("#edu-comps");
  cBox.innerHTML = comps.length ? "" : '<p class="muted">Competências aparecem quando você acerta quizzes.</p>';
  for (const c of comps) {
    const el = document.createElement("div");
    el.className = "row";
    el.style.cursor = "default";
    el.innerHTML = `
      <h3>${escapeHtml(c.name)}</h3>
      <p>${escapeHtml(c.subject_area)} · ${escapeHtml(c.level)}</p>
      <div class="meta"><span>${escapeHtml(c.status)}</span><span>${escapeHtml(c.evidence || "")}</span></div>`;
    cBox.append(el);
  }

  if (state.selectedTrack) {
    const t = tracks.find((x) => x.id === state.selectedTrack);
    if (t) await renderTrackDetail(t);
  } else {
    $("#edu-progress").hidden = true;
  }
}

function renderWeeklyPlan(week) {
  const box = $("#edu-week");
  const pct = week?.pct_minutes ?? 0;
  box.innerHTML = `
    <h2>Plano da semana</h2>
    <p class="muted">Semana de ${escapeHtml(week?.week_start || "—")} · meta ${week?.target_minutes ?? 180} min / ${week?.target_sessions ?? 3} sessões</p>
    <div class="progress is-thick"><span style="width:${pct}%"></span></div>
    <p class="muted" style="margin-top:6px">${week?.minutes_done ?? 0} min feitos · ${week?.sessions_done ?? 0} sessões</p>
    <div class="actions" style="margin-top:10px">
      <button type="button" class="ghost" id="btn-set-week">Ajustar meta</button>
    </div>`;
  $("#btn-set-week")?.addEventListener("click", async () => {
    const data = await openModal("Meta semanal de estudo", `
      <label>Minutos alvo<input name="target_minutes" type="number" min="30" max="2000" value="${week?.target_minutes ?? 180}"></label>
      <label>Sessões alvo<input name="target_sessions" type="number" min="1" max="21" value="${week?.target_sessions ?? 3}"></label>
      <p class="muted">Vale para a semana atual (segunda → domingo).</p>`);
    if (!data) return;
    await api("/education/weekly-plan", {
      method: "PUT",
      body: JSON.stringify({
        target_minutes: Number(data.target_minutes) || 180,
        target_sessions: Number(data.target_sessions) || 3,
        track_id: state.selectedTrack || null,
      }),
    });
    await loadEducation();
  });
}

function renderDueReviews(due) {
  const box = $("#edu-reviews");
  if (!due.length) {
    box.innerHTML = `<h2>Revisão espaçada</h2><p class="muted">Nada vencido. Anotações viram cartões automaticamente.</p>`;
    return;
  }
  box.innerHTML = `<h2>Revisão espaçada · ${due.length} vencida(s)</h2>`;
  for (const card of due.slice(0, 8)) {
    const el = document.createElement("div");
    el.className = "review-card";
    el.innerHTML = `
      <strong>${escapeHtml(card.prompt)}</strong>
      <p class="muted" style="margin:6px 0 0">Resposta (sua anotação): ${escapeHtml(card.answer || "—")}</p>
      <div class="review-actions">
        <button type="button" class="ghost" data-g="again">De novo</button>
        <button type="button" class="ghost" data-g="hard">Difícil</button>
        <button type="button" class="primary" data-g="good">Bom</button>
        <button type="button" class="ghost" data-g="easy">Fácil</button>
      </div>`;
    el.querySelectorAll("[data-g]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api(`/education/reviews/${card.id}/grade`, {
          method: "POST",
          body: JSON.stringify({ rating: btn.dataset.g }),
        });
        await loadEducation();
      });
    });
    box.append(el);
  }
}

async function openTrack(track) {
  state.selectedTrack = track.id;
  await loadEducation();
}

async function renderTrackDetail(track) {
  const [chapters, materials, progress] = await Promise.all([
    api(`/education/tracks/${track.id}/chapters`),
    api(`/education/tracks/${track.id}/materials`),
    api(`/education/tracks/${track.id}/progress`),
  ]);

  const prog = $("#edu-progress");
  prog.hidden = false;
  const map = (progress.chapters || []).map((ch) => {
    const cls = ch.status === "concluido" ? "is-done" : (ch.status === "em_progresso" ? "is-now" : "");
    return `<span class="chapter-dot ${cls}" title="${escapeHtml(ch.title)}">${ch.order_index + 1}</span>`;
  }).join("");
  prog.innerHTML = `
    <h2>Progresso · ${escapeHtml(track.title)}</h2>
    <p class="muted">${progress.chapters_done}/${progress.chapters_total} capítulos · ${progress.chapters_pct}% · ${progress.reviews_due} revisões · ${progress.minutes_week} min esta semana</p>
    <div class="progress is-thick"><span style="width:${progress.chapters_pct || 0}%"></span></div>
    <div class="chapter-map">${map || '<span class="muted">Sem mapa ainda</span>'}</div>
    <div class="actions" style="margin-top:8px">
      <button type="button" class="primary" id="btn-simulado">Simulado da trilha</button>
    </div>`;
  $("#btn-simulado")?.addEventListener("click", () => runSimulado(track.id, track.title));

  const chBox = $("#edu-chapters");
  if (!chapters.length) {
    chBox.innerHTML = `
      <p class="muted">Sem capítulos ainda.</p>
      <button type="button" class="primary" id="btn-gen-chapters">Gerar capítulos com Ayra</button>`;
    $("#btn-gen-chapters")?.addEventListener("click", async () => {
      $("#btn-gen-chapters").disabled = true;
      $("#btn-gen-chapters").textContent = "Gerando…";
      await api(`/education/tracks/${track.id}/chapters/generate`, { method: "POST" });
      await renderTrackDetail(track);
    });
  } else {
    chBox.innerHTML = "";
    for (const ch of chapters) {
      const el = document.createElement("div");
      el.className = "row";
      el.style.cursor = "default";
      el.innerHTML = `
        <h3>${ch.order_index + 1}. ${escapeHtml(ch.title)}</h3>
        <p>${escapeHtml(ch.summary || "")}</p>
        <div class="meta"><span>${ch.status}</span></div>
        <div class="actions" style="margin-top:8px">
          <button type="button" class="primary" data-study-ch="${ch.id}">Estudar</button>
          <button type="button" class="ghost" data-quiz-ch="${ch.id}">Quiz</button>
          <button type="button" class="ghost" data-done-ch="${ch.id}">Concluir</button>
        </div>`;
      el.querySelector("[data-study-ch]").addEventListener("click", async () => {
        await api(`/education/chapters/${ch.id}?new_status=em_progresso`, { method: "PATCH" });
        state.journeyId = track.journey_id || null;
        setView("ayra");
        await refreshJourneySelect();
        if (track.journey_id) journeySelect.value = track.journey_id;
        await sendChat(
          `Vamos estudar o capítulo «${ch.title}» de ${track.title}. ${ch.summary} ` +
          `Objetivos: ${(ch.objectives || []).join("; ")}. Ensine e faça uma pergunta de checagem.`,
          { clearInput: false }
        );
      });
      el.querySelector("[data-quiz-ch]").addEventListener("click", () => runQuiz(track.id, ch.id, ch.title));
      el.querySelector("[data-done-ch]").addEventListener("click", async () => {
        await api(`/education/chapters/${ch.id}?new_status=concluido`, { method: "PATCH" });
        await renderTrackDetail(track);
      });
      chBox.append(el);
    }
    const quizAll = document.createElement("button");
    quizAll.className = "ghost";
    quizAll.textContent = "Quiz geral da trilha";
    quizAll.style.marginTop = "8px";
    quizAll.addEventListener("click", () => runQuiz(track.id, null, track.title));
    chBox.append(quizAll);
  }

  const mBox = $("#edu-materials");
  mBox.innerHTML = `
    <form id="mat-form" class="inline-form" style="margin-bottom:10px">
      <input type="file" id="mat-file" accept=".txt,.md,.pdf,application/pdf" required>
      <button type="submit" class="primary">Enviar material</button>
    </form>
    <p class="muted" style="margin-bottom:8px">PDF escaneado? O Atlas tenta OCR automático.</p>`;
  if (!materials.length) {
    mBox.insertAdjacentHTML("beforeend", '<p class="muted">Nenhum material ainda.</p>');
  }
  for (const m of materials) {
    const el = document.createElement("div");
    el.className = "row";
    el.style.cursor = "default";
    el.innerHTML = `<h3>${escapeHtml(m.title)}</h3><div class="meta"><span>${m.formato}</span><span>${m.status}</span></div>`;
    mBox.append(el);
  }
  $("#mat-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = $("#mat-file").files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${API}/education/tracks/${track.id}/materials`, {
      method: "POST",
      headers: TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {},
      body: fd,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert(data.detail || res.status);
      return;
    }
    alert(data.aviso || "Material enviado.");
    await renderTrackDetail(track);
  });
}

async function runQuiz(trackId, chapterId, label) {
  const q = chapterId
    ? await api(`/education/tracks/${trackId}/quizzes?chapter_id=${chapterId}`, { method: "POST" })
    : await api(`/education/tracks/${trackId}/quizzes`, { method: "POST" });
  await takeQuiz(q, `Quiz: ${label}`);
}

async function runSimulado(trackId, label) {
  const q = await api(`/education/tracks/${trackId}/simulado`, { method: "POST" });
  await takeQuiz(q, `Simulado: ${label}`);
}

async function takeQuiz(q, title) {
  const fields = (q.questions || []).map((qq, i) => `
    <label>${i + 1}. ${escapeHtml(qq.pergunta)}
      <textarea name="a_${qq.id}" rows="2" required placeholder="Sua resposta"></textarea>
    </label>`).join("");

  const data = await openModal(title, fields || "<p>Sem perguntas</p>", { okLabel: "Enviar" });
  if (!data) return;

  const answers = (q.questions || []).map((qq) => ({
    question_id: qq.id,
    resposta: data[`a_${qq.id}`] || "",
  }));
  const graded = await api(`/education/quizzes/${q.id}/submit`, {
    method: "POST",
    body: JSON.stringify({ answers }),
  });
  const score = graded.score != null ? Math.round(graded.score * 100) : "?";
  const fb = (graded.answers || []).map((a) =>
    `${a.correto ? "✓" : "✗"} ${a.feedback || ""}`
  ).join("\n");
  alert(`Score: ${score}%\n\n${fb}`);
  await loadEducation();
}

async function continueWithAyra(track) {
  state.journeyId = track.journey_id || null;
  setView("ayra");
  await refreshJourneySelect();
  if (track.journey_id) journeySelect.value = track.journey_id;
  const msg = (
    `Vamos continuar «${track.title}» (${track.subject_area}). ` +
    `Nível ${track.level}. Objetivo: ${track.goal || "compreensão real"}. ` +
    `Retome do próximo capítulo/conceito e me faça uma pergunta de checagem no final.`
  );
  await sendChat(msg, { clearInput: false });
}

async function addNoteForTrack(track) {
  const data = await openModal("O que você aprendeu?", `
    <p class="muted">Trilha: <strong>${escapeHtml(track.title)}</strong></p>
    <label>Título curto<input name="title" placeholder="Ex.: Princípio da legalidade"></label>
    <label>Tópico<input name="topic" value="${escapeHtml(track.subject_area)}"></label>
    <label>Anotação (com suas palavras)<textarea name="content" rows="5" required
      placeholder="O que entendi hoje…"></textarea></label>`);
  if (!data) return;
  await api("/education/notes", {
    method: "POST",
    body: JSON.stringify({
      track_id: track.id,
      title: data.title || "",
      topic: data.topic || track.subject_area,
      content: data.content,
      session_id: state.sessionId,
    }),
  });
  await loadEducation();
}

$("#study-start-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const topic = $("#study-topic").value.trim();
  const level = $("#study-level").value;
  const goal = $("#study-goal").value.trim();
  if (!topic) return;
  const btn = $("#study-go");
  btn.disabled = true;
  btn.textContent = "Montando curso…";
  try {
    const data = await api("/education/start-with-ayra", {
      method: "POST",
      body: JSON.stringify({ topic, level, goal, subject_area: topic }),
    });
    state.sessionId = data.session_id;
    state.journeyId = data.journey.id;
    state.selectedTrack = data.track.id;
    sess.textContent = `sessão ${data.session_id.slice(0, 8)}`;
    setView("ayra");
    await refreshJourneySelect();
    journeySelect.value = data.journey.id;
    renderJourneyChip({ title: data.journey.title, progress: data.journey.progress });
    showWelcome();
    await sendChat(data.mensagem_sugerida, { clearInput: false });
    $("#study-topic").value = "";
    $("#study-goal").value = "";
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Estudar com a Ayra";
  }
});

$("#btn-add-note").addEventListener("click", async () => {
  const tracks = await api("/education/tracks");
  if (!tracks.length) {
    alert("Comece uma trilha em «O que você quer aprender?» primeiro.");
    return;
  }
  const preferred = state.selectedTrack || tracks[0].id;
  const options = tracks.map((t) =>
    `<option value="${t.id}" ${t.id === preferred ? "selected" : ""}>${escapeHtml(t.title)}</option>`
  ).join("");
  const data = await openModal("Nova anotação de aprendizado", `
    <label>Trilha<select name="track_id">${options}</select></label>
    <label>Título<input name="title" placeholder="Conceito do dia"></label>
    <label>Tópico<input name="topic" placeholder="Ex.: controle de constitucionalidade"></label>
    <label>O que aprendi<textarea name="content" rows="5" required></textarea></label>`);
  if (!data) return;
  await api("/education/notes", {
    method: "POST",
    body: JSON.stringify({
      track_id: data.track_id,
      title: data.title || "",
      topic: data.topic || "",
      content: data.content,
      session_id: state.sessionId,
    }),
  });
  await loadEducation();
});

// ---------- cabinet ----------
async function loadCabinet() {
  const statusFilter = $("#cab-filter-status")?.value || "";
  const demandQs = statusFilter ? `?status_filter=${encodeURIComponent(statusFilter)}` : "";
  const [snap, demands, citizens, timeline, agenda] = await Promise.all([
    api("/cabinet/snapshot"),
    api(`/cabinet/demands${demandQs}`),
    api("/cabinet/citizens"),
    api("/cabinet/timeline?limit=20"),
    api("/cabinet/agenda"),
  ]);
  $("#cab-summary").innerHTML = [
    ["Abertas", snap.demandas_abertas],
    ["Urgentes", snap.demandas_urgentes],
    ["Atrasadas", snap.demandas_atrasadas ?? 0],
    ["Agenda", (snap.agenda || []).length],
  ].map(([k, v]) => `<div class="stat"><span>${k}</span><b>${v}</b></div>`).join("");

  const dBox = $("#cab-demands");
  dBox.innerHTML = demands.length ? "" : '<p class="muted">Nenhuma demanda.</p>';
  for (const d of demands) {
    const overdue = d.due_date && new Date(d.due_date) < new Date() && !["concluida", "arquivada"].includes(d.status);
    const el = document.createElement("div");
    el.className = "row" + (overdue ? " is-overdue" : "");
    el.style.cursor = "default";
    el.innerHTML = `
      <h3>${escapeHtml(d.title)}</h3>
      <p>${escapeHtml(d.municipality || "s/ município")} · ${escapeHtml(d.category)}</p>
      <div class="meta"><span>${d.priority}</span><span>${d.status}</span>${overdue ? "<span>atrasada</span>" : ""}</div>
      <div class="actions" style="margin-top:8px">
        <button type="button" class="ghost" data-demand="${d.id}">Andamento</button>
        <button type="button" class="ghost" data-del-d="${d.id}">Apagar</button>
      </div>`;
    el.querySelector("[data-demand]").addEventListener("click", async () => {
      const data = await openModal("Atualizar demanda", `
        <label>Status
          <select name="status">
            <option value="aberta">aberta</option>
            <option value="em_andamento" selected>em_andamento</option>
            <option value="aguardando">aguardando</option>
            <option value="concluida">concluida</option>
            <option value="arquivada">arquivada</option>
          </select>
        </label>
        <label>Resultado<textarea name="result" rows="2"></textarea></label>`);
      if (!data) return;
      await api(`/cabinet/demands/${d.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: data.status, result: data.result || undefined }),
      });
      await loadCabinet();
    });
    el.querySelector("[data-del-d]").addEventListener("click", async () => {
      await api(`/cabinet/demands/${d.id}`, { method: "DELETE" });
      await loadCabinet();
    });
    dBox.append(el);
  }

  const cBox = $("#cab-citizens");
  cBox.innerHTML = citizens.length ? "" : '<p class="muted">Nenhum cidadão cadastrado.</p>';
  for (const c of citizens) {
    const el = document.createElement("div");
    el.className = "row";
    el.innerHTML = `
      <h3>${escapeHtml(c.name)}</h3>
      <p>${escapeHtml(c.municipality || "—")} · ${escapeHtml(c.contact || "")}</p>
      <div class="actions" style="margin-top:8px">
        <button type="button" class="ghost" data-dos="${c.id}">Dossiê</button>
        <button type="button" class="ghost" data-del-c="${c.id}">Apagar</button>
      </div>`;
    el.querySelector("[data-dos]").addEventListener("click", () => openCitizenDossier(c.id));
    el.querySelector("[data-del-c]").addEventListener("click", async () => {
      await api(`/cabinet/citizens/${c.id}`, { method: "DELETE" });
      await loadCabinet();
    });
    cBox.append(el);
  }

  const aBox = $("#cab-agenda");
  aBox.innerHTML = agenda.length ? "" : '<p class="muted">Agenda vazia.</p>';
  for (const a of agenda) {
    const el = document.createElement("div");
    el.className = "row";
    el.style.cursor = "default";
    const when = a.starts_at ? new Date(a.starts_at).toLocaleString("pt-BR") : "—";
    el.innerHTML = `
      <h3>${escapeHtml(a.title)}</h3>
      <p>${escapeHtml(when)} · ${escapeHtml(a.municipality || "")}</p>
      <div class="meta"><span>${a.status}</span></div>
      <div class="actions" style="margin-top:8px">
        <button type="button" class="ghost" data-ag-done="${a.id}">Realizado</button>
        <button type="button" class="ghost" data-ag-cancel="${a.id}">Cancelar</button>
      </div>`;
    el.querySelector("[data-ag-done]").addEventListener("click", async () => {
      await api(`/cabinet/agenda/${a.id}`, { method: "PATCH", body: JSON.stringify({ status: "realizado" }) });
      await loadCabinet();
    });
    el.querySelector("[data-ag-cancel]").addEventListener("click", async () => {
      await api(`/cabinet/agenda/${a.id}`, { method: "PATCH", body: JSON.stringify({ status: "cancelado" }) });
      await loadCabinet();
    });
    aBox.append(el);
  }

  const tBox = $("#cab-timeline");
  tBox.innerHTML = timeline.length ? "" : '<p class="muted">Linha do tempo vazia.</p>';
  for (const e of timeline) {
    const el = document.createElement("div");
    el.className = "row";
    el.style.cursor = "default";
    el.innerHTML = `
      <h3>${escapeHtml(e.title)}</h3>
      <p>${escapeHtml(e.description || "")}</p>
      <div class="meta"><span>${e.event_type}</span><span>${escapeHtml(e.municipality || "")}</span></div>`;
    tBox.append(el);
  }
}

async function openCitizenDossier(citizenId) {
  const data = await api(`/cabinet/citizens/${citizenId}`);
  const wrap = $("#cab-dossier-wrap");
  const box = $("#cab-dossier");
  wrap.hidden = false;
  const c = data.citizen;
  const dem = (data.demands || []).map((d) => `<li>${escapeHtml(d.title)} · ${d.status}</li>`).join("") || "<li>sem demandas</li>";
  const tl = (data.timeline || []).slice(0, 8).map((e) => `<li>${escapeHtml(e.title)}</li>`).join("") || "<li>sem eventos</li>";
  box.innerHTML = `
    <h3>${escapeHtml(c.name)}</h3>
    <p class="muted">${escapeHtml(c.municipality || "")} · ${escapeHtml(c.contact || "")}</p>
    <p>${escapeHtml(c.notes || "")}</p>
    <h4>Demandas</h4><ul>${dem}</ul>
    <h4>Timeline</h4><ul>${tl}</ul>`;
}

$("#cab-filter-status")?.addEventListener("change", () => loadCabinet());

$("#btn-cab-ayra")?.addEventListener("click", async () => {
  const data = await openModal("Assessoria de gabinete com a Ayra", `
    <label>Assunto<textarea name="topic" rows="2" required placeholder="Priorizar demandas de Sobral / ofício X…"></textarea></label>
    <label>Município<input name="municipality" placeholder="opcional"></label>`);
  if (!data) return;
  const res = await api("/cabinet/start-with-ayra", {
    method: "POST",
    body: JSON.stringify({ topic: data.topic, municipality: data.municipality || "" }),
  });
  state.sessionId = res.session_id;
  state.journeyId = res.journey.id;
  sess.textContent = `sessão ${res.session_id.slice(0, 8)}`;
  setView("ayra");
  await refreshJourneySelect();
  journeySelect.value = res.journey.id;
  await sendChat(res.mensagem_sugerida, { clearInput: false });
});

$("#btn-add-citizen").addEventListener("click", async () => {
  const data = await openModal("Novo cidadão", `
    <label>Nome<input name="name" required></label>
    <label>Município<input name="municipality" placeholder="Ex.: Sobral"></label>
    <label>Contato<input name="contact" placeholder="telefone / e-mail"></label>
    <label>Notas<textarea name="notes" rows="2"></textarea></label>`);
  if (!data) return;
  await api("/cabinet/citizens", { method: "POST", body: JSON.stringify(data) });
  await loadCabinet();
});

$("#btn-add-demand").addEventListener("click", async () => {
  const citizens = await api("/cabinet/citizens");
  const opts = '<option value="">— sem vincular —</option>' +
    citizens.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
  const data = await openModal("Nova demanda", `
    <label>Título<input name="title" required placeholder="Pavimentação / Ofício / Emprego"></label>
    <label>Assunto<textarea name="subject" rows="2"></textarea></label>
    <label>Município<input name="municipality"></label>
    <label>Categoria<input name="category" value="geral"></label>
    <label>Prioridade
      <select name="priority">
        <option value="baixa">baixa</option>
        <option value="media" selected>media</option>
        <option value="alta">alta</option>
        <option value="urgente">urgente</option>
      </select>
    </label>
    <label>Prazo<input name="due_date" type="date"></label>
    <label>Cidadão<select name="citizen_id">${opts}</select></label>`);
  if (!data) return;
  const body = {
    title: data.title,
    subject: data.subject || "",
    municipality: data.municipality || "",
    category: data.category || "geral",
    priority: data.priority,
    citizen_id: data.citizen_id || null,
  };
  if (data.due_date) body.due_date = new Date(data.due_date).toISOString();
  await api("/cabinet/demands", { method: "POST", body: JSON.stringify(body) });
  await loadCabinet();
});

$("#btn-add-agenda").addEventListener("click", async () => {
  const data = await openModal("Novo compromisso", `
    <label>Título<input name="title" required placeholder="Reunião com prefeito"></label>
    <label>Quando<input name="starts_at" type="datetime-local" required></label>
    <label>Município<input name="municipality"></label>
    <label>Notas<textarea name="notes" rows="2"></textarea></label>`);
  if (!data) return;
  const iso = new Date(data.starts_at).toISOString();
  await api("/cabinet/agenda", {
    method: "POST",
    body: JSON.stringify({
      title: data.title,
      starts_at: iso,
      municipality: data.municipality || "",
      notes: data.notes || "",
    }),
  });
  await loadCabinet();
});

// ---------- boot ----------
async function boot() {
  showWelcome();
  try {
    const h = await api("/health");
    $("#llm-badge").textContent = `llm · ${h.llm}`;
  } catch {
    $("#llm-badge").textContent = "offline";
  }
  await loadHome();
  await refreshJourneySelect();
}

boot();
