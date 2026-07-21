from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from io import StringIO

from .models import (
    Component,
    ComponentComparison,
    ComponentMapping,
    ComparisonResult,
    EmployeeComparison,
    ParsedDocument,
    Payslip,
)
from .parser import normalize_period
from .text_utils import normalize_key


TOLERANCE = Decimal("0.01")
DEFAULT_COMPONENT_ALIASES = {
    "salaris uit uren gewerkt": "Salaris",
    "loon salaris": "Salaris",
    "periodieke uitbetaling vakantiegeld": "Vakantiegeld",
    "vak toesl per": "Vakantiegeld",
    "inhouding internet": "Inhouding internet",
    "inh internet": "Inhouding internet",
    "o u leasebudget": "O.U. leasebudget",
    "ou leasebudget": "O.U. leasebudget",
    "o u leasebudg": "O.U. leasebudget",
    "ou leasebudg": "O.U. leasebudget",
    "targetbonus": "Targetbonus",
    "brutoloon": "Brutoloon",
    "bruto": "Brutoloon",
    "bijtelling prive gebruik auto": "Fiscale bijtelling auto",
    "fisc byt auto": "Fiscale bijtelling auto",
    "bijtelling privegebruik fiets": "Fiscale bijtelling fiets",
    "fisc byt fiets": "Fiscale bijtelling fiets",
    "medewerkersbijdrage fiets": "Medewerkersbijdrage fiets",
    "wn bydr fiets": "Medewerkersbijdrage fiets",
    "eigen bijdrage auto": "Eigen bijdrage auto",
    "wn bydr auto": "Eigen bijdrage auto",
    "aftrek prive gebruik auto": "Aftrek prive gebruik auto",
    "aftrek pr gebr": "Aftrek prive gebruik auto",
    "er leaseauto": "ER leaseauto",
    "div netto inh": "ER leaseauto",
    "loonheffing": "Loonheffing",
    "lb pr volksvz": "Loonheffing",
    "pensioen": "Pensioenpremie",
    "pensioenpremie": "Pensioenpremie",
    "anw hiaat": "ANW-Hiaat",
    "wga hiaat": "WGA-Hiaat",
    "nettoloon": "Nettoloon",
    "er leaseauto": "ER leaseauto",
    "inhouding personeelsvereniging": "Personeelsvereniging",
    "personeelsver": "Personeelsvereniging",
    "uitbetaling flexvergoeding": "Flexvergoeding",
    "flex vergoed": "Flexvergoeding",
    "o u athlon flex": "O.U. Athlon Flex",
    "o u athlonflex": "O.U. Athlon Flex",
    "ou athlonflex": "O.U. Athlon Flex",
    "uit te betalen loon": "Uit te betalen loon",
}


@dataclass
class ComponentAggregate:
    label: str
    amount: Decimal
    raw_labels: set[str]


def load_component_mappings(csv_text: str | None) -> list[ComponentMapping]:
    if not csv_text or not csv_text.strip():
        return []

    reader = csv.DictReader(StringIO(csv_text.strip()))
    mappings: list[ComponentMapping] = []
    for row in reader:
        source_a = _first_present(row, ("document_a", "bron_a", "a", "source_a"))
        source_b = _first_present(row, ("document_b", "bron_b", "b", "source_b"))
        canonical = _first_present(row, ("canonical", "component", "naam", "name"))
        if not canonical:
            canonical = source_a or source_b
        if source_a or source_b:
            mappings.append(
                ComponentMapping(
                    source_a=source_a or "",
                    source_b=source_b or "",
                    canonical=canonical or "",
                )
            )
    return mappings


