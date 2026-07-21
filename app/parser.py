from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .models import Component, ExtractionResult, PageText, ParsedDocument, Payslip, TextWord
from .text_utils import AMOUNT_RE, clean_line, normalize_key, parse_amount


NAME_RE = re.compile(
    r"\b(?:naam werknemer|werknemer|medewerker|personeelslid|naam)\b\s*[:\-]?\s*(?P<value>.+)?",
    re.IGNORECASE,
)
DOB_RE = re.compile(
    r"\b(?:geboortedatum|geboorte\s*datum|geb\.?\s*datum|geb\.?\s*dat\.?|geboren)\b\s*[:\-]?\s*(?P<date>\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})",
    re.IGNORECASE,
)
EMPLOYEE_CODE_RE = re.compile(
    r"\b(?:medewerker\s*(?:nummer|nr\.?|code)|medew\.?\s*code|personeels\s*(?:nummer|nr\.?)|pers\.?\s*nr\.?|werknemer\s*(?:nummer|nr\.?|code)|werknr\.?|employee\s*(?:no\.?|number|id|code)|payroll\s*id)\b\s*[:\-]?\s*(?P<value>[A-Za-z0-9][A-Za-z0-9 ._/-]{1,35})?",
    re.IGNORECASE,
)
GENERIC_CODE_RE = re.compile(
    r"^\s*code\s*[:\-]\s*(?P<value>[A-Za-z0-9][A-Za-z0-9 ._/-]{1,35})",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b")
PERIOD_RE = re.compile(
    r"\b(?:periode|loonperiode|tijdvak|maand)\b\s*[:\-]?\s*(?P<period>[A-Za-z0-9 /\-.]{3,30})",
    re.IGNORECASE,
)
STOP_LABEL_RE = re.compile(
    r"\b(?:geboortedatum|geboorte\s*datum|geb\.?\s*datum|personeelsnummer|werknemersnummer|periode|loonperiode|tijdvak)\b",
    re.IGNORECASE,
)
SKIP_NAME_HINTS = (
    "werkgever",
    "bedrijf",
    "inhoudingsplichtige",
    "loonheffingennummer",
    "administratie",
    "grootboek",
)
SKIP_COMPONENT_HINTS = (
    "geboortedatum",
    "geboorte datum",
    "rekeningnummer",
    "iban",
    "bsn",
    "burgerservicenummer",
    "loonheffingennummer",
)
MONTH_NAMES = (
    "januari",
    "februari",
    "maart",
    "april",
    "mei",
    "juni",
    "juli",
    "augustus",
    "september",
    "oktober",
    "november",
    "december",
)
MONTH_TO_NUMBER = {month: index for index, month in enumerate(MONTH_NAMES, start=1)}
MONTH_RE = re.compile(r"\b(?:" + "|".join(MONTH_NAMES) + r")\b", re.IGNORECASE)
MONTH_YEAR_RE = re.compile(
    r"\b(?:" + "|".join(MONTH_NAMES) + r")\s+\d{4}\b",
    re.IGNORECASE,
)
PROVIDER_MARKERS = (
    ("AFAS", ("afas", "profit")),
    ("Loket", ("loket",)),
    ("Nmbrs", ("nmbrs",)),
    ("ADP", ("adp",)),
    ("Visma", ("visma", "raet")),
    ("SD Worx", ("sd worx", "sdworx")),
)
SCENARIO_MARKERS = (
    ("schaduwdraai", ("schaduwdraai", "shadow run")),
    ("proefrun", ("proef", "concept", "test", "testrun")),
    ("productie", ("productie", "definitief", "final")),
)
SECTION_STOP_KEYS = {
    "medewerker holding",
    "vaste gegevens",
    "mobiliteit",
    "reserveringen en saldi",
    "betalingen",
}
HEADER_LABEL_KEYS = {
    "gemaakt op",
    "periode",
    "datum van",
    "datum t m",
    "datum tm",
    "uitbetaald",
    "betalingen",
}


@dataclass
class _VisualLine:
    y: float
    words: list[TextWord]


@dataclass(frozen=True)
class _ColumnRole:
    role: str
    x: float


@dataclass
class _PayrollTable:
    rows: list[_VisualLine]
    columns: list[_ColumnRole]
    label_left: float
    label_right: float


def parse_document(extraction: ExtractionResult, *, source: str) -> ParsedDocument:
    warnings = list(extraction.warnings)
    segments = split_payslip_segments(extraction.pages)
    payslips: list[Payslip] = []
    document_text = "\n".join(page.text for page in extraction.pages)

    for segment in segments:
        page_numbers = [page.page_number for page in segment]
        raw_text = "\n".join(page.text for page in segment)
        payslip_warnings: list[str] = []
        name = extract_name_from_pages(segment, raw_text)
        birth_date = extract_birth_date_from_pages(segment, raw_text)
        employee_code = extract_employee_code_from_pages(segment, raw_text)
        period = extract_period_from_pages(segment, raw_text)
        components = extract_components_from_pages(segment, raw_text)

        if not name:
            payslip_warnings.append("Naam niet betrouwbaar gevonden.")
        if not birth_date:
            payslip_warnings.append("Geboortedatum niet betrouwbaar gevonden.")
        if not components:
            payslip_warnings.append("Geen looncomponentregels met bedragen gevonden.")

        payslips.append(
            Payslip(
                source=source,
                source_name=extraction.source_name,
                employee_name=name,
                birth_date=birth_date,
                employee_code=employee_code,
                period=period,
                page_numbers=page_numbers,
                components=components,
                warnings=payslip_warnings,
                raw_text=raw_text,
            )
        )

    if not payslips:
        warnings.append("Geen loonstroken gevonden in het document.")

    duplicate_keys = _duplicate_identity_keys(payslips)
    for duplicate_key, count in duplicate_keys.items():
        warnings.append(f"Dubbele identiteit gevonden ({duplicate_key}) in {count} loonstroken.")

    return ParsedDocument(
        source=source,
        source_name=extraction.source_name,
        payslips=payslips,
        warnings=warnings,
        period=_dominant_value([payslip.period for payslip in payslips]),
        normalized_period=_dominant_value([normalize_period(payslip.period) for payslip in payslips]),
        provider=detect_provider(extraction.source_name, document_text),
        scenario=detect_scenario(extraction.source_name, document_text),
    )


def split_payslip_segments(pages: list[PageText]) -> list[list[PageText]]:
    if _looks_like_db_grid_document(pages):
        return [[page] for page in pages]

    afas_segments = _split_afas_segments(pages)
    if afas_segments:
        return afas_segments

    segments: list[list[PageText]] = []
    current: list[PageText] = []

    for page in pages:
        chunks = _split_page_on_multiple_identities(page)
        for chunk in chunks:
            starts_new = _has_identity_marker(chunk.text)
            if starts_new and current:
                segments.append(current)
                current = [chunk]
            else:
                current.append(chunk)

    if current:
        segments.append(current)
    return segments


def _looks_like_db_grid_document(pages: list[PageText]) -> bool:
    if not pages:
        return False
    matches = sum(1 for page in pages if _is_db_grid_page(page))
    return matches >= max(1, len(pages) // 2)


def _is_db_grid_page(page: PageText) -> bool:
    if not page.words:
        return False
    lines = _visual_lines(page.words)
    has_employee_header = any("werknr" in normalize_key(_line_text(line)) for line in lines)
    has_specificatie = any(
        "specificatie" in _compact_line_key(line) and "opbouw" in _compact_line_key(line)
        for line in lines
    )
    return has_employee_header and has_specificatie


def _split_afas_segments(pages: list[PageText]) -> list[list[PageText]]:
    if not pages or not any(_is_afas_profile_page(page) for page in pages):
        return []

    segments: list[list[PageText]] = []
    current: list[PageText] = []
    for page in pages:
        if _is_afas_identity_page(page):
            if current:
                segments.append(current)
            current = [page]
            continue

        if _is_afas_calculation_page(page):
            if current:
                current.append(page)
                segments.append(current)
                current = []
            else:
                segments.append([page])
            continue

        if current:
            current.append(page)
        else:
            segments.append([page])

    if current:
        segments.append(current)
    return segments


def _is_afas_profile_page(page: PageText) -> bool:
    key = normalize_key(page.text)
    return "loonstrook" in key and (
        "overige gegevens" in key
        or "loonberekening" in key
        or "medew code" in key
    )


def _is_afas_identity_page(page: PageText) -> bool:
    key = normalize_key(page.text)
    return "loonstrook" in key and "medew code" in key and "loonberekening" not in key


def _is_afas_calculation_page(page: PageText) -> bool:
    key = normalize_key(page.text)
    return "loonberekening" in key and "omschrijving" in key


def extract_name_from_pages(pages: list[PageText], text: str) -> str | None:
    db_name = _extract_db_grid_name_from_pages(pages)
    if db_name:
        return db_name

    afas_name = _extract_afas_header_name_from_pages(pages)
    if afas_name:
        return afas_name

    for page in pages:
        for line in _visual_lines(page.words):
            for word in line.words:
                word_match = re.search(
                    r"\b(?:de heer|mevrouw|mw\.?|dhr\.?)\s+(?P<name>[A-Z][A-Z. ]{3,})",
                    word.text,
                    re.IGNORECASE,
                )
                if word_match:
                    candidate = _normalize_person_prefix(word_match.group("name"))
                    if _looks_like_person_name(candidate):
                        return candidate

            line_text = _line_text(line)
            match = re.search(
                r"\b(?:de heer|mevrouw|mw\.?|dhr\.?)\s+(?P<name>[A-Z][A-Z. ]{3,})",
                line_text,
                re.IGNORECASE,
            )
            if match:
                candidate = _normalize_person_prefix(match.group("name"))
                if _looks_like_person_name(candidate):
                    return candidate
    name = extract_name(text)
    if name:
        return name
    return None


def extract_name(text: str) -> str | None:
    lines = [clean_line(line) for line in text.splitlines() if clean_line(line)]

    honorific_name = _extract_honorific_name(lines)
    if honorific_name:
        return honorific_name

    header_name = _extract_name_from_header(lines)
    if header_name:
        return header_name

    for index, line in enumerate(lines):
        lower = line.lower()
        if any(hint in lower for hint in SKIP_NAME_HINTS):
            continue
        if normalize_key(line) == "naam" and index + 1 < len(lines):
            next_key = normalize_key(lines[index + 1])
            if next_key in {"rekening", "periode betaling"}:
                continue
        match = NAME_RE.search(line)
        if not match:
            continue

        value = (match.group("value") or "").strip(" :-")
        if not value and index + 1 < len(lines):
            value = lines[index + 1].strip(" :-")
        value = STOP_LABEL_RE.split(value)[0]
        value = re.sub(r"^\d{2,10}\s+", "", value).strip(" :-")
        value = re.sub(r"\s{2,}", " ", value)
        if _looks_like_person_name(value):
            return value
    return None


def extract_birth_date_from_pages(pages: list[PageText], text: str) -> str | None:
    birth_date = extract_birth_date(text)
    if birth_date:
        return birth_date

    for page in pages:
        lines = _visual_lines(page.words)
        for index, line in enumerate(lines):
            line_key = normalize_key(_line_text(line))
            if "geboortedatum" in line_key or "geb datum" in line_key:
                same_line_date = _date_from_visual_line(line)
                if same_line_date:
                    return same_line_date

                header_center = _line_center(line)
                for candidate in lines[index + 1 : index + 6]:
                    for word in candidate.words:
                        if abs(_word_center(word) - header_center) > 90:
                            continue
                        parsed = _date_from_text(word.text)
                        if parsed:
                            return parsed
    return None


def extract_birth_date(text: str) -> str | None:
    match = DOB_RE.search(text)
    if match:
        return _normalize_date(match.group("date"))

    lines = [clean_line(line) for line in text.splitlines() if clean_line(line)]
    for index, line in enumerate(lines):
        key = normalize_key(line)
        if key in {"geboortedatum", "geboorte datum", "geb datum", "geb dat"}:
            for candidate in lines[index + 1 : index + 4]:
                parsed = _date_from_text(candidate)
                if parsed:
                    return parsed
    return None


def extract_employee_code_from_pages(pages: list[PageText], text: str) -> str | None:
    employee_code = extract_employee_code(text)
    if employee_code:
        return employee_code

    for page in pages:
        lines = _visual_lines(page.words)
        for index, line in enumerate(lines):
            line_text = _line_text(line)
            employee_code = _employee_code_from_visual_line(line)
            if employee_code:
                return employee_code
            employee_code = _employee_code_from_line(line_text)
            if employee_code:
                return employee_code
            if EMPLOYEE_CODE_RE.search(line_text) and index + 1 < len(lines):
                employee_code = _clean_employee_code(_line_text(lines[index + 1]))
                if employee_code:
                    return employee_code
    return None


def extract_employee_code(text: str) -> str | None:
    lines = [clean_line(line) for line in text.splitlines() if clean_line(line)]
    for index, line in enumerate(lines):
        employee_code = _employee_code_from_line(line)
        if employee_code:
            return employee_code

        if EMPLOYEE_CODE_RE.search(line) and index + 1 < len(lines):
            employee_code = _clean_employee_code(lines[index + 1])
            if employee_code:
                return employee_code
    return None


def extract_period_from_pages(pages: list[PageText], text: str) -> str | None:
    period = extract_period(text)
    if period:
        return period

    for page in pages:
        lines = _visual_lines(page.words)
        for line in lines:
            key = normalize_key(_line_text(line))
            if "periode" not in key:
                continue
            month = _month_from_text(_line_text(line))
            if month:
                return _period_with_year(month, "\n".join(page.text for page in pages))
            period_code = re.search(r"\b\d{1,2}/\d{4}\b", _line_text(line))
            if period_code:
                return period_code.group(0)
    return None


def extract_period(text: str) -> str | None:
    lines = [clean_line(line) for line in text.splitlines() if clean_line(line)]
    for index, line in enumerate(lines):
        if normalize_key(line) == "loonstrook" and index > 0:
            previous_line = lines[index - 1]
            if MONTH_YEAR_RE.search(previous_line):
                return previous_line
    for line in lines:
        if MONTH_YEAR_RE.fullmatch(line):
            return line
    for index, line in enumerate(lines):
        if normalize_key(line).rstrip(":") == "periode":
            for candidate in lines[index + 1 : index + 5]:
                month = _month_from_text(candidate)
                if month:
                    return _period_with_year(month, text)
                period_code = re.search(r"\b\d{1,2}/\d{4}\b", candidate)
                if period_code:
                    return period_code.group(0)
    for line in lines:
        period_code = re.search(r"\b\d{1,2}/\d{4}\b", line)
        if period_code:
            return period_code.group(0)
        if normalize_key(line) == "periode betaling":
            continue
        match = PERIOD_RE.search(line)
        if match:
            value = STOP_LABEL_RE.split(match.group("period"))[0].strip(" :-")
            if value:
                return value
    return None


def normalize_period(period: str | None) -> str | None:
    if not period:
        return None
    value = clean_line(period)

    year_month = re.search(r"\b(?P<year>20\d{2}|19\d{2})[-/.](?P<month>\d{1,2})\b", value)
    if year_month:
        month = int(year_month.group("month"))
        if 1 <= month <= 12:
            return f"{year_month.group('year')}-{month:02d}"

    month_year = re.search(r"\b(?P<month>\d{1,2})[-/.](?P<year>20\d{2}|19\d{2})\b", value)
    if month_year:
        month = int(month_year.group("month"))
        if 1 <= month <= 12:
            return f"{month_year.group('year')}-{month:02d}"

    text_month = MONTH_RE.search(value)
    text_year = re.search(r"\b(20\d{2}|19\d{2})\b", value)
    if text_month and text_year:
        month = MONTH_TO_NUMBER[text_month.group(0).lower()]
        return f"{text_year.group(1)}-{month:02d}"

    return value


def detect_provider(source_name: str, text: str) -> str | None:
    haystack = normalize_key(f"{source_name}\n{text}")
    for provider, markers in PROVIDER_MARKERS:
        if any(marker in haystack for marker in markers):
            return provider
    return None


def detect_scenario(source_name: str, text: str) -> str | None:
    haystack = normalize_key(f"{source_name}\n{text}")
    for scenario, markers in SCENARIO_MARKERS:
        if any(marker in haystack for marker in markers):
            return scenario
    return None


def extract_components_from_pages(pages: list[PageText], text: str) -> list[Component]:
    layout_components = extract_components_from_layout(pages)
    if layout_components:
        return layout_components
    return extract_components(text)


def extract_components_from_layout(pages: list[PageText]) -> list[Component]:
    components: list[Component] = []
    for page in pages:
        if not page.words:
            continue
        lines = _visual_lines(page.words)
        db_components = _components_from_db_grid_layout(lines)
        if db_components:
            components.extend(db_components)
            continue

        loket_components = _components_from_loket_layout(lines)
        if loket_components:
            components.extend(loket_components)
            continue

        for table in _afas_payroll_tables(lines):
            components.extend(_components_from_visual_table(table))
    return components


def extract_components(text: str) -> list[Component]:
    components: list[Component] = []
    for raw in text.splitlines():
        line = clean_line(raw)
        if not line or _skip_component_line(line):
            continue

        matches = list(AMOUNT_RE.finditer(line))
        if not matches:
            continue

        amount_match = matches[-1]
        amount = parse_amount(amount_match.group("amount"))
        if amount is None:
            continue

        prefix = line[: amount_match.start()].strip(" :-")
        if len(prefix) < 3:
            continue

        code, label = _extract_component_code_and_label(prefix)
        if not label or len(normalize_key(label)) < 3:
            continue

        components.append(
            Component(
                code=code,
                label=label,
                normalized_label=normalize_key(label),
                amount=amount,
                raw_line=line,
            )
        )
    return components


def _extract_honorific_name(lines: list[str]) -> str | None:
    for line in lines:
        match = re.search(r"\b(?:de heer|mevrouw|mw\.?|dhr\.?)\s+(?P<name>[A-Z][A-Z. ]{3,})", line, re.IGNORECASE)
        if not match:
            continue
        candidate = _normalize_person_prefix(match.group("name"))
        if _looks_like_person_name(candidate):
            return candidate
    return None


def _extract_db_grid_name_from_pages(pages: list[PageText]) -> str | None:
    for page in pages:
        if page.words and not _is_db_grid_page(page):
            continue
        for line in _visual_lines(page.words):
            words = line.words
            for index, word in enumerate(words):
                key = normalize_key(word.text)
                if key not in {"de", "dhr", "dhr.", "mevrouw", "mw"}:
                    continue
                if key == "de" and index + 1 < len(words) and normalize_key(words[index + 1].text) != "heer":
                    continue
                start = index + 2 if key == "de" else index + 1
                name_words: list[TextWord] = []
                previous_x1 = words[start - 1].x1 if start > 0 else word.x1
                for candidate in words[start:]:
                    candidate_key = normalize_key(candidate.text)
                    if candidate.x0 - previous_x1 > 70:
                        break
                    if candidate_key in {"valid", "digital", "business", "bv", "b v", "n v", "nv"}:
                        break
                    if re.search(r"\d", candidate.text):
                        break
                    name_words.append(candidate)
                    previous_x1 = candidate.x1
                candidate_name = clean_line(" ".join(item.text for item in name_words))
                candidate_name = _normalize_person_prefix(candidate_name)
                if _looks_like_person_name(candidate_name):
                    return candidate_name
    return None


def _extract_afas_header_name_from_pages(pages: list[PageText]) -> str | None:
    for page in pages:
        if not _is_afas_profile_page(page):
            continue
        for line in _visual_lines(page.words):
            if line.y > 120:
                continue
            line_text = _line_text(line)
            key = normalize_key(line_text)
            if any(skip in key for skip in ("loonstrook", "gemaakt op", "datum van", "datum t m")):
                continue
            month = _month_from_text(line_text)
            if not month:
                continue
            candidate = re.sub(rf"\b{re.escape(month)}\b", "", line_text, flags=re.IGNORECASE, count=1)
            candidate = clean_line(candidate.strip(" :-"))
            if DATE_RE.search(candidate) or re.search(r"\d{4}\s?[A-Z]{2}", candidate):
                continue
            if _looks_like_person_name(candidate):
                return candidate
    return None


def _extract_name_from_header(lines: list[str]) -> str | None:
    for index, line in enumerate(lines):
        if normalize_key(line) != "loonstrook":
            continue
        for candidate in lines[index + 1 : index + 12]:
            candidate_key = normalize_key(candidate.rstrip(":"))
            if candidate_key in HEADER_LABEL_KEYS:
                continue
            if _month_from_text(candidate):
                continue
            if candidate.startswith("€") or candidate_key.startswith("eur "):
                continue
            if re.search(r"\d{4}\s?[A-Z]{2}", candidate):
                continue
            if DATE_RE.search(candidate):
                continue
            if _looks_like_person_name(candidate):
                return candidate
    return None


def _visual_lines(words: list[TextWord]) -> list[_VisualLine]:
    lines: list[_VisualLine] = []
    for word in sorted(words, key=lambda item: (item.y0, item.x0)):
        if not word.text.strip():
            continue
        if normalize_key(word.text) == "concept":
            continue
        if lines and abs(lines[-1].y - word.y0) <= 8:
            lines[-1].words.append(word)
            continue
        lines.append(_VisualLine(y=word.y0, words=[word]))

    for line in lines:
        line.words.sort(key=lambda item: item.x0)
    return lines


def _afas_payroll_tables(lines: list[_VisualLine]) -> list[_PayrollTable]:
    tables: list[_PayrollTable] = []
    for index, line in enumerate(lines):
        key = normalize_key(_line_text(line))
        if "omschrijving" not in key:
            continue
        if not any(marker in key for marker in ("bruto netto", "normaal", "bijzonder", "t m periode")):
            continue

        columns = _columns_from_afas_header(line)
        if not columns:
            continue

        data_columns = [column for column in columns if column.role != "omschrijving"]
        if not data_columns:
            continue

        label_left = min(word.x0 for word in line.words if normalize_key(word.text) == "omschrijving")
        label_right = min(column.x for column in data_columns) - 10
        rows: list[_VisualLine] = []
        for row in lines[index + 1 :]:
            left_key = normalize_key(_label_text(row, label_left, label_right))
            full_key = normalize_key(_line_text(row))
            if left_key in SECTION_STOP_KEYS or full_key in SECTION_STOP_KEYS:
                break
            if "concept loonstrook" in full_key:
                continue
            rows.append(row)

        if rows:
            tables.append(
                _PayrollTable(
                    rows=rows,
                    columns=columns,
                    label_left=label_left,
                    label_right=label_right,
                )
            )
    return tables


def _columns_from_afas_header(line: _VisualLine) -> list[_ColumnRole]:
    columns: list[_ColumnRole] = []
    words = line.words
    index = 0
    while index < len(words):
        word = words[index]
        key = normalize_key(word.text)
        role: str | None = None

        if key == "omschrijving":
            role = "omschrijving"
        elif key == "aantal":
            role = "aantal"
        elif key == "basis":
            role = "basis"
        elif key == "bruto netto":
            role = "bruto_netto"
        elif key == "periode":
            previous_key = normalize_key(words[index - 1].text) if index else ""
            role = "tm_periode" if previous_key in {"t m", "tm"} else "periode"
        elif key in {"t m", "tm"} and index + 1 < len(words) and normalize_key(words[index + 1].text) == "periode":
            role = "tm_periode"
        elif key == "normaal":
            role = "normaal"
        elif key == "bijzonder":
            role = "bijzonder"
        elif key == "cumulatief":
            role = "cumulatief"

        if role:
            columns.append(_ColumnRole(role=role, x=word.x0))
        index += 1

    return _dedupe_columns(columns)


def _components_from_visual_table(table: _PayrollTable) -> list[Component]:
    components: list[Component] = []
    last_component: Component | None = None
    last_y: float | None = None

    for line in table.rows:
        label = _label_text(line, table.label_left, table.label_right)
        role_amounts = _role_amounts(line, table.columns, table.label_right)

        if not role_amounts:
            if (
                last_component
                and label.startswith("(")
                and last_y is not None
                and line.y - last_y <= 18
            ):
                last_component.label = clean_line(f"{last_component.label} {label}")
                last_component.normalized_label = normalize_key(last_component.label)
                last_component.raw_line = clean_line(f"{last_component.raw_line} {label}")
            continue

        amount = _first_amount(
            role_amounts,
            ("bruto_netto", "periode", "normaal", "bijzonder"),
        )
        if amount is None or not label or _skip_component_line(label):
            continue

        component = _component_from_label_amount(label, amount, role_amounts)
        if component:
            components.append(component)
            last_component = component
            last_y = line.y

    return components


def _components_from_db_grid_layout(lines: list[_VisualLine]) -> list[Component]:
    header_index = _db_grid_header_index(lines)
    if header_index is None:
        return []

    column_line_index = _db_grid_column_line_index(lines, header_index)
    if column_line_index is None:
        return []

    column_line = lines[column_line_index]
    columns = _columns_from_db_grid_header(lines[header_index], column_line)
    data_columns = [column for column in columns if column.role != "omschrijving"]
    if len(data_columns) < 2:
        return []

    first_data_x = min(column.x for column in data_columns)
    label_left = max(0, first_data_x - 95)
    label_right = first_data_x - 12

    components: list[Component] = []
    for line in lines[column_line_index + 1 :]:
        if line.y - lines[header_index].y > 250:
            break

        label = _label_text(line, label_left, label_right)
        if not label or _skip_db_grid_label(label):
            continue

        role_amounts = _role_amounts(line, columns, label_right)
        amount = _db_grid_period_amount(role_amounts)
        if amount is None:
            continue

        component = _component_from_label_amount(label, amount, role_amounts)
        if component:
            components.append(component)

    bottom_total = _db_grid_bottom_total(lines)
    if bottom_total is not None:
        components.append(
            Component(
                code=None,
                label="Uit te betalen loon",
                normalized_label=normalize_key("Uit te betalen loon"),
                amount=bottom_total,
                raw_line=f"Uit te betalen loon {bottom_total}",
            )
        )
    return components


def _db_grid_header_index(lines: list[_VisualLine]) -> int | None:
    for index, line in enumerate(lines):
        compact = _compact_line_key(line)
        if "specificatie" in compact and "opbouw" in compact and "tmperiode" in compact:
            return index
    return None


def _db_grid_column_line_index(lines: list[_VisualLine], header_index: int) -> int | None:
    for index in range(header_index + 1, min(header_index + 5, len(lines))):
        key = normalize_key(_line_text(lines[index]))
        if key.count("tabel") >= 2 and key.count("tarief") >= 2:
            return index
    return None


def _columns_from_db_grid_header(header_line: _VisualLine, column_line: _VisualLine) -> list[_ColumnRole]:
    tabels = [word.x0 for word in column_line.words if normalize_key(word.text) == "tabel"]
    tarifs = [word.x0 for word in column_line.words if normalize_key(word.text) == "tarief"]
    if len(tabels) < 2 or len(tarifs) < 2:
        return []
    tm_x = max((word.x0 for word in header_line.words), default=tarifs[-1] + 80)
    return [
        _ColumnRole("specificatie_tabel", tabels[0]),
        _ColumnRole("specificatie_tarief", tarifs[0]),
        _ColumnRole("opbouw_tabel", tabels[1]),
        _ColumnRole("opbouw_tarief", tarifs[1]),
        _ColumnRole("tm_periode", tm_x),
    ]


def _db_grid_period_amount(role_amounts: dict[str, Decimal]) -> Decimal | None:
    specificatie_roles = ("specificatie_tabel", "specificatie_tarief")
    if any(role in role_amounts for role in specificatie_roles):
        return sum((role_amounts[role] for role in specificatie_roles if role in role_amounts), Decimal("0"))
    return _first_amount(role_amounts, ("tm_periode", "opbouw_tabel", "opbouw_tarief"))


def _skip_db_grid_label(label: str) -> bool:
    key = normalize_key(label)
    if not key:
        return True
    return key in {"tabel", "tarief", "specificatie", "opbouw", "tm periode", "heff pl loon"}


def _db_grid_bottom_total(lines: list[_VisualLine]) -> Decimal | None:
    for line in reversed(lines):
        compact = _compact_line_key(line)
        if "uittebetalen" not in compact:
            continue
        amounts = [
            parse_amount(match.group("amount"))
            for word in line.words
            for match in AMOUNT_RE.finditer(word.text)
        ]
        amounts = [amount for amount in amounts if amount is not None]
        if amounts:
            return amounts[-1]
    return None


def _components_from_loket_layout(lines: list[_VisualLine]) -> list[Component]:
    header_index: int | None = None
    spec_x: float | None = None
    opbouw_x: float | None = None
    tm_x: float | None = None

    for index, line in enumerate(lines):
        for word in line.words:
            key = normalize_key(word.text)
            if key == "specificatie":
                header_index = index
                spec_x = word.x0
            elif key == "opbouw":
                opbouw_x = word.x0
            elif key in {"tm periode", "t m periode"}:
                tm_x = word.x0
        if header_index is not None:
            break

    if header_index is None or spec_x is None:
        return []

    label_left = max(0, spec_x - 190)
    label_right = spec_x - 25
    data_columns = [
        _ColumnRole("specificatie_tabel", spec_x - 20),
        _ColumnRole("specificatie_tarief", spec_x + 80),
    ]
    if opbouw_x is not None:
        data_columns.extend(
            [
                _ColumnRole("opbouw_tabel", opbouw_x - 25),
                _ColumnRole("opbouw_tarief", opbouw_x + 70),
            ]
        )
    if tm_x is not None:
        data_columns.append(_ColumnRole("tm_periode", tm_x + 40))

    components: list[Component] = []
    for line in lines[header_index + 1 :]:
        if line.y - lines[header_index].y > 360:
            break

        label = _label_text(line, label_left, label_right)
        if not label or _skip_loket_label(label):
            continue

        role_amounts = _role_amounts(line, data_columns, label_right)
        if not role_amounts:
            continue

        priority = ("tm_periode", "specificatie_tabel", "specificatie_tarief", "opbouw_tabel", "opbouw_tarief")
        if normalize_key(label) not in {"bruto", "nettoloon"}:
            priority = ("specificatie_tabel", "specificatie_tarief", "tm_periode", "opbouw_tabel", "opbouw_tarief")
        amount = _first_amount(role_amounts, priority)
        if amount is None:
            continue

        component = _component_from_label_amount(label, amount, role_amounts)
        if component:
            components.append(component)

    bottom_total = _loket_bottom_total(lines)
    if bottom_total is not None:
        components.append(
            Component(
                code=None,
                label="Uit te betalen loon",
                normalized_label=normalize_key("Uit te betalen loon"),
                amount=bottom_total,
                raw_line=f"Uit te betalen loon {bottom_total}",
            )
        )
    return components


def _component_from_label_amount(
    label: str,
    amount: Decimal,
    role_amounts: dict[str, Decimal],
) -> Component | None:
    code, clean_label = _extract_component_code_and_label(label)
    if not clean_label or len(normalize_key(clean_label)) < 3:
        return None
    return Component(
        code=code,
        label=clean_label,
        normalized_label=normalize_key(clean_label),
        amount=amount,
        raw_line=clean_line(f"{label} {_format_role_amounts(role_amounts)}"),
    )


def _role_amounts(
    line: _VisualLine,
    columns: list[_ColumnRole],
    label_right: float,
) -> dict[str, Decimal]:
    amounts: dict[str, Decimal] = {}
    data_columns = [column for column in columns if column.role != "omschrijving"]
    for word in line.words:
        if word.x0 <= label_right or "%" in word.text:
            continue
        if not AMOUNT_RE.fullmatch(word.text.strip()):
            continue
        amount = parse_amount(word.text)
        if amount is None:
            continue
        role = _nearest_column_role(word, data_columns)
        if role in {"aantal", "basis", "cumulatief"}:
            continue
        amounts[role] = amount
    return amounts


def _nearest_column_role(word: TextWord, columns: list[_ColumnRole]) -> str:
    if not columns:
        return "amount"
    return min(columns, key=lambda column: abs(column.x - word.x0)).role


def _line_text(line: _VisualLine) -> str:
    return clean_line(" ".join(word.text for word in line.words))


def _compact_line_key(line: _VisualLine) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_key(_line_text(line)))


def _label_text(line: _VisualLine, label_left: float, label_right: float) -> str:
    return clean_line(
        " ".join(
            word.text
            for word in line.words
            if label_left - 12 <= word.x0 <= label_right
        )
    )


def _date_from_visual_line(line: _VisualLine) -> str | None:
    for word in line.words:
        parsed = _date_from_text(word.text)
        if parsed:
            return parsed
    return None


def _date_from_text(text: str) -> str | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    return _normalize_date(match.group(0))


def _employee_code_from_visual_line(line: _VisualLine) -> str | None:
    for index, word in enumerate(line.words):
        key = normalize_key(word.text)
        if key not in {"medew code", "werknr", "werknr."}:
            continue
        for candidate in line.words[index + 1 : index + 5]:
            employee_code = _clean_employee_code(candidate.text)
            if employee_code:
                return employee_code
    return None


def _employee_code_from_line(line: str) -> str | None:
    match = EMPLOYEE_CODE_RE.search(line)
    if match:
        employee_code = _clean_employee_code(match.group("value") or line[match.end() :])
        if employee_code:
            return employee_code

    match = GENERIC_CODE_RE.search(line)
    if match:
        return _clean_employee_code(match.group("value"))
    return None


def _clean_employee_code(value: str) -> str | None:
    value = STOP_LABEL_RE.split(value)[0]
    value = clean_line(value.strip(" :-"))
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,24}", value)
    for token in tokens:
        cleaned = token.strip(" ._/-")
        if not cleaned or DATE_RE.fullmatch(cleaned):
            continue
        if not re.search(r"\d", cleaned):
            continue
        return cleaned
    return None


