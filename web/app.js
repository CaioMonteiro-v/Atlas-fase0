const API = "";
const TOKEN = ""; // cole ATLAS_API_TOKEN se configurado

const state = {
  view: "home",
  sessionId: null,
  journeyId: null,
  journeys: [],
  selectedJourney: null,
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
    memory: loadMemory,
    library: () => {},
    finance: loadFinance,
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
  try {
    const d = await api("/memory/dashboard");
    const j = d.jornada_ativa;
    box.innerHTML = `
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
        <h3>Memória</h3>
        <b>${d.memorias}</b>
        <p>${d.conhecimento_nos} nós no grafo</p>
      </div>
      <div class="home-card">
        <h3>Finanças</h3>
        <b>${money(d.financas.patrimonio)}</b>
        <p>Poupança ${pct(d.financas.taxa_poupanca)} · ${d.financas.metas_ativas} meta(s)</p>
      </div>`;
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
    box.innerHTML = `<p class="muted">Não deu para carregar o painel (${escapeHtml(err.message)}).</p>`;
  }
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
        <option value="educacao">Educação</option>
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
        <button type="button" class="ghost" data-del="${m.id}">Remover</button>
      </div>`;
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
    el.style.cursor = "default";
    el.innerHTML = `
      <h3>${escapeHtml(h.titulo)}</h3>
      <p>${escapeHtml(h.descricao || "")}</p>
      <div class="meta"><span>${h.tipo}</span><span>${h.match}</span><span>score ${h.score}</span></div>`;
    box.append(el);
  }
});

// ---------- finance ----------
async function loadFinance() {
  const [health, accounts, goals, txs] = await Promise.all([
    api("/finance/health"),
    api("/finance/accounts"),
    api("/finance/goals"),
    api("/finance/transactions?limit=20"),
  ]);

  $("#finance-health").innerHTML = [
    ["Patrimônio", money(health.patrimonio)],
    ["Receita / mês", money(health.receita_mes)],
    ["Despesa / mês", money(health.despesa_mes)],
    ["Poupança", pct(health.taxa_poupanca)],
    ["Reserva", health.reserva_meses != null ? `${health.reserva_meses} meses` : "n/d"],
    ["Metas", `${health.metas_ativas} · ${pct(health.progresso_metas)}`],
  ].map(([k, v]) => `<div class="stat"><span>${k}</span><b>${v}</b></div>`).join("");

  const accBox = $("#finance-accounts");
  accBox.innerHTML = accounts.length ? "" : '<p class="muted">Crie uma conta para começar.</p>';
  for (const a of accounts) {
    const el = document.createElement("div");
    el.className = "row";
    el.style.cursor = "default";
    el.innerHTML = `
      <h3>${escapeHtml(a.name)}</h3>
      <p>${escapeHtml(a.kind)} · ${a.currency}</p>
      <div class="meta"><b>${money(a.balance)}</b></div>`;
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
      <p>${escapeHtml(t.kind)} · ${escapeHtml(t.category)}</p>
      <div class="meta"><span class="${cls}">${sign} ${money(t.amount)}</span></div>`;
    txBox.append(el);
  }
}

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
    <label>Conta<select name="account_id">${options}</select></label>
    <label>Tipo
      <select name="kind">
        <option value="despesa">despesa</option>
        <option value="receita">receita</option>
        <option value="transferencia">transferencia</option>
      </select>
    </label>
    <label>Valor<input name="amount" type="number" min="0.01" step="0.01" required></label>
    <label>Categoria<input name="category" value="geral"></label>
    <label>Descrição<input name="description" placeholder="Mercado, salário…"></label>`);
  if (!data) return;
  await api("/finance/transactions", {
    method: "POST",
    body: JSON.stringify({
      account_id: data.account_id,
      kind: data.kind,
      amount: Number(data.amount),
      category: data.category || "geral",
      description: data.description || "",
    }),
  });
  await loadFinance();
});

$("#btn-add-goal").addEventListener("click", async () => {
  const data = await openModal("Nova meta financeira", `
    <label>Título<input name="title" required placeholder="Reserva de emergência"></label>
    <label>Valor alvo (R$)<input name="target_amount" type="number" min="0.01" step="0.01" required></label>
    <label>Já juntado (R$)<input name="current_amount" type="number" min="0" step="0.01" value="0"></label>`);
  if (!data) return;
  await api("/finance/goals", {
    method: "POST",
    body: JSON.stringify({
      title: data.title,
      target_amount: Number(data.target_amount),
      current_amount: Number(data.current_amount || 0),
    }),
  });
  await loadFinance();
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
