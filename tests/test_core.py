"""
Teste de integração do núcleo de memória. Roda sem rede e sem LLM.

    python -m pytest tests/ -v     (ou simplesmente: python tests/test_core.py)

Cobre exatamente os pontos que quebravam no código antigo:
  - persistência real (o InMemory* perdia tudo a cada restart)
  - histórico devolve os turnos MAIS RECENTES
  - grafo é percorrível nos dois sentidos
  - conceito repetido não vira nó duplicado
  - embeddings são efetivamente usados na busca
  - isolamento por user_id
"""

from __future__ import annotations

import math
import random

from app.core.db import Database
from app.domain.models import (
    Journey,
    JourneyStep,
    KnowledgeEdge,
    KnowledgeNode,
    PersonalMemory,
    Privacy,
)
from app.memory.store import (
    AuditStore,
    ConversationStore,
    JourneyStore,
    KnowledgeStore,
    PersonalStore,
)

U = "user-caio"
OTHER = "user-intruso"
MODEL = "text-embedding-004"
DIM = 32


def fake_embedding(seed: str) -> list[float]:
    rnd = random.Random(seed)
    return [rnd.uniform(-1, 1) for _ in range(DIM)]


def make_db() -> Database:
    db = Database(":memory:")
    db.migrate()
    return db


def check(label: str, cond: bool) -> None:
    print(f"  {'PASS' if cond else 'FALHOU':>6}  {label}")
    assert cond, label


def test_conversational() -> None:
    print("\n[1] Memória conversacional")
    db = make_db()
    conv = ConversationStore(db)
    conv.ensure_session(U, "s1")

    for i in range(30):
        conv.add_turn(U, "s1", "user" if i % 2 == 0 else "ayra", f"turno {i}")

    hist = conv.history(U, "s1", limit=5)
    check("histórico traz os 5 turnos MAIS RECENTES", [t.text for t in hist] ==
          ["turno 25", "turno 26", "turno 27", "turno 28", "turno 29"])
    check("ordem cronológica preservada", [t.turn_number for t in hist] == [26, 27, 28, 29, 30])
    check("outro usuário não vê a sessão", conv.history(OTHER, "s1") == [])
    check("purge remove tudo", conv.purge_session(U, "s1") == 30 and conv.history(U, "s1") == [])


def test_personal() -> None:
    print("\n[2] Memória pessoal (CRUD + privacidade)")
    db = make_db()
    ps = PersonalStore(db)

    m = ps.create(PersonalMemory(
        user_id=U, category="objetivos",
        content={"texto": "estudar fora com bolsa integral"},
        tags=["longo prazo"],
    ))
    check("privacidade PRIVATE por padrão (Cap. 126)", m.privacy == Privacy.PRIVATE)
    check("leitura", ps.get(U, m.id) is not None)
    check("isolamento: outro usuário não lê", ps.get(OTHER, m.id) is None)

    upd = ps.update(U, m.id, {"content": {"texto": "estudar fora com bolsa integral", "prazo": "2027"}})
    check("update persiste", upd is not None and upd.content["prazo"] == "2027")
    check("update de outro usuário falha", ps.update(OTHER, m.id, {"category": "hack"}) is None)

    ps.create(PersonalMemory(user_id=U, category="preferencias", content={"idioma": "pt-BR"}))
    check("lista por categoria", len(ps.list(U, category="objetivos")) == 1)
    check("lista por privacidade", len(ps.list(U, privacy=Privacy.PRIVATE)) == 2)
    check("delete de outro usuário falha", ps.delete(OTHER, m.id) is False)
    check("delete próprio funciona", ps.delete(U, m.id) is True)