def _line_center(line: _VisualLine) -> float:
    if not line.words:
        return 0
    return (min(word.x0 for word in line.words) + max(word.x1 for word in line.words)) / 2


def _word_center(word: TextWord) -> float:
    return (word.x0 + word.x1) / 2


def _first_amount(role_amounts: dict[str, Decimal], roles: tuple[str, ...]) -> Decimal | None:
    for role in roles:
        if role in role_amounts:
            return role_amounts[role]
    return None


def _format_role_amounts(role_amounts: dict[str, Decimal]) -> str:
    return " ".join(f"{role}={amount}" for role, amount in role_amounts.items())


def _dedupe_columns(columns: list[_ColumnRole]) -> list[_ColumnRole]:
    deduped: list[_ColumnRole] = []
    seen: set[str] = set()
    for column in columns:
        if column.role in seen:
            continue
        seen.add(column.role)
        deduped.append(column)
    return deduped


def _loket_bottom_total(lines: list[_VisualLine]) -> Decimal | None:
    for line in reversed(lines):
        text = _line_text(line)
        if "UIT" not in text.upper() or "BETALEN" not in text.upper():
            continue
        amounts = [
            parse_amount(word.text)
            for word in line.words
            if AMOUNT_RE.fullmatch(word.text.strip())
        ]
        amounts = [amount for amount in amounts if amount is not None]
        if amounts:
            return amounts[-1]
    return None


