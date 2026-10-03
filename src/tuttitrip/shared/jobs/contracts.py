"""Mirror of the worker contract. Canonical: tuttitrip-worker ``contracts.py``.

Both repositories render the same document with :func:`contract_json` and
commit it as ``contracts/jobs.schema.json``. CI compares the two files.
Rules (deploy/CONVENTIONS.md, "Integracja z workerem"):

* every payload carries ``contract_version``;
* payloads are small (ids and parameters), sent as portable JSON;
* change order: worker accepts old+new, backend switches, worker drops old.
"""

import json
import re
import unicodedata
from enum import StrEnum
from typing import Final, Literal, NamedTuple, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = 1
WORKER_APPLICATION_NAME = "tuttitrip-worker"
PROGRESS_EVENT = "progress"


class Queue(StrEnum):
    """DBOS queues served by the worker."""

    DEFAULT = "default"
    LOCAL_LLM = "local_llm"
    OPENROUTER = "openrouter"


class Workflow(StrEnum):
    """Registered DBOS workflow names in the worker."""

    GENERATE_TRIP_PLAN = "generate_trip_plan"
    EMBED_TEXTS = "embed_texts"
    PING = "ping"
    PARSE_PASTED_PLAN = "parse_pasted_plan"
    EXTRACT_OFFER_EVIDENCE = "extract_offer_evidence"
    FETCH_PLACE_CANDIDATES = "fetch_place_candidates"
    WRITE_JUSTIFICATIONS = "write_justifications"


ProviderName = Literal["openrouter", "local"]


class ErrorCode(StrEnum):
    """Machine-readable ``code`` of the worker's ``ContractError``."""

    UNSUPPORTED_CONTRACT_VERSION = "unsupported_contract_version"
    INVALID_PAYLOAD = "invalid_payload"
    NOT_IMPLEMENTED = "not_implemented"


class ContractPayload(BaseModel):
    """Base for every input and output: carries the contract version."""

    contract_version: int = CONTRACT_VERSION


class GenerateTripPlanInput(ContractPayload):
    """Input of ``generate_trip_plan``."""

    trip_id: UUID
    request: str = Field(min_length=1, max_length=4000)
    provider: ProviderName = "openrouter"


class GenerateTripPlanOutput(ContractPayload):
    """Output of ``generate_trip_plan`` (the full plan goes to ``job_results``)."""

    destination: str
    days: int
    highlights: list[str]


class EmbedTextsInput(ContractPayload):
    """Input of ``embed_texts``: embed short texts of one source row."""

    source_kind: str = Field(min_length=1, max_length=50)
    source_id: str = Field(min_length=1, max_length=200)
    texts: list[str] = Field(min_length=1, max_length=64)


class EmbedTextsOutput(ContractPayload):
    """Output of ``embed_texts`` (vectors are in the ``embeddings`` table)."""

    model: str
    dimensions: int
    stored: int


class PingInput(ContractPayload):
    """Input of ``ping``: an echo used by post-deploy smoke tests (no LLM)."""

    message: str = Field(max_length=200)


class PingOutput(ContractPayload):
    """Output of ``ping``."""

    message: str
    worker_app_version: str


# --- shared helpers -----------------------------------------------------------------

SLUG_PATTERN: Final = r"^[a-z0-9]+(-[a-z0-9]+)*$"
"""City slug: lowercase ASCII words joined by ``-``. Built from a free-text city
name by lowercasing, removing diacritics (``ł`` becomes ``l``), turning every
run of other characters into one ``-`` and trimming ``-`` at both ends
(``"Gdańsk, Polska"`` becomes ``"gdansk-polska"``). Backend and worker use this
one rule."""

Locale = Literal["pl", "en"]

_NON_ASCII_LETTERS = str.maketrans({"ł": "l", "Ł": "L", "đ": "d", "Đ": "D"})


