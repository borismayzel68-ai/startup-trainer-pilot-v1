"""Data contracts between pipeline stages. LLM outputs are validated against these."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_serializer


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _OmitEmpty(BaseModel):
    """HQ-016: новые поля сериализуются, только если заполнены — вывод не-LS кейсов
    остаётся побайтно прежним (golden, T3)."""
    _omit_if_empty: ClassVar[tuple[str, ...]] = ()

    @model_serializer(mode="wrap")
    def _drop_empty(self, handler):
        d = handler(self)
        for k in self._omit_if_empty:
            if k in d and not d[k]:
                d.pop(k)
        return d


# ---------------------------------------------------------------- intake ----
class Answer(BaseModel):
    field: str
    block: str
    label: str
    value: str


FileKind = Literal["pitch_deck", "fin_model", "evidence"]


class FileRef(BaseModel):
    kind: FileKind
    url: str


class Submission(_OmitEmpty):
    _omit_if_empty: ClassVar[tuple[str, ...]] = ("ls_stage",)
    submission_id: str               # Tilda tranid (идемпотентность)
    received_at: datetime = Field(default_factory=utcnow)
    project_id: str | None = None    # VC-ID, если уже присвоен фондом
    project_name: str = ""
    founder_name: str = ""
    founder_email: str = ""
    consent: bool = False
    # маршрутизация — те же поля и словари, что у тренажёра (routing_v2)
    jurisdiction: str = ""
    target_markets: str = ""
    product_class: str = ""
    intended_use: str = ""
    ls_stage: str = ""               # HQ-016: стадия LS-актива (поле формы ls_stage), иначе пусто
    answers: list[Answer] = []
    files: list[FileRef] = []


# --------------------------------------------------------------- parsing ----
SourceKind = Literal["form", "deck", "model", "evidence"]


class ParsedDoc(BaseModel):
    kind: FileKind
    source: SourceKind
    name: str
    url: str
    ok: bool = True
    text: str = ""                    # что уйдёт в LLM (усечено)
    pages: int | None = None
    metrics: dict[str, list] = {}     # только для xlsx: канонические метрики
    formula_cells: int | None = None  # только для xlsx: сколько формул в просмотренной области
    warnings: list[str] = []


# ------------------------------------------------------------ evaluation ----
class Category(str, Enum):
    FACT = "FACT"                      # HQ-035: только после подписи человека (verified_by), канон Р-5
    FACT_CANDIDATE = "FACT_CANDIDATE"  # код: документ ≥ fact_min_level без подписи — «заявление с документом»
    FOUNDER_CLAIM = "FOUNDER_CLAIM"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class Claim(BaseModel):
    id: str
    slot: str | None = None             # 'module.slot' из чек-листа (канон SLOT_CATALOG) или null
    block: str = ""                     # блок Deal Genome; для слота — вычисляет код
    statement: str
    sources: list[SourceKind] = []
    source_ref: str = ""                # «слайд 7», «лист P&L, строка Revenue», «анкета q_traction»
    category: Category
    evidence_level: int = Field(ge=0, le=4)
    evidence_basis: str = ""
    conflicts_with: list[str] = []
    verification_step: str = ""
    machine_adjusted: list[str] = []    # что поменяли детерминированные правила (аудит-след)
    verified_by: str | None = None      # HQ-035: роль подписанта факта (ставит только код по журналу подписей)

    @field_validator("sources", mode="before")
    @classmethod
    def _norm_sources(cls, v):
        allowed = {"form", "deck", "model", "evidence"}
        return [s for s in (v or []) if s in allowed]


class ThesisFit(BaseModel):
    fit: bool
    reason: str


class ClassificationResult(BaseModel):
    company_summary: str
    sector: str
    stage: str
    thesis_fit: ThesisFit
    claims: list[Claim]


class Argument(BaseModel):
    point: str
    claim_ids: list[str] = []
    severity: Literal["low", "medium", "high"] | None = None   # только для Анны
    unsupported: bool = False                                 # ставит код, если ссылки невалидны


class BullCase(BaseModel):
    thesis: str
    arguments: list[Argument]
    what_must_be_true: list[str]


class BearCase(BaseModel):
    thesis: str
    arguments: list[Argument]
    kill_questions: list[str]


class Barrier(BaseModel):
    barrier: str
    why_it_matters: str
    block: str


class ProofPoint(BaseModel):
    proof_point: str
    block: str
    claim_ids: list[str] = []
    current_level: int = Field(ge=0, le=4)
    target_level: int = Field(ge=0, le=4)
    how_to_obtain: str
    valuation_impact: str


class Synthesis(BaseModel):
    executive_summary: str
    critical_barriers: list[Barrier]
    proof_points: list[ProofPoint]
    plan_30: list[str]
    plan_60: list[str]
    plan_90: list[str]
    founder_objections_to_preempt: list[str]   # безопасная для фаундера версия аргументов Анны
    verdict_rationale: str
    revaluation_condition: str            # ключевое условие переоценки (канон Internal Brief)


# ---------------------------------------------------------------- scores ----
SlotState = Literal["SCORED", "N_A_BY_STAGE", "MISSING"]


class ItemScore(_OmitEmpty):
    _omit_if_empty: ClassVar[tuple[str, ...]] = ("applicability", "na_reason")
    key: str
    label: str
    critical: bool
    best_level: int | None          # None = пробел
    status: Category
    claim_ids: list[str] = []
    applicability: SlotState | None = None   # HQ-016: только когда применяется LS-правило
    na_reason: str | None = None


class BlockScore(BaseModel):
    key: str
    label: str
    weight: float
    score: float                    # 0..1
    items: list[ItemScore]


Verdict = Literal["PASS", "HOLD", "NEXT"]


class Scores(_OmitEmpty):
    _omit_if_empty: ClassVar[tuple[str, ...]] = ("ls",)
    readiness: int                  # 0..100
    blocks: list[BlockScore]
    critical_gaps: list[str]
    critical_conflicts: list[str]
    verdict: Verdict
    verdict_gates: list[str]        # почему именно такой вердикт (детерминированно)
    ls: dict | None = None          # HQ-016: сводка LS-правила (знаменатель, N/A, К1–К6, барьеры, К7)


class EvaluationResult(BaseModel):
    job_id: str
    submission: Submission
    routing: dict                       # результат routing_v2: модули, юрисдикция, класс продукта
    docs: list[ParsedDoc]
    classification: ClassificationResult
    scores: Scores
    bull: BullCase
    bear: BearCase
    synthesis: Synthesis
    model: str
    methodology_version: str
    created_at: datetime = Field(default_factory=utcnow)