def _skip_loket_label(label: str) -> bool:
    key = normalize_key(label)
    if not key:
        return True
    if key in {"tabel", "tarief", "specificatie", "opbouw", "tm periode"}:
        return True
    if key.startswith("uren ") or key in {"deeltijdfactor", "dgn soc verz"}:
        return True
    return False


def _split_page_on_multiple_identities(page: PageText) -> list[PageText]:
    lines = page.text.splitlines()
    starts: list[int] = []
    for index, line in enumerate(lines):
        if NAME_RE.search(line) and _nearby_birth_date(lines, index):
            starts.append(index)

    if len(starts) <= 1:
        return [page]

    chunks: list[PageText] = []
    starts.append(len(lines))
    for chunk_index in range(len(starts) - 1):
        start = starts[chunk_index]
        end = starts[chunk_index + 1]
        chunk_text = "\n".join(lines[start:end]).strip()
        chunk_words = [
            word
            for word in page.words
            if chunk_text and word.text in chunk_text
        ]
        if chunk_text:
            chunks.append(
                PageText(
                    page_number=page.page_number,
                    text=chunk_text,
                    method=f"{page.method}:split",
                    words=chunk_words,
                )
            )
    return chunks


def _nearby_birth_date(lines: list[str], index: int) -> bool:
    window = "\n".join(lines[index : index + 10])
    return bool(DOB_RE.search(window))