def city_slug(name: str) -> str:
    """Build a city slug the way the worker does (rule in ``SLUG_PATTERN``).

    Args:
        name: Free-text city name, e.g. ``"Gdańsk, Polska"``.

    Returns:
        Lowercase ASCII words joined by ``-`` (``"gdansk-polska"``); empty when
        the name has no letters or digits.
    """
    ascii_name = (
        unicodedata.normalize("NFKD", name.translate(_NON_ASCII_LETTERS))
        .encode("ascii", "ignore")
        .decode()
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


# --- parse_pasted_plan -------------------------------------------------------------

TIME_PATTERN: Final = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
"""24-hour ``HH:MM``, zero padded."""

TransportMode = Literal["walk", "public_transport", "car", "taxi", "bike", "other"]


def _normalize_time(value: object) -> object:
    """Zero-pad ``H:MM`` to ``HH:MM`` (``9:00`` becomes ``09:00``).

    Args:
        value: Raw value; anything but a string is returned unchanged.

    Returns:
        The padded time, or the input for the pattern check to judge.
    """
    if isinstance(value, str) and re.fullmatch(r"[0-9]:[0-5][0-9]", value.strip()):
        return f"0{value.strip()}"
    return value


class ParsedPlanItem(BaseModel):
    """One item read from a pasted plan; ``quote`` is verbatim from the text.

    Times and amounts are claims of the checked plan, not catalog data.
    ``start_time`` and ``end_time`` are ``HH:MM``; ``9:00`` is normalized to
    ``09:00``. ``amount_minor`` is in minor units (grosze, cents) and is a
    price **per person** (the total for a group is the linter's business);
    when the text gives only a group total, the parser leaves it ``None``.
    ``day`` is ``None`` when the text does not say which day (the item stays
    in text order, see ``index``).
    """

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    day: int | None = Field(default=None, ge=1, le=60)
    start_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    end_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    place_name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=100)
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    transport: TransportMode | None = None
    quote: str = Field(min_length=1, max_length=1000)

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _pad_time(cls, value: object) -> object:
        return _normalize_time(value)


class MatchCandidate(BaseModel):
    """A catalog place proposed for a pasted item (pure code ranks them)."""

    model_config = ConfigDict(frozen=True)

    place_id: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=200)
    address: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=100)
    score: float = Field(ge=0, le=1)


