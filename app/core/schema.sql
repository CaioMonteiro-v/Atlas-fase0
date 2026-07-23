-- =====================================================================
-- Atlas / Ayra — Núcleo de Memória (Fase 0)
-- SQLite é o alvo de desenvolvimento. O schema foi escrito para migrar
-- para Postgres sem reescrita: sem tipos exóticos, sem AUTOINCREMENT,
-- IDs em TEXT (uuid4), timestamps ISO-8601 em UTC.
-- =====================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Camada 1 — Memória Conversacional (efêmera por natureza, Cap. 30)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversational_turns (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    turn_number   INTEGER NOT NULL,
    speaker       TEXT NOT NULL CHECK (speaker IN ('user', 'ayra', 'system')),
    text          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (session_id, turn_number)
);

CREATE INDEX IF NOT EXISTS idx_turns_session
    ON conversational_turns (user_id, session_id, turn_number);

-- Sessões: permitem consolidação (Cap. 36) e expiração de efêmeros.
CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    journey_id     TEXT,
    title          TEXT,
    consolidated_at TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (journey_id) REFERENCES journeys (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id, updated_at DESC);

-- ---------------------------------------------------------------------
-- Camada 2 — Memória Pessoal (permanente, autorizada, Cap. 30/31)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS personal_memories (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    category          TEXT NOT NULL,
    content           TEXT NOT NULL,              -- JSON
    tags              TEXT NOT NULL DEFAULT '[]', -- JSON array
    privacy           TEXT NOT NULL DEFAULT 'private'
                      CHECK (privacy IN ('public', 'private', 'restricted', 'ephemeral')),
    confidence        REAL NOT NULL DEFAULT 1.0,  -- 0..1 (extraído por LLM < afirmado pelo usuário)
    source_session_id TEXT,                       -- rastreabilidade p/ Cap. 127 (transparência)
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_personal_cat
    ON personal_memories (user_id, category);
CREATE INDEX IF NOT EXISTS idx_personal_privacy
    ON personal_memories (user_id, privacy);

-- ---------------------------------------------------------------------
-- Camada 3 — Memória de Conhecimento: Grafo (Cap. 32/33)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    node_type    TEXT NOT NULL,   -- Documento | Conceito | Aplicacao | Exercicio
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    metadata     TEXT NOT NULL DEFAULT '{}',
    privacy      TEXT NOT NULL DEFAULT 'restricted'
                 CHECK (privacy IN ('public', 'private', 'restricted', 'ephemeral')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON knowledge_nodes (user_id, node_type);

-- Deduplicação de CONCEITOS: o mesmo conceito nunca deve virar dois nós.
-- Índice parcial: documentos podem repetir título, conceitos não.
CREATE UNIQUE INDEX IF NOT EXISTS uq_nodes_concept
    ON knowledge_nodes (user_id, lower(title))
    WHERE node_type = 'Conceito';

CREATE TABLE IF NOT EXISTS knowledge_edges (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    rel_type    TEXT NOT NULL,
    properties  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES knowledge_nodes (id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES knowledge_nodes (id) ON DELETE CASCADE,
    UNIQUE (source_id, target_id, rel_type)
);

-- Dois índices: o grafo precisa ser percorrido nos DOIS sentidos.
-- (o código antigo só indexava a saída — nós-alvo ficavam invisíveis)
CREATE INDEX IF NOT EXISTS idx_edges_source ON knowledge_edges (source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON knowledge_edges (target_id);

-- Busca léxica (FTS5) — metade do retrieval híbrido.
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5 (
    title,
    description,
    content = 'knowledge_nodes',
    content_rowid = 'rowid',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS trg_nodes_ai AFTER INSERT ON knowledge_nodes BEGIN
    INSERT INTO knowledge_fts (rowid, title, description)
    VALUES (new.rowid, new.title, new.description);
END;

CREATE TRIGGER IF NOT EXISTS trg_nodes_ad AFTER DELETE ON knowledge_nodes BEGIN
    INSERT INTO knowledge_fts (knowledge_fts, rowid, title, description)
    VALUES ('delete', old.rowid, old.title, old.description);
END;

CREATE TRIGGER IF NOT EXISTS trg_nodes_au AFTER UPDATE ON knowledge_nodes BEGIN
    INSERT INTO knowledge_fts (knowledge_fts, rowid, title, description)
    VALUES ('delete', old.rowid, old.title, old.description);
    INSERT INTO knowledge_fts (rowid, title, description)
    VALUES (new.rowid, new.title, new.description);
END;

-- ---------------------------------------------------------------------
-- Embeddings — tabela genérica: qualquer objeto pode ser vetorizado.
-- Guardar o nome do modelo é o que permite trocar de provedor (Cap. 119)
-- sem corromper o índice: vetores de modelos diferentes não se comparam.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS embeddings (
    owner_type  TEXT NOT NULL,   -- 'knowledge_node' | 'personal_memory' | 'turn'
    owner_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vector      BLOB NOT NULL,   -- float32 little-endian, L2-normalizado
    created_at  TEXT NOT NULL,
    PRIMARY KEY (owner_type, owner_id, model)
);

CREATE INDEX IF NOT EXISTS idx_emb_scan ON embeddings (user_id, owner_type, model);

-- ---------------------------------------------------------------------
-- Camada 4 — Jornadas e Projetos (Cap. 19/20 — a unidade central)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'ativo',
    privacy     TEXT NOT NULL DEFAULT 'private'
                CHECK (privacy IN ('public', 'private', 'restricted', 'ephemeral')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_user ON projects (user_id, status);

CREATE TABLE IF NOT EXISTS journeys (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    project_id      TEXT,
    domain          TEXT NOT NULL DEFAULT 'geral',  -- educacao | financas | gabinete | geral
    title           TEXT NOT NULL,
    stated_goal     TEXT NOT NULL DEFAULT '',  -- "quero estudar Excel"
    real_goal       TEXT NOT NULL DEFAULT '',  -- "quero conseguir um emprego" (Etapa 1: Descobrir)
    diagnosis       TEXT NOT NULL DEFAULT '{}',-- nível, prazo, recursos (Etapa 2: Diagnosticar)
    status          TEXT NOT NULL DEFAULT 'descobrindo'
                    CHECK (status IN ('descobrindo','diagnosticando','ativa','pausada','concluida','abandonada')),
    privacy         TEXT NOT NULL DEFAULT 'private'
                    CHECK (privacy IN ('public', 'private', 'restricted', 'ephemeral')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_journeys_user ON journeys (user_id, status);

CREATE TABLE IF NOT EXISTS journey_steps (
    id           TEXT PRIMARY KEY,
    journey_id   TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    order_index  INTEGER NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pendente'
                 CHECK (status IN ('pendente','em_progresso','concluida','pulada')),
    due_date     TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (journey_id) REFERENCES journeys (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_steps_journey ON journey_steps (journey_id, order_index);

-- Liga jornada <-> conhecimento: uma jornada "consome" nós do grafo.
CREATE TABLE IF NOT EXISTS journey_knowledge (
    journey_id  TEXT NOT NULL,
    node_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'material', -- material | pre_requisito | produzido
    created_at  TEXT NOT NULL,
    PRIMARY KEY (journey_id, node_id),
    FOREIGN KEY (journey_id) REFERENCES journeys (id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES knowledge_nodes (id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- Auditoria (Cap. 113 — Observabilidade / Cap. 127 — Transparência)
-- Registra o que a Ayra LEU para responder. É isso que permite a ela
-- explicar "usei X e Y" em vez de ser uma caixa-preta.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_access_log (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    session_id  TEXT,
    action      TEXT NOT NULL,   -- read | write | delete | export
    owner_type  TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON memory_access_log (user_id, created_at DESC);

-- ---------------------------------------------------------------------
-- Domínio Financeiro (Cap. 82–92) — Fase 1
-- Contas, movimentos e metas. Indicadores de saúde são calculados, não
-- persistidos: o snapshot muda a cada lançamento.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS finance_accounts (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'corrente'
                CHECK (kind IN ('corrente', 'poupanca', 'investimento', 'carteira', 'cartao', 'outro')),
    currency    TEXT NOT NULL DEFAULT 'BRL',
    balance     REAL NOT NULL DEFAULT 0,
    privacy     TEXT NOT NULL DEFAULT 'private'
                CHECK (privacy IN ('public', 'private', 'restricted', 'ephemeral')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fin_accounts_user ON finance_accounts (user_id);

CREATE TABLE IF NOT EXISTS finance_transactions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('receita', 'despesa', 'transferencia')),
    amount      REAL NOT NULL CHECK (amount > 0),
    category    TEXT NOT NULL DEFAULT 'geral',
    description TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES finance_accounts (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fin_tx_user ON finance_transactions (user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_fin_tx_account ON finance_transactions (account_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_fin_tx_category ON finance_transactions (user_id, category);

CREATE TABLE IF NOT EXISTS finance_goals (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    journey_id      TEXT,
    title           TEXT NOT NULL,
    target_amount   REAL NOT NULL CHECK (target_amount > 0),
    current_amount  REAL NOT NULL DEFAULT 0 CHECK (current_amount >= 0),
    deadline        TEXT,
    status          TEXT NOT NULL DEFAULT 'ativa'
                    CHECK (status IN ('ativa', 'concluida', 'pausada', 'abandonada')),
    privacy         TEXT NOT NULL DEFAULT 'private'
                    CHECK (privacy IN ('public', 'private', 'restricted', 'ephemeral')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (journey_id) REFERENCES journeys (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_fin_goals_user ON finance_goals (user_id, status);

-- ---------------------------------------------------------------------
-- Domínio Educação (Cap. 69–81) — estudo GERAL, não só idiomas
-- Trilhas, sessões e competências. Idiomas são uma área entre muitas.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS study_tracks (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    journey_id    TEXT,
    title         TEXT NOT NULL,
    subject_area  TEXT NOT NULL DEFAULT 'geral',
    -- exemplos: matematica | fisica | direito | medicina | programacao |
    --           historia | administracao | idiomas | musica | outro | geral
    level         TEXT NOT NULL DEFAULT 'iniciante'
                  CHECK (level IN ('iniciante', 'intermediario', 'avancado')),
    goal          TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'ativa'
                  CHECK (status IN ('ativa', 'pausada', 'concluida', 'abandonada')),
    privacy       TEXT NOT NULL DEFAULT 'private'
                  CHECK (privacy IN ('public', 'private', 'restricted', 'ephemeral')),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (journey_id) REFERENCES journeys (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_study_tracks_user ON study_tracks (user_id, status);
CREATE INDEX IF NOT EXISTS idx_study_tracks_area ON study_tracks (user_id, subject_area);

CREATE TABLE IF NOT EXISTS study_sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    track_id    TEXT NOT NULL,
    minutes     INTEGER NOT NULL CHECK (minutes > 0),
    notes       TEXT NOT NULL DEFAULT '',
    topics      TEXT NOT NULL DEFAULT '[]',
    occurred_at TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (track_id) REFERENCES study_tracks (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_study_sessions_track ON study_sessions (track_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_study_sessions_user ON study_sessions (user_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS competencies (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    track_id      TEXT,
    name          TEXT NOT NULL,
    subject_area  TEXT NOT NULL DEFAULT 'geral',
    level         TEXT NOT NULL DEFAULT 'iniciar'
                  CHECK (level IN ('iniciar', 'praticar', 'proficiente', 'dominio')),
    evidence      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'em_desenvolvimento'
                  CHECK (status IN ('em_desenvolvimento', 'adquirida', 'a_revisar')),
    privacy       TEXT NOT NULL DEFAULT 'private'
                  CHECK (privacy IN ('public', 'private', 'restricted', 'ephemeral')),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (track_id) REFERENCES study_tracks (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_competencies_user ON competencies (user_id, status);

-- Caderno de anotações: o que o aluno registrou que aprendeu (Cap. 61/72).
CREATE TABLE IF NOT EXISTS study_notes (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    track_id    TEXT NOT NULL,
    session_id  TEXT,
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL,
    topic       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (track_id) REFERENCES study_tracks (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_study_notes_track ON study_notes (track_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_study_notes_user ON study_notes (user_id, created_at DESC);

-- Capítulos da trilha (aula viva — Cap. 70/72)
CREATE TABLE IF NOT EXISTS study_chapters (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    track_id      TEXT NOT NULL,
    order_index   INTEGER NOT NULL,
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    objectives    TEXT NOT NULL DEFAULT '[]',  -- JSON array
    status        TEXT NOT NULL DEFAULT 'pendente'
                  CHECK (status IN ('pendente', 'em_progresso', 'concluido')),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (track_id) REFERENCES study_tracks (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_study_chapters_track ON study_chapters (track_id, order_index);

-- Quizzes de checagem (Cap. 79 — evidência de competência)
CREATE TABLE IF NOT EXISTS study_quizzes (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    track_id      TEXT NOT NULL,
    chapter_id    TEXT,
    title         TEXT NOT NULL,
    questions     TEXT NOT NULL DEFAULT '[]',  -- JSON: [{id, pergunta, opcoes?, resposta_esperada, explicacao}]
    answers       TEXT NOT NULL DEFAULT '[]',  -- JSON: [{question_id, resposta, correto?, feedback}]
    score         REAL,                        -- 0..1
    status        TEXT NOT NULL DEFAULT 'aberto'
                  CHECK (status IN ('aberto', 'corrigido')),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (track_id) REFERENCES study_tracks (id) ON DELETE CASCADE,
    FOREIGN KEY (chapter_id) REFERENCES study_chapters (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_study_quizzes_track ON study_quizzes (track_id, created_at DESC);

-- Materiais da trilha → nós do grafo (Cap. 71/117)
CREATE TABLE IF NOT EXISTS study_materials (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    track_id      TEXT NOT NULL,
    node_id       TEXT,              -- knowledge_nodes.id (Documento)
    title         TEXT NOT NULL,
    formato       TEXT NOT NULL DEFAULT 'text',
    status        TEXT NOT NULL DEFAULT 'processando'
                  CHECK (status IN ('processando', 'pronto', 'erro')),
    created_at    TEXT NOT NULL,
    FOREIGN KEY (track_id) REFERENCES study_tracks (id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES knowledge_nodes (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_study_materials_track ON study_materials (track_id, created_at DESC);

-- Revisão espaçada (Cap. 78)
CREATE TABLE IF NOT EXISTS study_review_cards (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    track_id        TEXT NOT NULL,
    note_id         TEXT,
    chapter_id      TEXT,
    prompt          TEXT NOT NULL,
    answer          TEXT NOT NULL DEFAULT '',
    ease            REAL NOT NULL DEFAULT 2.5,
    interval_days   INTEGER NOT NULL DEFAULT 1,
    repetitions     INTEGER NOT NULL DEFAULT 0,
    next_review_at  TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (track_id) REFERENCES study_tracks (id) ON DELETE CASCADE,
    FOREIGN KEY (note_id) REFERENCES study_notes (id) ON DELETE SET NULL,
    FOREIGN KEY (chapter_id) REFERENCES study_chapters (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_study_reviews_due
    ON study_review_cards (user_id, next_review_at);
CREATE INDEX IF NOT EXISTS idx_study_reviews_track
    ON study_review_cards (track_id, next_review_at);

-- Plano semanal de estudo
CREATE TABLE IF NOT EXISTS study_weekly_plans (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    track_id         TEXT,
    week_start       TEXT NOT NULL,
    target_minutes   INTEGER NOT NULL DEFAULT 180,
    target_sessions  INTEGER NOT NULL DEFAULT 3,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    FOREIGN KEY (track_id) REFERENCES study_tracks (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_study_weekly
    ON study_weekly_plans (user_id, week_start, IFNULL(track_id, ''));

CREATE INDEX IF NOT EXISTS idx_study_weekly_user ON study_weekly_plans (user_id, week_start DESC);

-- ---------------------------------------------------------------------
-- Domínio Gabinete Inteligente (Cap. 97–105)
-- Cidadão no centro: demandas, linha do tempo, agenda.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cabinet_citizens (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    municipality  TEXT NOT NULL DEFAULT '',
    contact       TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    privacy       TEXT NOT NULL DEFAULT 'private'
                  CHECK (privacy IN ('public', 'private', 'restricted', 'ephemeral')),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cabinet_citizens_user ON cabinet_citizens (user_id);
CREATE INDEX IF NOT EXISTS idx_cabinet_citizens_muni ON cabinet_citizens (user_id, municipality);

CREATE TABLE IF NOT EXISTS cabinet_demands (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    citizen_id    TEXT,
    title         TEXT NOT NULL,
    subject       TEXT NOT NULL DEFAULT '',
    municipality  TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT 'geral',
    priority      TEXT NOT NULL DEFAULT 'media'
                  CHECK (priority IN ('baixa', 'media', 'alta', 'urgente')),
    status        TEXT NOT NULL DEFAULT 'aberta'
                  CHECK (status IN ('aberta', 'em_andamento', 'aguardando', 'concluida', 'arquivada')),
    origin        TEXT NOT NULL DEFAULT '',
    assignee      TEXT NOT NULL DEFAULT '',
    due_date      TEXT,
    result        TEXT NOT NULL DEFAULT '',
    privacy       TEXT NOT NULL DEFAULT 'private'
                  CHECK (privacy IN ('public', 'private', 'restricted', 'ephemeral')),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (citizen_id) REFERENCES cabinet_citizens (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cabinet_demands_user ON cabinet_demands (user_id, status);
CREATE INDEX IF NOT EXISTS idx_cabinet_demands_muni ON cabinet_demands (user_id, municipality);

CREATE TABLE IF NOT EXISTS cabinet_timeline (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    citizen_id    TEXT,
    demand_id     TEXT,
    municipality  TEXT NOT NULL DEFAULT '',
    event_type    TEXT NOT NULL DEFAULT 'nota'
                  CHECK (event_type IN ('contato', 'demanda', 'documento', 'visita', 'reuniao', 'retorno', 'nota')),
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    occurred_at   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (citizen_id) REFERENCES cabinet_citizens (id) ON DELETE SET NULL,
    FOREIGN KEY (demand_id) REFERENCES cabinet_demands (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cabinet_timeline_user ON cabinet_timeline (user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_cabinet_timeline_citizen ON cabinet_timeline (citizen_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS cabinet_agenda (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    title              TEXT NOT NULL,
    municipality       TEXT NOT NULL DEFAULT '',
    related_demand_id  TEXT,
    starts_at          TEXT NOT NULL,
    notes              TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'agendado'
                       CHECK (status IN ('agendado', 'realizado', 'cancelado')),
    privacy            TEXT NOT NULL DEFAULT 'private'
                       CHECK (privacy IN ('public', 'private', 'restricted', 'ephemeral')),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    FOREIGN KEY (related_demand_id) REFERENCES cabinet_demands (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cabinet_agenda_user ON cabinet_agenda (user_id, starts_at);