def _has_identity_marker(text: str) -> bool:
    return bool(extract_name(text) and extract_birth_date(text))


def _looks_like_person_name(value: str) -> bool:
    if not value:
        return False
    key = normalize_key(value)
    if len(key) < 3:
        return False
    if any(
        hint in key
        for hint in (
            "loonstrook",
            "salaris",
            "werkgever",
            "bedrijf",
            "gemaakt",
            "periode",
            "datum",
            "rekening",
            "betaling",
        )
    ):
        return False
    if re.fullmatch(r"[\d\s.,/-]+", value):
        return False
    return any(char.isalpha() for char in value)


def _normalize_person_prefix(value: str) -> str:
    value = re.sub(r"^(?:de heer|mevrouw|mw\.?|dhr\.?)\s+", "", value, flags=re.IGNORECASE)
    value = re.split(r"\s+(?:B\.?V\.?|N\.?V\.?)\b", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = clean_line(value)
    return value.title() if value.isupper() else value


def _normalize_date(value: str) -> str | None:
    parts = re.split(r"[-/.]", value.strip())
    if len(parts) != 3:
        return None
    day, month, year = parts
    if len(year) == 2:
        yy = int(year)
        current_yy = date.today().year % 100
        year = str(2000 + yy if yy <= current_yy else 1900 + yy)
    try:
        parsed = date(int(year), int(month), int(day))
    except ValueError:
        return None
    return parsed.isoformat()


def _month_from_text(text: str) -> str | None:
    match = MONTH_RE.search(text)
    if not match:
        return None
    return match.group(0).capitalize()


def _period_with_year(month: str, text: str) -> str:
    years = re.findall(r"\b(20\d{2}|19\d{2})\b", text)
    if years:
        return f"{month} {years[0]}"
    return month


def _skip_component_line(line: str) -> bool:
    key = normalize_key(line)
    if any(hint in key for hint in SKIP_COMPONENT_HINTS):
        return True
    if DOB_RE.search(line):
        return True
    return False


def _extract_component_code_and_label(prefix: str) -> tuple[str | None, str]:
    prefix = clean_line(prefix)
    match = re.match(r"^(?P<code>[A-Za-z]?\d{2,8}[A-Za-z]?)\s+(?P<label>.+)$", prefix)
    code = None
    label = prefix
    if match:
        code = match.group("code")
        label = match.group("label")

    label = re.sub(r"(?:\s+-?\d+(?:[,.]\d+)?%?)+$", "", label).strip(" :-")
    label = clean_line(label)
    return code, label


def _dominant_value(values: list[str | None]) -> str | None:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _duplicate_identity_keys(payslips: list[Payslip]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for payslip in payslips:
        if not payslip.identity_complete:
            continue
        if payslip.employee_code:
            continue
        identity = payslip.employee_code or normalize_key(payslip.employee_name or "")
        key = f"{identity}|{payslip.birth_date}|{normalize_period(payslip.period) or ''}"
        counts[key] = counts.get(key, 0) + 1
    return {key: count for key, count in counts.items() if count > 1}
