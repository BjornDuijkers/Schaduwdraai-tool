from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class TextWord:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str
    method: str = "text"
    words: list[TextWord] = field(default_factory=list)


@dataclass
class ExtractionResult:
    source_name: str
    pages: list[PageText]
    warnings: list[str] = field(default_factory=list)
    text_layer_present: bool = True


@dataclass
class Component:
    code: str | None
    label: str
    normalized_label: str
    amount: Decimal
    raw_line: str


@dataclass
class Payslip:
    source: str
    source_name: str
    employee_name: str | None
    birth_date: str | None
    period: str | None
    page_numbers: list[int]
    components: list[Component]
    warnings: list[str] = field(default_factory=list)
    raw_text: str = ""

    @property
    def identity_complete(self) -> bool:
        return bool(self.employee_name and self.birth_date)


@dataclass
class ParsedDocument:
    source: str
    source_name: str
    payslips: list[Payslip]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ComponentMapping:
    source_a: str
    source_b: str
    canonical: str


@dataclass
class EmployeeComparison:
    status: str
    employee_name: str | None
    birth_date: str | None
    source_a_pages: str
    source_b_pages: str
    match_note: str = ""


@dataclass
class ComponentComparison:
    employee_name: str | None
    birth_date: str | None
    canonical_component: str
    component_a: str | None
    amount_a: Decimal | None
    component_b: str | None
    amount_b: Decimal | None
    difference: Decimal | None
    status: str
    pages_a: str
    pages_b: str


@dataclass
class ComparisonResult:
    employees: list[EmployeeComparison]
    components: list[ComponentComparison]
    warnings: list[str]
    summary: dict[str, int | str]
