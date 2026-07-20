"""Pydantic models — the extraction contract.

Every Claude call in the system is validated against one of these models
(via app.llm.extract). Anything a customer could audit — Spec Match %,
prices, totals — is NOT produced by the LLM; those live in the deterministic
result types at the bottom and are computed by plain Python.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# LLM output contracts (schema-enforced structured output)
# ---------------------------------------------------------------------------

class EmailClassification(BaseModel):
    """Is this email a tender/RFP relevant to the configured product categories?"""
    is_tender: bool
    relevant_to_categories: bool = Field(description="True only if it concerns the product categories provided in the prompt")
    title: str = ""
    issuer: str = ""
    reference_no: str = ""
    due_date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD if stated, else null")
    confidence: float = Field(ge=0, le=1)


class PortalListing(BaseModel):
    title: str
    url: str = ""
    issuer: str = ""
    reference_no: str = ""
    due_date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD if stated, else null")


class PortalListings(BaseModel):
    listings: list[PortalListing]


class SpecParam(BaseModel):
    """One normalized requirement parameter extracted from an RFP line item."""
    name: str = Field(description="Canonical parameter name, snake_case, e.g. voltage_grade, conductor_material, core_count, cross_section_sqmm, insulation_type, armouring, standard")
    kind: Literal["numeric_exact", "numeric_min", "categorical"]
    value: str = Field(description="The requirement as stated, e.g. '11 kV', 'Aluminium', '3'")
    numeric_value: Optional[float] = Field(default=None, description="Parsed numeric value in the unit given, for numeric kinds")
    unit: str = Field(default="", description="Unit of numeric_value, e.g. kV, sqmm, deg_c; empty for categorical")


class RFPLineItem(BaseModel):
    item_no: str
    description: str
    quantity: float = Field(description="Quantity in the stated unit; 0 if unstated")
    unit: str = Field(default="m", description="e.g. m, km, nos")
    specs: list[SpecParam]


class RFPTest(BaseModel):
    name: str
    standard: str = ""
    description: str = ""


class RFPDataset(BaseModel):
    """Normalized RFP extracted from tender documents."""
    title: str = ""
    issuer: str = ""
    reference_no: str = ""
    due_date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD")
    line_items: list[RFPLineItem]
    tests: list[RFPTest]
    special_conditions: list[str] = Field(default_factory=list)


class SpecKV(BaseModel):
    name: str = Field(description="Canonical spec name, snake_case (voltage_grade, conductor_material, core_count, cross_section_sqmm, insulation_type, armouring, standard, cable_type, ...)")
    value: str


class DatasheetSKU(BaseModel):
    name: str
    category: str = ""
    specs: list[SpecKV]
    unit_price: Optional[float] = Field(default=None, description="Unit price if stated on the datasheet, else null")
    price_unit: str = Field(default="m")


class DatasheetSKUs(BaseModel):
    """SKUs extracted from an unstructured (PDF/DOCX) product datasheet."""
    skus: list[DatasheetSKU]


class RoleSummaries(BaseModel):
    """Orchestrator's two role-contextual summaries."""
    product_summary: str = Field(description="Summary of product requirements for the Technical Agent")
    test_summary: str = Field(description="Summary of testing/acceptance requirements for the Pricing Agent")


class VerifierItemExam(BaseModel):
    """Independent cross-examination of one technical pick (no access to Technical Agent reasoning)."""
    item_no: str
    agrees_with_pick: bool
    concerns: list[str] = Field(default_factory=list, description="Specific spec-level concerns, each citing parameter names")
    unfulfillable: bool = Field(description="True if no catalog SKU can plausibly satisfy this item")


class VerifierExam(BaseModel):
    items: list[VerifierItemExam]
    overall_comment: str = ""


class MTODraft(BaseModel):
    subject: str
    body: str


class ReplyClassification(BaseModel):
    references_bid: bool
    intent: Literal["award", "rejection", "clarification_request", "acknowledgement", "other"]
    summary: str
    ambiguous: bool
    confidence: float = Field(ge=0, le=1)


class FollowupDraft(BaseModel):
    subject: str
    body: str


class DeviationStatement(BaseModel):
    """Clause-level prose for deviations; facts are supplied, LLM only words them."""
    clauses: list[str]


# ---------------------------------------------------------------------------
# Deterministic result types (computed by plain Python — never by an LLM)
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    param: str
    kind: str
    required: str
    actual: Optional[str]
    score: float


class MatchResult(BaseModel):
    sku_id: str = ""
    pct: float
    evidence: list[Evidence]


class TechItem(BaseModel):
    item_no: str
    description: str
    quantity: float
    unit: str
    top3: list[MatchResult]
    top_pick: Optional[str]  # sku_id, None when nothing matches at all
    below_threshold: bool


class TechTable(BaseModel):
    items: list[TechItem]


class PriceLine(BaseModel):
    item_no: str
    sku_id: str
    description: str
    quantity: float
    unit: str
    unit_price: float
    currency: str
    amount: float
    priced: bool  # False = missing price table entry → escalation, never a guess


class TestPriceLine(BaseModel):
    test_name: str
    standard: str
    price: float
    currency: str
    priced: bool


class PriceTable(BaseModel):
    lines: list[PriceLine]
    test_lines: list[TestPriceLine]
    material_total: float
    test_total: float
    grand_total: float
    currency: str = "INR"


class VerdictItem(BaseModel):
    item_no: str
    status: Literal["verified", "flagged"]
    reasons: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    per_item: list[VerdictItem]
    overall: Literal["proceed", "proceed_with_deviations", "recommend_no_bid"]
    evidence: list[str] = Field(default_factory=list)


class MTORequest(BaseModel):
    item_no: str
    closest_sku: str
    gaps: list[Evidence]
    draft_subject: str
    draft_body: str


class DraftResponse(BaseModel):
    """What the orchestrator posts to the human checkpoint."""
    sku_table: TechTable
    price_table: PriceTable
    mto_requests: list[MTORequest]
    verifier_verdict: Verdict
    run_log: list[str]