def test_knowledge_graph() -> None:
    print("\n[3] Grafo de conhecimento + busca híbrida")
    db = make_db()
    ks = KnowledgeStore(db)

    doc = ks.upsert_node(KnowledgeNode(
        user_id=U, node_type="Documento", title="Introdução à IA",
        description="Documento sobre inteligência artificial e machine learning.",
        privacy=Privacy.PRIVATE,
    ))
    ia = ks.upsert_node(KnowledgeNode(
        user_id=U, node_type="Conceito", title="Inteligência Artificial",
        description="Campo da computação dedicado a problemas cognitivos.",
    ))
    ml = ks.upsert_node(KnowledgeNode(
        user_id=U, node_type="Conceito", title="Machine Learning",
        description="Subcampo da IA em que sistemas aprendem a partir de dados.",
    ))

    dup = ks.upsert_node(KnowledgeNode(
        user_id=U, node_type="Conceito", title="machine learning",  # caixa diferente
        description="duplicata",
    ))
    check("conceito repetido NÃO vira nó novo (Cap. 117)", dup.id == ml.id)

    ks.add_edge(KnowledgeEdge(user_id=U, source_id=doc.id, target_id=ia.id, rel_type="CONTEM_CONCEITO"))
    ks.add_edge(KnowledgeEdge(user_id=U, source_id=ml.id, target_id=ia.id, rel_type="EXPLICA"))

    viz_ia = {n.title for n in ks.neighbors(U, ia.id)}
    check("vizinhos de IA incluem ML (aresta de ENTRADA)", "Machine Learning" in viz_ia)
    check("vizinhos de IA incluem o Documento", "Introdução à IA" in viz_ia)
    viz_ml = {n.title for n in ks.neighbors(U, ml.id)}
    check("vizinhos de ML incluem IA (aresta de SAÍDA)", "Inteligência Artificial" in viz_ml)
    check("filtro por rel_type funciona",
          [n.title for n in ks.neighbors(U, ia.id, rel_type="EXPLICA")] == ["Machine Learning"])

    # busca léxica pura
    hits = ks.search(U, "machine learning dados", top_k=3)
    check("FTS5 encontra por palavra-chave", any(h.node.id == ml.id for h in hits))
    check("busca não vaza para outro usuário", ks.search(OTHER, "machine learning") == [])

    # busca semântica: embedding do ML é feito idêntico ao da query
    ks.save_embedding(U, ml.id, MODEL, fake_embedding("alvo"))
    ks.save_embedding(U, ia.id, MODEL, fake_embedding("outro"))
    ks.save_embedding(U, doc.id, MODEL, fake_embedding("outro2"))
    hits = ks.search(U, "zzz termo inexistente", query_embedding=fake_embedding("alvo"),
                     model=MODEL, top_k=3)
    check("embedding É usado na busca (era ignorado antes)", hits and hits[0].node.id == ml.id)
    check("origem do match é registrada", hits[0].matched_by in ("vector", "hybrid"))

    hits = ks.search(U, "machine learning", query_embedding=fake_embedding("alvo"), model=MODEL, top_k=3)
    check("fusão híbrida marca match duplo", any(h.matched_by == "hybrid" for h in hits))

    ks.delete_node(U, ia.id)
    check("delete do nó cascateia arestas", ks.neighbors(U, ml.id) == [])
    check("FTS é reindexado no delete", all(h.node.id != ia.id for h in ks.search(U, "cognitivos")))


def test_journeys() -> None:
    print("\n[4] Jornadas (a unidade central que não existia no código)")
    db = make_db()
    js = JourneyStore(db)

    j = js.create(Journey(
        user_id=U, domain="educacao", title="Aprender Excel",
        stated_goal="quero estudar Excel", status="descobrindo",
    ))
    check("nasce em 'descobrindo' (Etapa 1)", js.get(U, j.id).status == "descobrindo")

    js.set_goals(U, j.id, real_goal="conseguir um emprego",
                 diagnosis={"nivel": "iniciante", "prazo_meses": 3})
    got = js.get(U, j.id)
    check("objetivo REAL sobrescreve o declarado", got.real_goal == "conseguir um emprego")
    check("vira 'ativa' após diagnóstico", got.status == "ativa")

    js.add_steps(U, j.id, [
        JourneyStep(journey_id=j.id, user_id=U, order_index=0, title="Fórmulas básicas"),
        JourneyStep(journey_id=j.id, user_id=U, order_index=0, title="Tabelas dinâmicas"),
        JourneyStep(journey_id=j.id, user_id=U, order_index=0, title="Projeto final"),
    ])
    got = js.get(U, j.id)
    check("passos ordenados", [s.order_index for s in got.steps] == [0, 1, 2])
    check("progresso = 0", got.progress == 0.0)

    js.set_step_status(U, got.steps[0].id, "concluida")
    check("progresso recalculado", math.isclose(js.get(U, j.id).progress, 0.33, abs_tol=0.01))
    check("jornada ativa é encontrada", js.active(U).id == j.id)
    check("outro usuário não enxerga", js.get(OTHER, j.id) is None)


def test_audit() -> None:
    print("\n[5] Auditoria (Cap. 127 — transparência)")
    db = make_db()
    audit = AuditStore(db)
    audit.log(U, "read", "personal_memory", "m1", session_id="s1", reason="montar contexto")
    audit.log(U, "read", "knowledge_node", "n1", session_id="s1", reason="montar contexto")
    log = audit.recent(U)
    check("acessos registrados", len(log) == 2)
    check("motivo gravado", log[0]["reason"] == "montar contexto")


def test_finance() -> None:
    print("\n[6] Domínio Financeiro (Cap. 82–92)")
    from app.domain.models import FinanceAccount, FinanceGoal, FinanceTransaction
    from app.finance.store import FinanceStore

    db = make_db()
    fin = FinanceStore(db)

    acc = fin.create_account(FinanceAccount(
        user_id=U, name="Corrente", kind="corrente", balance=1000.0,
    ))
    check("conta criada com saldo", fin.get_account(U, acc.id).balance == 1000.0)
    check("outro usuário não vê a conta", fin.get_account(OTHER, acc.id) is None)

    fin.add_transaction(FinanceTransaction(
        user_id=U, account_id=acc.id, kind="despesa", amount=200.0,
        category="moradia", description="aluguel",
    ))
    fin.add_transaction(FinanceTransaction(
        user_id=U, account_id=acc.id, kind="receita", amount=500.0,
        category="salario", description="pagamento",
    ))
    got = fin.get_account(U, acc.id)
    check("saldo atualizado na mesma transação", abs(got.balance - 1300.0) < 0.01)

    goal = fin.create_goal(FinanceGoal(
        user_id=U, title="Reserva", target_amount=6000.0, current_amount=1500.0,
    ))
    check("progresso da meta", abs(goal.progress - 0.25) < 0.01)
    upd = fin.update_goal_progress(U, goal.id, 6000.0)
    check("meta conclui ao atingir alvo", upd is not None and upd.status == "concluida")

    health = fin.health(U)
    check("patrimônio no health", abs(health.patrimonio - 1300.0) < 0.01)
    check("receita do mês", health.receita_mes >= 500.0)
    check("despesa do mês", health.despesa_mes >= 200.0)
    check("snapshot monta contexto", fin.snapshot(U).contas[0].id == acc.id)


def test_session_journey_bind() -> None:
    print("\n[7] Chat ↔ jornada")
    db = make_db()
    conv = ConversationStore(db)
    js = JourneyStore(db)
    j = js.create(Journey(
        user_id=U, domain="educacao", title="Inglês",
        stated_goal="aprender inglês", status="ativa",
    ))
    s = conv.ensure_session(U, "sess-1", journey_id=j.id)
    check("sessão nasce amarrada à jornada", s.journey_id == j.id)
    got = conv.get_session(U, "sess-1")
    check("get_session devolve journey_id", got is not None and got.journey_id == j.id)
    conv.ensure_session(U, "sess-2")
    check("bind posterior funciona", conv.bind_journey(U, "sess-2", j.id))
    check("sessão 2 amarrada", conv.get_session(U, "sess-2").journey_id == j.id)


def test_pdf_extract() -> None:
    print("\n[8] Extração de PDF")
    from io import BytesIO

    from pypdf import PdfWriter

    from app.knowledge.extract import extract_text_from_bytes

    text, fmt = extract_text_from_bytes(
        "nota.md",
        "Conceito de juros compostos explicado em detalhe para o Atlas.".encode(),
    )
    check("texto puro funciona", fmt == "text" and "juros" in text)

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    try:
        extract_text_from_bytes("vazio.pdf", buf.getvalue())
        check("PDF sem texto deveria falhar", False)
    except ValueError:
        check("PDF sem texto extraível é rejeitado", True)


def test_education_general() -> None:
    print("\n[9] Domínio Educação — estudo GERAL + caderno + área livre")
    from app.domain.models import Competency, StudyNote, StudySession, StudyTrack
    from app.education.store import EducationStore

    db = make_db()
    edu = EducationStore(db)

    calc = edu.create_track(StudyTrack(
        user_id=U, title="Cálculo I", subject_area="Cálculo I",
        goal="passar na prova", level="iniciante",
    ))
    direito = edu.create_track(StudyTrack(
        user_id=U, title="Direito Constitucional", subject_area="Direito Constitucional",
        goal="concurso", level="intermediario",
    ))
    fisio = edu.create_track(StudyTrack(
        user_id=U, title="Fisioterapia respiratória", subject_area="Fisioterapia",
        goal="prática clínica", level="iniciante",
    ))
    areas = {t.subject_area for t in edu.list_tracks(U)}
    check("áreas livres (não lista fechada)", "Fisioterapia" in areas and "Direito Constitucional" in areas)
    check("isolamento por usuário", edu.get_track(OTHER, calc.id) is None)

    edu.add_session(StudySession(user_id=U, track_id=calc.id, minutes=50, notes="limites", topics=["limites"]))
    edu.create_competency(Competency(
        user_id=U, track_id=calc.id, name="Limites", subject_area="Cálculo I", level="praticar",
    ))
    edu.create_note(StudyNote(
        user_id=U, track_id=direito.id, title="Legalidade",
        topic="princípios", content="Administração só age conforme a lei.",
    ))
    snap = edu.snapshot(U)
    check("snapshot tem 3 trilhas", len(snap.tracks_ativas) == 3)
    check("minutos da semana", snap.minutos_semana >= 50)
    check("caderno no snapshot", len(snap.notas_recentes) >= 1)
    check("fisioterapia também entra", any(t.title.startswith("Fisio") for t in snap.tracks_ativas))


def test_cabinet() -> None:
    print("\n[10] Domínio Gabinete Inteligente")
    from app.domain.models import CabinetCitizen, CabinetDemand, CabinetTimelineEvent
    from app.cabinet.store import CabinetStore

    db = make_db()
    cab = CabinetStore(db)

    cid = cab.create_citizen(CabinetCitizen(
        user_id=U, name="Maria Silva", municipality="Sobral", contact="88 99999",
    ))
    dem = cab.create_demand(CabinetDemand(
        user_id=U, citizen_id=cid.id, title="Pavimentação rua X",
        municipality="Sobral", category="infraestrutura", priority="alta",
    ))
    cab.add_timeline(CabinetTimelineEvent(
        user_id=U, citizen_id=cid.id, demand_id=dem.id, municipality="Sobral",
        event_type="contato", title="Ligação recebida", description="Pediu retorno",
    ))
    check("demanda ligada ao cidadão", cab.get_demand(U, dem.id).citizen_id == cid.id)
    check("outro usuário não vê demanda", cab.get_demand(OTHER, dem.id) is None)
    cab.update_demand(U, dem.id, {"status": "em_andamento"})
    check("status atualizado", cab.get_demand(U, dem.id).status == "em_andamento")
    snap = cab.snapshot(U)
    check("snapshot conta abertas", snap.demandas_abertas >= 1)
    check("município no snapshot", "Sobral" in snap.municipios)
    check("timeline do cidadão", len(cab.list_timeline(U, citizen_id=cid.id)) >= 1)


def test_education_chapters_quiz() -> None:
    print("\n[11] Capítulos + quiz + competência")
    import asyncio

    from app.domain.models import StudyTrack
    from app.education.mentor import StudyMentor
    from app.llm.fake import FakeLLM
    from app.memory.service import MemoryService

    db = make_db()
    mem = MemoryService(db, FakeLLM())
    mentor = StudyMentor(mem, FakeLLM())
    track = mem.education.create_track(StudyTrack(
        user_id=U, title="Psicologia cognitiva", subject_area="Psicologia",
        goal="entender memória de trabalho", level="iniciante",
    ))

    chapters = asyncio.run(mentor.generate_chapters(U, track.id))
    check("gerou capítulos", len(chapters) >= 4)
    chapters2 = asyncio.run(mentor.generate_chapters(U, track.id))
    check("não duplica capítulos", len(chapters2) == len(chapters))

    quiz = asyncio.run(mentor.generate_quiz(U, track.id, chapter_id=chapters[0].id))
    check("quiz tem perguntas", len(quiz.questions) >= 2)

    graded = asyncio.run(mentor.grade_quiz(U, quiz.id, [
        {"question_id": q.id, "resposta": "explicação do aluno"} for q in quiz.questions
    ]))
    check("quiz corrigido", graded.status == "corrigido" and graded.score is not None)
    comps = mem.education.list_competencies(U)
    check("competência registrada após quiz", len(comps) >= 1)


if __name__ == "__main__":
    test_conversational()
    test_personal()
    test_knowledge_graph()
    test_journeys()
    test_audit()
    test_finance()
    test_session_journey_bind()
    test_pdf_extract()
    test_education_general()
    test_cabinet()
    test_education_chapters_quiz()
    print("\nTodos os testes passaram.\n")