class PlaceMatch(BaseModel):
    """Catalog match of one parsed item.

    ``status``: ``matched`` (``place_id`` set, confident), ``needs_confirmation``
    (low confidence, the host picks from ``candidates``) or ``unrecognized``
    (``place_id`` is ``None``). Filled by the matching step
    (``tuttitrip-worker#24``); until then ``ParsePastedPlanOutput.matches`` is
    empty.
    """

    model_config = ConfigDict(frozen=True)

    item_index: int = Field(ge=0)
    status: Literal["matched", "needs_confirmation", "unrecognized"]
    place_id: str | None = Field(default=None, max_length=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    candidates: list[MatchCandidate] = Field(default_factory=list, max_length=9)


class UnreadItem(BaseModel):
    """Text the parser could not turn into a valid item (no verbatim quote)."""

    model_config = ConfigDict(frozen=True)

    quote: str = Field(max_length=1000)
    reason: Literal["quote_not_in_text", "invalid_item"]


class ParsePastedPlanInput(ContractPayload):
    """Input of ``parse_pasted_plan``; the text is read from ``pasted_documents``."""

    trip_id: UUID
    document_id: UUID
    city_slug: str = Field(pattern=SLUG_PATTERN, max_length=100)
    provider: ProviderName = "openrouter"


class ParsePastedPlanOutput(ContractPayload):
    """Output of ``parse_pasted_plan`` (also kept in ``job_results``)."""

    items: list[ParsedPlanItem] = Field(max_length=300)
    unread: list[UnreadItem] = Field(default_factory=list, max_length=300)
    matches: list[PlaceMatch] = Field(default_factory=list, max_length=300)


# --- extract_offer_evidence ----------------------------------------------------------

OfferVerdict = Literal["present", "absent", "not_applicable"]
"""What a quote says about a requirement. No quote means unconfirmed, which is
decided by the backend, never by the worker."""


class EvidenceQuote(BaseModel):
    """One verbatim quote of the offer and what it says about the requirement."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=1000)
    verdict: OfferVerdict | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class RequirementEvidence(BaseModel):
    """Quotes of the offer for one requirement, each with its own assessment."""

    model_config = ConfigDict(frozen=True)

    requirement_key: str = Field(min_length=1, max_length=100)
    quotes: list[EvidenceQuote] = Field(default_factory=list, max_length=10)


class RequirementLabel(BaseModel):
    """A requirement key with the human label the model reads (backend#67)."""

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)


class ExtractOfferEvidenceInput(ContractPayload):
    """Input of ``extract_offer_evidence``; the offer is in ``pasted_documents``.

    ``requirement_keys`` must be unique. ``requirements`` optionally gives a
    label for keys (each must also be in ``requirement_keys``).
    """

    trip_id: UUID
    document_id: UUID
    requirement_keys: list[str] = Field(min_length=1, max_length=50)
    requirements: list[RequirementLabel] | None = Field(default=None, max_length=50)
    provider: ProviderName = "openrouter"

    @model_validator(mode="after")
    def _unique_known_keys(self) -> Self:
        keys = self.requirement_keys
        if len(set(keys)) != len(keys):
            msg = "requirement_keys must be unique"
            raise ValueError(msg)
        labelled = [item.key for item in self.requirements or []]
        if len(set(labelled)) != len(labelled) or not set(labelled) <= set(keys):
            msg = "requirements need unique keys that are in requirement_keys"
            raise ValueError(msg)
        return self


class ExtractOfferEvidenceOutput(ContractPayload):
    """Output of ``extract_offer_evidence``: one entry per requested key."""

    evidence: list[RequirementEvidence] = Field(max_length=50)


# --- fetch_place_candidates --------------------------------------------------------


class FetchPlaceCandidatesInput(ContractPayload):
    """Input of ``fetch_place_candidates``: open data (OSM) for one city.

    Exactly one of ``city_query`` (free text) and ``city_slug`` is required.
    With ``city_query`` the worker derives ``city_slug`` by the rule in
    :data:`SLUG_PATTERN` and returns it in the output.
    """

    city_query: str | None = Field(default=None, min_length=1, max_length=200)
    city_slug: str | None = Field(default=None, pattern=SLUG_PATTERN, max_length=100)

    @model_validator(mode="after")
    def _exactly_one_city(self) -> Self:
        if (self.city_query is None) == (self.city_slug is None):
            msg = "exactly one of city_query and city_slug is required"
            raise ValueError(msg)
        return self


class FetchPlaceCandidatesOutput(ContractPayload):
    """Output of ``fetch_place_candidates`` (rows go to the places catalog).

    ``refreshed`` is ``False`` when the city already had candidates and nothing
    was fetched (``stored`` is then 0).
    """

    city_slug: str = Field(pattern=SLUG_PATTERN)
    source: Literal["osm"] = "osm"
    refreshed: bool
    stored: int = Field(ge=0)


# --- write_justifications ------------------------------------------------------------


class Justification(BaseModel):
    """A short reason a place is in the plan, like the ``explain()`` card.

    ``place_id`` is the catalog place id (the same id as in the plan item).
    ``profile_id`` is the person the card is written for; ``None`` is the
    group-level justification. ``source`` tells whether a model wrote ``text``
    or the deterministic template did.
    """

    model_config = ConfigDict(frozen=True)

    place_id: str = Field(min_length=1, max_length=100)
    profile_id: UUID | None = None
    text: str = Field(min_length=1, max_length=600)
    source: Literal["model", "template"]


class WriteJustificationsInput(ContractPayload):
    """Input of ``write_justifications``; the plan is read by id."""

    plan_id: UUID
    locale: Locale = "pl"
    provider: ProviderName = "openrouter"


class WriteJustificationsOutput(ContractPayload):
    """Output of ``write_justifications`` (also kept in ``job_results``)."""

    justifications: list[Justification] = Field(max_length=600)


class Progress(BaseModel):
    """Value of the ``progress`` event."""

    stage: str
    percent: int = Field(ge=0, le=100)


class WorkflowSpec(NamedTuple):
    """Queue and payload models of one workflow."""

    queue: Queue
    input: type[ContractPayload]
    output: type[ContractPayload]


WORKFLOWS: dict[Workflow, WorkflowSpec] = {
    # Default queue; enqueue on queue_for(provider) (openrouter or local_llm).
    Workflow.GENERATE_TRIP_PLAN: WorkflowSpec(
        Queue.OPENROUTER, GenerateTripPlanInput, GenerateTripPlanOutput
    ),
    Workflow.EMBED_TEXTS: WorkflowSpec(
        Queue.DEFAULT, EmbedTextsInput, EmbedTextsOutput
    ),
    Workflow.PING: WorkflowSpec(Queue.DEFAULT, PingInput, PingOutput),
    # LLM workflows: enqueue on queue_for(provider), like generate_trip_plan.
    Workflow.PARSE_PASTED_PLAN: WorkflowSpec(
        Queue.OPENROUTER, ParsePastedPlanInput, ParsePastedPlanOutput
    ),
    Workflow.EXTRACT_OFFER_EVIDENCE: WorkflowSpec(
        Queue.OPENROUTER, ExtractOfferEvidenceInput, ExtractOfferEvidenceOutput
    ),
    Workflow.WRITE_JUSTIFICATIONS: WorkflowSpec(
        Queue.OPENROUTER, WriteJustificationsInput, WriteJustificationsOutput
    ),
    # Open data (OSM), no LLM.
    Workflow.FETCH_PLACE_CANDIDATES: WorkflowSpec(
        Queue.DEFAULT, FetchPlaceCandidatesInput, FetchPlaceCandidatesOutput
    ),
}

EVENTS: dict[str, type[BaseModel]] = {PROGRESS_EVENT: Progress}


def queue_for(provider: ProviderName) -> Queue:
    """Queue an LLM job must be enqueued on, given its provider.

    Args:
        provider: Model backend chosen for the job.

    Returns:
        ``local_llm`` for the local GPU model, ``openrouter`` otherwise.
    """
    return Queue.LOCAL_LLM if provider == "local" else Queue.OPENROUTER


type JSON = dict[str, JSON] | list[JSON] | str | int | float | bool | None


def _strip_docs(node: JSON) -> JSON:
    """Drop ``title``/``description`` so docstrings never cause drift.

    Args:
        node: Any JSON value.

    Returns:
        The same value without documentation keys.
    """
    if isinstance(node, dict):
        return {
            k: _strip_docs(v)
            for k, v in node.items()
            if k not in {"title", "description"}
        }
    if isinstance(node, list):
        return [_strip_docs(v) for v in node]
    return node


def _schema(model: type[BaseModel]) -> JSON:
    return _strip_docs(model.model_json_schema())


def _workflow_entry(spec: WorkflowSpec) -> dict[str, JSON]:
    return {
        "queue": spec.queue.value,
        "input": _schema(spec.input),
        "output": _schema(spec.output),
    }


def contract_document() -> dict[str, JSON]:
    """Build the language-neutral description of the contract.

    Returns:
        Versions, names and JSON Schemas of all payloads and events.
    """
    queues: list[JSON] = [q.value for q in sorted(Queue)]
    workflows: dict[str, JSON] = {
        name.value: _workflow_entry(spec) for name, spec in WORKFLOWS.items()
    }
    events: dict[str, JSON] = {name: _schema(model) for name, model in EVENTS.items()}
    return {
        "contract_version": CONTRACT_VERSION,
        "application_name": WORKER_APPLICATION_NAME,
        "queues": queues,
        "workflows": workflows,
        "events": events,
    }


def contract_json() -> str:
    """Render the contract exactly as committed in ``contracts/jobs.schema.json``.

    Returns:
        Pretty-printed JSON with sorted keys and a trailing newline.
    """
    return (
        json.dumps(contract_document(), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )
