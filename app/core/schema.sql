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