def compare_documents(
    doc_a: ParsedDocument,
    doc_b: ParsedDocument,
    *,
    mappings: list[ComponentMapping] | None = None,
    tolerance: Decimal = TOLERANCE,
) -> ComparisonResult:
    mappings = mappings or []
    warnings = list(doc_a.warnings) + list(doc_b.warnings)
    warnings.extend(_payslip_warnings(doc_a))
    warnings.extend(_payslip_warnings(doc_b))
    warnings.extend(_context_warnings(doc_a, doc_b))

    comparison_payslips_a = _aggregate_payslips_for_comparison(doc_a.payslips)
    comparison_payslips_b = _aggregate_payslips_for_comparison(doc_b.payslips)

    matched_pairs, employee_rows, unmatched_b = _match_payslips(comparison_payslips_a, comparison_payslips_b)
    component_rows: list[ComponentComparison] = []

    mapping_index = _mapping_index(mappings)
    for payslip_a, payslip_b, note in matched_pairs:
        if note:
            warnings.append(note)
        component_rows.extend(
            _compare_components(
                payslip_a,
                payslip_b,
                mapping_index=mapping_index,
                tolerance=tolerance,
            )
        )

    for payslip_b in unmatched_b:
        employee_rows.append(
            EmployeeComparison(
                status="ALLEEN_IN_B",
                employee_name=payslip_b.employee_name,
                birth_date=payslip_b.birth_date,
                employee_code=payslip_b.employee_code,
                source_a_pages="",
                source_b_pages=_pages(payslip_b),
                match_note="Niet gevonden in document A.",
            )
        )

    warnings.extend(_unmatched_employee_warnings(employee_rows))

    summary = {
        "loonstroken_document_a": len(doc_a.payslips),
        "loonstroken_document_b": len(doc_b.payslips),
        "componenten_document_a": sum(len(payslip.components) for payslip in doc_a.payslips),
        "componenten_document_b": sum(len(payslip.components) for payslip in doc_b.payslips),
        "gematchte_medewerkers": len(matched_pairs),
        "alleen_in_a": sum(1 for row in employee_rows if row.status == "ALLEEN_IN_A"),
        "alleen_in_b": sum(1 for row in employee_rows if row.status == "ALLEEN_IN_B"),
        "componentregels": len(component_rows),
        "componenten_ok": sum(1 for row in component_rows if row.status == "OK"),
        "componentverschillen": sum(1 for row in component_rows if row.status == "VERSCHIL"),
        "componenten_alleen_in_a": sum(1 for row in component_rows if row.status == "ALLEEN_IN_A"),
        "componenten_alleen_in_b": sum(1 for row in component_rows if row.status == "ALLEEN_IN_B"),
        "waarschuwingen": len(warnings),
    }

    return ComparisonResult(
        employees=employee_rows,
        components=component_rows,
        warnings=warnings,
        summary=summary,
    )


def _match_payslips(
    payslips_a: list[Payslip], payslips_b: list[Payslip]
) -> tuple[list[tuple[Payslip, Payslip, str]], list[EmployeeComparison], list[Payslip]]:
    matched: list[tuple[Payslip, Payslip, str]] = []
    employee_rows: list[EmployeeComparison] = []
    remaining_b = list(payslips_b)

    for payslip_a in payslips_a:
        match: Payslip | None = None
        note = ""

        match = _find_exact_code_birthdate_match(payslip_a, remaining_b)
        if match:
            note = _name_conflict_note(payslip_a, match)

        if not match and _employee_key(payslip_a):
            match = _find_exact_name_birthdate_match(payslip_a, remaining_b)
            if match:
                note = _code_conflict_note(payslip_a, match)

        if not match and payslip_a.birth_date:
            match, score = _find_fuzzy_birthdate_match(payslip_a, remaining_b)
            if match:
                note = (
                    f"Fuzzy match gebruikt voor {payslip_a.employee_name} "
                    f"en {match.employee_name} op geboortedatum {payslip_a.birth_date} "
                    f"(score {score:.2f})."
                )
                code_note = _code_conflict_note(payslip_a, match)
                if code_note:
                    note = f"{note} {code_note}"

        if match and match in remaining_b:
            remaining_b.remove(match)
            matched.append((payslip_a, match, note))
            employee_rows.append(
                EmployeeComparison(
                    status="MATCH",
                    employee_name=payslip_a.employee_name or match.employee_name,
                    birth_date=payslip_a.birth_date or match.birth_date,
                    employee_code=payslip_a.employee_code or match.employee_code,
                    source_a_pages=_pages(payslip_a),
                    source_b_pages=_pages(match),
                    match_note=note,
                )
            )
            continue

        employee_rows.append(
            EmployeeComparison(
                status="ALLEEN_IN_A",
                employee_name=payslip_a.employee_name,
                birth_date=payslip_a.birth_date,
                employee_code=payslip_a.employee_code,
                source_a_pages=_pages(payslip_a),
                source_b_pages="",
                match_note="Niet gevonden in document B.",
            )
        )

    return matched, employee_rows, remaining_b


def _aggregate_payslips_for_comparison(payslips: list[Payslip]) -> list[Payslip]:
    grouped: dict[str, list[Payslip]] = {}
    passthrough: list[Payslip] = []
    for payslip in payslips:
        key = _payslip_group_key(payslip)
        if not key:
            passthrough.append(payslip)
            continue
        grouped.setdefault(key, []).append(payslip)

    merged = [_merge_payslip_group(group) for group in grouped.values()]
    return sorted(merged + passthrough, key=lambda item: (item.page_numbers[0] if item.page_numbers else 0))


def _payslip_group_key(payslip: Payslip) -> str | None:
    period = _period_key(payslip)
    if payslip.employee_code and payslip.birth_date:
        return f"code:{normalize_key(payslip.employee_code)}|{payslip.birth_date}|{period}"
    if payslip.employee_name and payslip.birth_date:
        return f"name:{normalize_key(payslip.employee_name)}|{payslip.birth_date}|{period}"
    return None


def _merge_payslip_group(group: list[Payslip]) -> Payslip:
    if len(group) == 1:
        return group[0]

    first = group[0]
    page_numbers = sorted({page for payslip in group for page in payslip.page_numbers})
    components = [component for payslip in group for component in payslip.components]
    warnings = [
        warning
        for payslip in group
        for warning in payslip.warnings
        if warning != "Geen looncomponentregels met bedragen gevonden."
    ]
    return Payslip(
        source=first.source,
        source_name=first.source_name,
        employee_name=next((payslip.employee_name for payslip in group if payslip.employee_name), None),
        birth_date=next((payslip.birth_date for payslip in group if payslip.birth_date), None),
        employee_code=next((payslip.employee_code for payslip in group if payslip.employee_code), None),
        period=next((payslip.period for payslip in group if payslip.period), None),
        page_numbers=page_numbers,
        components=components,
        warnings=warnings,
        raw_text="\n".join(payslip.raw_text for payslip in group if payslip.raw_text),
    )


def _compare_components(
    payslip_a: Payslip,
    payslip_b: Payslip,
    *,
    mapping_index: dict[str, dict[str, str]],
    tolerance: Decimal,
) -> list[ComponentComparison]:
    components_a = _aggregate_components(payslip_a.components, "A", mapping_index)
    components_b = _aggregate_components(payslip_b.components, "B", mapping_index)
    all_keys = sorted(set(components_a) | set(components_b))
    rows: list[ComponentComparison] = []

    for key in all_keys:
        aggregate_a = components_a.get(key)
        aggregate_b = components_b.get(key)
        amount_a = aggregate_a.amount if aggregate_a else None
        amount_b = aggregate_b.amount if aggregate_b else None

        if amount_a is not None and amount_b is not None:
            difference = amount_b - amount_a
            status = "OK" if abs(difference) <= tolerance else "VERSCHIL"
        elif amount_a is not None:
            difference = None
            status = "ALLEEN_IN_A"
        else:
            difference = None
            status = "ALLEEN_IN_B"

        rows.append(
            ComponentComparison(
                employee_name=payslip_a.employee_name or payslip_b.employee_name,
                birth_date=payslip_a.birth_date or payslip_b.birth_date,
                employee_code=payslip_a.employee_code or payslip_b.employee_code,
                deviation_id=_deviation_id(
                    payslip_a,
                    payslip_b,
                    key=key,
                    status=status,
                    amount_a=amount_a,
                    amount_b=amount_b,
                ),
                canonical_component=_display_component_key(key, aggregate_a, aggregate_b),
                component_a=aggregate_a.label if aggregate_a else None,
                amount_a=amount_a,
                component_b=aggregate_b.label if aggregate_b else None,
                amount_b=amount_b,
                difference=difference,
                status=status,
                pages_a=_pages(payslip_a),
                pages_b=_pages(payslip_b),
            )
        )
    return rows


def _aggregate_components(
    components: list[Component],
    source: str,
    mapping_index: dict[str, dict[str, str]],
) -> dict[str, ComponentAggregate]:
    aggregates: dict[str, ComponentAggregate] = {}
    for component in components:
        key = _canonical_component_key(component, source, mapping_index)
        if key not in aggregates:
            aggregates[key] = ComponentAggregate(
                label=_component_label(component),
                amount=component.amount,
                raw_labels={component.raw_line},
            )
        else:
            aggregates[key].amount += component.amount
            aggregates[key].raw_labels.add(component.raw_line)
    return aggregates


def _canonical_component_key(
    component: Component,
    source: str,
    mapping_index: dict[str, dict[str, str]],
) -> str:
    source_map = mapping_index.get(source, {})
    candidates = _component_mapping_candidates(component)
    for candidate in candidates:
        if candidate in source_map:
            return f"map:{normalize_key(source_map[candidate])}"

    alias = _default_component_alias(component)
    if alias:
        return f"alias:{normalize_key(alias)}"

    if component.code:
        return f"code:{normalize_key(component.code)}"
    return f"label:{component.normalized_label}"


def _component_mapping_candidates(component: Component) -> list[str]:
    candidates = {
        normalize_key(component.label),
        normalize_key(component.raw_line),
    }
    if component.code:
        candidates.add(f"code:{normalize_key(component.code)}")
        candidates.add(normalize_key(f"{component.code} {component.label}"))
        candidates.add(normalize_key(component.code))
    return [candidate for candidate in candidates if candidate]


def _mapping_index(mappings: list[ComponentMapping]) -> dict[str, dict[str, str]]:
    index = {"A": {}, "B": {}}
    for mapping in mappings:
        if mapping.source_a:
            for key in _mapping_text_candidates(mapping.source_a):
                index["A"][key] = mapping.canonical
        if mapping.source_b:
            for key in _mapping_text_candidates(mapping.source_b):
                index["B"][key] = mapping.canonical
    return index


def _mapping_text_candidates(value: str) -> set[str]:
    normalized = normalize_key(value)
    candidates = {normalized}
    first = normalized.split(" ", 1)[0] if normalized else ""
    if first:
        candidates.add(f"code:{first}")
    return {candidate for candidate in candidates if candidate}


def _default_component_alias(component: Component) -> str | None:
    candidates = _component_mapping_candidates(component)
    for candidate in candidates:
        if candidate in DEFAULT_COMPONENT_ALIASES:
            return DEFAULT_COMPONENT_ALIASES[candidate]
    return None


def _display_component_key(
    key: str,
    aggregate_a: ComponentAggregate | None,
    aggregate_b: ComponentAggregate | None,
) -> str:
    if key.startswith("map:"):
        return key[4:].replace(" ", " ").title()
    if key.startswith("alias:"):
        return key[6:].replace(" ", " ").title()
    if aggregate_a:
        return aggregate_a.label
    if aggregate_b:
        return aggregate_b.label
    return key


def _component_label(component: Component) -> str:
    if component.code:
        return f"{component.code} {component.label}"
    return component.label


def _period_key(payslip: Payslip) -> str:
    return normalize_period(payslip.period) or normalize_key(payslip.period or "")


def _periods_compatible(payslip_a: Payslip, payslip_b: Payslip) -> bool:
    period_a = _period_key(payslip_a)
    period_b = _period_key(payslip_b)
    return not period_a or not period_b or period_a == period_b


def _employee_key(payslip: Payslip) -> str | None:
    if not payslip.employee_name or not payslip.birth_date:
        return None
    return f"{normalize_key(payslip.employee_name)}|{payslip.birth_date}"


def _employee_code_key(payslip: Payslip) -> str | None:
    if not payslip.employee_code or not payslip.birth_date:
        return None
    return f"{normalize_key(payslip.employee_code)}|{payslip.birth_date}"


def _find_exact_code_birthdate_match(
    payslip_a: Payslip,
    candidates: list[Payslip],
) -> Payslip | None:
    key = _employee_code_key(payslip_a)
    if not key:
        return None
    for candidate in candidates:
        if _employee_code_key(candidate) == key and _periods_compatible(payslip_a, candidate):
            return candidate
    return None


def _find_exact_name_birthdate_match(
    payslip_a: Payslip,
    candidates: list[Payslip],
) -> Payslip | None:
    key = _employee_key(payslip_a)
    if not key:
        return None
    for candidate in candidates:
        if _employee_key(candidate) == key and _periods_compatible(payslip_a, candidate):
            return candidate
    return None


def _code_conflict_note(payslip_a: Payslip, payslip_b: Payslip) -> str:
    if not payslip_a.employee_code or not payslip_b.employee_code:
        return ""
    if normalize_key(payslip_a.employee_code) == normalize_key(payslip_b.employee_code):
        return ""
    return (
        "Naam en geboortedatum matchen, maar medewerker codes verschillen: "
        f"A={payslip_a.employee_code}, B={payslip_b.employee_code}."
    )


def _name_conflict_note(payslip_a: Payslip, payslip_b: Payslip) -> str:
    if not payslip_a.employee_name or not payslip_b.employee_name:
        return ""
    if _person_name_compare_key(payslip_a.employee_name) == _person_name_compare_key(payslip_b.employee_name):
        return ""
    return (
        "Medewerker code en geboortedatum matchen, maar namen verschillen: "
        f"A={payslip_a.employee_name}, B={payslip_b.employee_name}."
    )


def _person_name_compare_key(name: str) -> str:
    tokens = normalize_key(name).split()
    normalized: list[str] = []
    initials = ""
    for token in tokens:
        if len(token) == 1:
            initials += token
            continue
        if initials:
            normalized.append(initials)
            initials = ""
        normalized.append(token)
    if initials:
        normalized.append(initials)
    return " ".join(normalized)


def _find_fuzzy_birthdate_match(
    payslip_a: Payslip, candidates: list[Payslip]
) -> tuple[Payslip | None, float]:
    if not payslip_a.employee_name or not payslip_a.birth_date:
        return None, 0.0
    name_a = normalize_key(payslip_a.employee_name)
    best_match = None
    best_score = 0.0

    for candidate in candidates:
        if candidate.birth_date != payslip_a.birth_date or not candidate.employee_name:
            continue
        if not _periods_compatible(payslip_a, candidate):
            continue
        score = SequenceMatcher(None, name_a, normalize_key(candidate.employee_name)).ratio()
        if score > best_score:
            best_match = candidate
            best_score = score

    if best_match and best_score >= 0.86:
        return best_match, best_score
    return None, best_score


def _pages(payslip: Payslip) -> str:
    return ", ".join(str(page) for page in payslip.page_numbers)


def _deviation_id(
    payslip_a: Payslip,
    payslip_b: Payslip,
    *,
    key: str,
    status: str,
    amount_a: Decimal | None,
    amount_b: Decimal | None,
) -> str:
    parts = [
        payslip_a.employee_code or payslip_b.employee_code or "",
        payslip_a.employee_name or payslip_b.employee_name or "",
        payslip_a.birth_date or payslip_b.birth_date or "",
        _pages(payslip_a),
        _pages(payslip_b),
        key,
        status,
        str(amount_a or ""),
        str(amount_b or ""),
    ]
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _context_warnings(doc_a: ParsedDocument, doc_b: ParsedDocument) -> list[str]:
    warnings: list[str] = []
    if doc_a.normalized_period and doc_b.normalized_period and doc_a.normalized_period != doc_b.normalized_period:
        warnings.append(
            "Verschillende periodes gedetecteerd: "
            f"document A={doc_a.normalized_period}, document B={doc_b.normalized_period}."
        )
    if doc_a.provider and doc_b.provider and doc_a.provider != doc_b.provider:
        warnings.append(
            f"Providerwissel gedetecteerd: document A={doc_a.provider}, document B={doc_b.provider}."
        )
    if doc_a.scenario and doc_b.scenario and doc_a.scenario != doc_b.scenario:
        warnings.append(
            f"Verschillende scenario's gedetecteerd: document A={doc_a.scenario}, document B={doc_b.scenario}."
        )
    return warnings


def _unmatched_employee_warnings(employee_rows: list[EmployeeComparison]) -> list[str]:
    warnings: list[str] = []
    for row in employee_rows:
        if row.status == "ALLEEN_IN_A":
            warnings.append(
                _unmatched_warning(
                    row,
                    source="document A",
                    missing_source="document B",
                    pages=row.source_a_pages,
                )
            )
        elif row.status == "ALLEEN_IN_B":
            warnings.append(
                _unmatched_warning(
                    row,
                    source="document B",
                    missing_source="document A",
                    pages=row.source_b_pages,
                )
            )
    return warnings


def _unmatched_warning(
    row: EmployeeComparison,
    *,
    source: str,
    missing_source: str,
    pages: str,
) -> str:
    identity = row.employee_name or "onbekende medewerker"
    if row.birth_date:
        identity = f"{identity} ({row.birth_date})"
    if row.employee_code:
        identity = f"{identity}, code {row.employee_code}"
    page_text = f", pagina's {pages}" if pages else ""
    return f"Geen match gevonden voor {identity} uit {source}{page_text}; niet gevonden in {missing_source}."


def _payslip_warnings(document: ParsedDocument) -> list[str]:
    warnings: list[str] = []
    for index, payslip in enumerate(document.payslips, start=1):
        for warning in payslip.warnings:
            pages = _pages(payslip)
            warnings.append(
                f"{document.source_name}, loonstrook {index}, pagina {pages}: {warning}"
            )
    return warnings


def _first_present(row: dict[str, str], keys: tuple[str, ...]) -> str:
    lower_map = {key.lower().strip(): value for key, value in row.items()}
    for key in keys:
        value = lower_map.get(key)
        if value:
            return value.strip()
    return ""
