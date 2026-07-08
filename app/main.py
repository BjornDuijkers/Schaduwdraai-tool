from __future__ import annotations

import os
import uuid
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from .comparator import compare_documents, load_component_mappings
from .extractor import extract_pdf_text
from .models import ComponentComparison, ComparisonResult
from .parser import parse_document
from .report import create_excel_report


BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"
PROJECTS_DIR = INSTANCE_DIR / "projects"
ALLOWED_EXTENSIONS = {".pdf"}


app = Flask(__name__)
app.secret_key = os.environ.get("SCHADUWDRAAI_SECRET", "local-dev-only")
app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/compare")
def compare():
    project_id = uuid.uuid4().hex[:12]
    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        document_a = _save_pdf_upload("document_a", project_dir, "document_a")
        document_b = _save_pdf_upload("document_b", project_dir, "document_b")
        mapping_text = _read_optional_mapping()
        tolerance = _read_tolerance()

        extracted_a = extract_pdf_text(
            document_a,
            source_name=document_a.name,
            work_dir=project_dir,
            allow_ocr=True,
        )
        extracted_b = extract_pdf_text(
            document_b,
            source_name=document_b.name,
            work_dir=project_dir,
            allow_ocr=True,
        )
        parsed_a = parse_document(extracted_a, source="A")
        parsed_b = parse_document(extracted_b, source="B")
        mappings = load_component_mappings(mapping_text)
        result = compare_documents(
            parsed_a,
            parsed_b,
            mappings=mappings,
            tolerance=tolerance,
        )

        report_path = project_dir / "schaduwdraai_rapport.xlsx"
        create_excel_report(result, parsed_a, parsed_b, report_path)

        return render_template(
            "result.html",
            project_id=project_id,
            result=result,
            doc_a=parsed_a,
            doc_b=parsed_b,
            report_name=report_path.name,
            overview=_build_result_overview(result),
        )
    except Exception as exc:
        flash(str(exc))
        return redirect(url_for("index"))


@app.get("/download/<project_id>/<filename>")
def download(project_id: str, filename: str):
    safe_project = secure_filename(project_id)
    safe_filename = secure_filename(filename)
    directory = PROJECTS_DIR / safe_project
    return send_from_directory(directory, safe_filename, as_attachment=True)


@app.get("/health")
def health():
    return {"status": "ok"}


def _save_pdf_upload(field_name: str, project_dir: Path, prefix: str) -> Path:
    upload = request.files.get(field_name)
    if upload is None or not upload.filename:
        raise ValueError(f"Upload ontbreekt: {field_name}.")

    original_name = secure_filename(upload.filename)
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Alleen PDF-bestanden zijn toegestaan: {original_name}.")

    target = project_dir / f"{prefix}_{original_name}"
    upload.save(target)
    return target


def _read_optional_mapping() -> str | None:
    upload = request.files.get("mapping_csv")
    if upload and upload.filename:
        return upload.read().decode("utf-8-sig", errors="replace")
    textarea_value = request.form.get("mapping_text", "")
    return textarea_value if textarea_value.strip() else None


def _read_tolerance() -> Decimal:
    raw = request.form.get("tolerance", "0.01").strip().replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Tolerantie moet een getal zijn, bijvoorbeeld 0.01.") from exc


def _build_result_overview(result: ComparisonResult) -> dict[str, object]:
    component_groups: dict[tuple[str, str], list[ComponentComparison]] = defaultdict(list)
    for component in result.components:
        key = (component.employee_name or "", component.birth_date or "")
        component_groups[key].append(component)

    rows: list[dict[str, object]] = []
    clean_count = 0
    seen_keys: set[tuple[str, str]] = set()

    for employee in result.employees:
        key = (employee.employee_name or "", employee.birth_date or "")
        seen_keys.add(key)
        components = sorted(
            component_groups.get(key, []),
            key=lambda row: (row.status == "OK", row.canonical_component.lower()),
        )
        deviations = [component for component in components if component.status != "OK"]
        row = _overview_row(
            employee_name=employee.employee_name,
            birth_date=employee.birth_date,
            status=employee.status,
            match_note=employee.match_note,
            pages_a=employee.source_a_pages,
            pages_b=employee.source_b_pages,
            components=components,
            deviations=deviations,
        )

        if row["has_deviation"]:
            rows.append(row)
        else:
            clean_count += 1

    for key, components in component_groups.items():
        if key in seen_keys:
            continue
        deviations = [component for component in components if component.status != "OK"]
        row = _overview_row(
            employee_name=key[0],
            birth_date=key[1],
            status="MATCH",
            match_note="",
            pages_a=components[0].pages_a if components else "",
            pages_b=components[0].pages_b if components else "",
            components=components,
            deviations=deviations,
        )
        if row["has_deviation"]:
            rows.append(row)
        else:
            clean_count += 1

    return {
        "rows": rows,
        "clean_count": clean_count,
        "deviation_payslips": len(rows),
        "total_payslips": len(result.employees),
    }


def _overview_row(
    *,
    employee_name: str | None,
    birth_date: str | None,
    status: str,
    match_note: str,
    pages_a: str,
    pages_b: str,
    components: list[ComponentComparison],
    deviations: list[ComponentComparison],
) -> dict[str, object]:
    difference_total = sum(
        (component.difference for component in deviations if component.difference is not None),
        Decimal("0"),
    )
    deviation_labels = [
        _deviation_label(component)
        for component in deviations[:3]
    ]
    extra_count = max(len(deviations) - len(deviation_labels), 0)
    if extra_count:
        deviation_labels.append(f"+{extra_count} meer")

    has_deviation = status != "MATCH" or bool(deviations)
    status_counts = {
        "ok": sum(1 for component in components if component.status == "OK"),
        "verschil": sum(1 for component in components if component.status == "VERSCHIL"),
        "alleen_a": sum(1 for component in components if component.status == "ALLEEN_IN_A"),
        "alleen_b": sum(1 for component in components if component.status == "ALLEEN_IN_B"),
    }
    if status != "MATCH" and not deviations:
        deviation_labels.append(match_note or status)

    return {
        "employee_name": employee_name or "Onbekende medewerker",
        "birth_date": birth_date or "",
        "status": status,
        "match_note": match_note,
        "pages_a": pages_a,
        "pages_b": pages_b,
        "components": components,
        "deviations": deviations,
        "component_count": len(components),
        "deviation_count": len(deviations) if status == "MATCH" else max(len(deviations), 1),
        "difference_total": _format_amount(difference_total),
        "difference_total_raw": difference_total,
        "deviation_summary": "; ".join(deviation_labels) if deviation_labels else "Geen afwijkingen",
        "has_deviation": has_deviation,
        "status_counts": status_counts,
    }


def _deviation_label(component: ComponentComparison) -> str:
    if component.status == "VERSCHIL":
        return f"{component.canonical_component} ({_format_amount(component.difference)})"
    if component.status == "ALLEEN_IN_A":
        return f"{component.canonical_component} alleen A"
    if component.status == "ALLEEN_IN_B":
        return f"{component.canonical_component} alleen B"
    return component.canonical_component


def _format_amount(value: Decimal | None) -> str:
    if value is None:
        return ""
    sign = "-" if value < 0 else ""
    amount = abs(value)
    formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}{formatted}"


if __name__ == "__main__":
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("SCHADUWDRAAI_PORT", "5057"))
    debug = os.environ.get("SCHADUWDRAAI_DEBUG") == "1"
    app.run(host="127.0.0.1", port=port, debug=debug)
