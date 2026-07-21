from __future__ import annotations

import os
import re
import uuid
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from .comparator import compare_documents, load_component_mappings
from .extractor import extract_pdf_text
from .models import ComponentComparison, ComparisonResult
from .parser import normalize_period, parse_document
from .report import create_excel_report, create_issues_report
from .text_utils import normalize_key
from .storage import (
    REVIEW_STATUSES,
    delete_component_alias,
    init_db,
    list_component_aliases,
    list_projects,
    load_component_mappings_from_db,
    load_project,
    load_project_settings,
    save_component_alias,
    save_project,
    upsert_review_item,
)


BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"
PROJECTS_DIR = INSTANCE_DIR / "projects"
ALLOWED_EXTENSIONS = {".pdf"}


app = Flask(__name__)
app.secret_key = os.environ.get("SCHADUWDRAAI_SECRET", "local-dev-only")
app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024
init_db()


@app.get("/")
def index():
    projects = list_projects()
    requested_project_id = secure_filename(request.args.get("project_id", ""))
    project_id = requested_project_id or (projects[0]["project_id"] if projects else "")
    project = load_project(project_id) if project_id else None
    workspace = None
    if project:
        doc_a, doc_b, result = project
        workspace = _build_mapping_workspace(
            project_id,
            doc_a,
            doc_b,
            result,
            list_component_aliases(),
        )
    return render_template(
        "index.html",
        projects=projects,
        selected_project_id=project_id,
        mapping_workspace=workspace,
    )


@app.get("/upload")
def upload():
    return render_template("upload.html")


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
        _apply_document_overrides(parsed_a, "a")
        _apply_document_overrides(parsed_b, "b")

        uploaded_mappings = load_component_mappings(mapping_text)
        learned_mappings = load_component_mappings_from_db()
        mappings = learned_mappings + uploaded_mappings
        result = compare_documents(
            parsed_a,
            parsed_b,
            mappings=mappings,
            tolerance=tolerance,
        )
        save_project(
            project_id,
            parsed_a,
            parsed_b,
            result,
            tolerance=tolerance,
            uploaded_mappings=uploaded_mappings,
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
            review_statuses=sorted(REVIEW_STATUSES),
        )
    except Exception as exc:
        flash(str(exc))
        return redirect(url_for("upload"))


@app.get("/download/<project_id>/<filename>")
def download(project_id: str, filename: str):
    safe_project = secure_filename(project_id)
    safe_filename = secure_filename(filename)
    directory = PROJECTS_DIR / safe_project
    if safe_filename == "schaduwdraai_rapport.xlsx":
        project = load_project(safe_project)
        if project:
            doc_a, doc_b, result = project
            directory.mkdir(parents=True, exist_ok=True)
            create_excel_report(result, doc_a, doc_b, directory / safe_filename)
    return send_from_directory(directory, safe_filename, as_attachment=True)


@app.get("/download/<project_id>/issues.xlsx")
def download_issues(project_id: str):
    safe_project = secure_filename(project_id)
    project = load_project(safe_project)
    if not project:
        flash("Vergelijking niet gevonden.")
        return redirect(url_for("index"))

    doc_a, doc_b, result = project
    directory = PROJECTS_DIR / safe_project
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / "issues.xlsx"
    create_issues_report(result, doc_a, doc_b, output_path)
    return send_from_directory(directory, output_path.name, as_attachment=True)


@app.get("/review/<project_id>")
def review(project_id: str):
    safe_project = secure_filename(project_id)
    project = load_project(safe_project)
    if not project:
        flash("Vergelijking niet gevonden.")
        return redirect(url_for("index"))

    doc_a, doc_b, result = project
    return render_template(
        "review.html",
        project_id=safe_project,
        result=result,
        doc_a=doc_a,
        doc_b=doc_b,
        overview=_build_result_overview(result),
        component_options=_component_options(result),
        component_inventory=_component_inventory(result),
        mapping_candidates=_mapping_candidates(result),
        learned_aliases=list_component_aliases(),
        review_statuses=["open", "akkoord", "uitzoeken", "foutieve match", "exporteren als issue"],
    )


@app.post("/api/projects/<project_id>/review/<deviation_id>")
def update_review(project_id: str, deviation_id: str):
    safe_project = secure_filename(project_id)
    if not load_project(safe_project):
        return jsonify({"ok": False, "error": "Vergelijking niet gevonden."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        review = upsert_review_item(
            safe_project,
            deviation_id,
            status=str(payload.get("status", "open")),
            note=str(payload.get("note", "")),
            export_issue=_payload_bool(payload.get("export_issue")),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "review": review})


@app.post("/api/component-aliases")
def create_component_alias():
    payload = request.get_json(silent=True) or {}
    try:
        alias = save_component_alias(
            canonical=str(payload.get("canonical", "")),
            source_a=str(payload.get("source_a", "")),
            source_b=str(payload.get("source_b", "")),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "alias": alias})


@app.post("/api/projects/<project_id>/component-aliases")
def create_project_component_alias(project_id: str):
    safe_project = secure_filename(project_id)
    project = load_project(safe_project)
    if not project:
        return jsonify({"ok": False, "error": "Vergelijking niet gevonden."}), 404

    payload = request.get_json(silent=True) or {}
    try:
        alias = save_component_alias(
            canonical=str(payload.get("canonical", "")),
            source_a=str(payload.get("source_a", "")),
            source_b=str(payload.get("source_b", "")),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    result = _recalculate_project(safe_project, project)
    return jsonify({"ok": True, "alias": alias, "reload": True, "summary": result.summary})


@app.post("/api/projects/<project_id>/component-aliases/batch")
def create_project_component_aliases_batch(project_id: str):
    safe_project = secure_filename(project_id)
    project = load_project(safe_project)
    if not project:
        return jsonify({"ok": False, "error": "Vergelijking niet gevonden."}), 404

    payload = request.get_json(silent=True) or {}
    requested_mappings = payload.get("mappings", [])
    if not isinstance(requested_mappings, list) or not requested_mappings:
        return jsonify({"ok": False, "error": "Geen mappings ontvangen."}), 400
    if len(requested_mappings) > 100:
        return jsonify({"ok": False, "error": "Maximaal 100 mappings per keer."}), 400

    aliases = []
    seen_pairs: set[tuple[str, str]] = set()
    try:
        for item in requested_mappings:
            if not isinstance(item, dict):
                raise ValueError("Ongeldige mapping ontvangen.")
            source_a = str(item.get("source_a", "")).strip()
            source_b = str(item.get("source_b", "")).strip()
            pair = (normalize_key(source_a), normalize_key(source_b))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            aliases.append(
                save_component_alias(
                    canonical=str(item.get("canonical", "")),
                    source_a=source_a,
                    source_b=source_b,
                )
            )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    result = _recalculate_project(safe_project, project)
    return jsonify(
        {
            "ok": True,
            "aliases": aliases,
            "saved": len(aliases),
            "reload": True,
            "summary": result.summary,
        }
    )


def _recalculate_project(project_id: str, project) -> ComparisonResult:
    doc_a, doc_b, _old_result = project
    settings = load_project_settings(project_id) or {
        "tolerance": Decimal("0.01"),
        "uploaded_mappings": [],
    }
    uploaded_mappings = settings["uploaded_mappings"]
    tolerance = settings["tolerance"]
    mappings = load_component_mappings_from_db() + uploaded_mappings
    result = compare_documents(doc_a, doc_b, mappings=mappings, tolerance=tolerance)
    save_project(
        project_id,
        doc_a,
        doc_b,
        result,
        tolerance=tolerance,
        uploaded_mappings=uploaded_mappings,
    )
    return result


@app.delete("/api/component-aliases/<int:alias_id>")
def remove_component_alias(alias_id: int):
    deleted = delete_component_alias(alias_id)
    if not deleted:
        return jsonify({"ok": False, "error": "Mapping niet gevonden."}), 404
    return jsonify({"ok": True, "deleted": alias_id})


@app.get("/health")
def health():
    return {"status": "ok"}


def _apply_document_overrides(document, suffix: str) -> None:
    period = request.form.get(f"period_{suffix}", "").strip()
    provider = request.form.get(f"provider_{suffix}", "").strip()
    scenario = request.form.get(f"scenario_{suffix}", "").strip()
    if period:
        document.period = period
        document.normalized_period = normalize_period(period)
    if provider:
        document.provider = provider
    if scenario:
        document.scenario = scenario


def _payload_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "ja", "yes", "on"}
    return bool(value)


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
    component_groups: dict[tuple[str, str, str], list[ComponentComparison]] = defaultdict(list)
    for component in result.components:
        key = (component.employee_name or "", component.birth_date or "", component.employee_code or "")
        component_groups[key].append(component)

    rows: list[dict[str, object]] = []
    clean_count = 0
    seen_keys: set[tuple[str, str, str]] = set()

    for employee in result.employees:
        key = (employee.employee_name or "", employee.birth_date or "", employee.employee_code or "")
        seen_keys.add(key)
        components = sorted(
            component_groups.get(key, []),
            key=lambda row: (row.status == "OK", row.canonical_component.lower()),
        )
        deviations = [component for component in components if component.status != "OK"]
        row = _overview_row(
            employee_name=employee.employee_name,
            birth_date=employee.birth_date,
            employee_code=employee.employee_code,
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
            employee_code=key[2],
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


def _component_options(result: ComparisonResult) -> dict[str, list[str]]:
    return {
        "a": sorted({row.component_a for row in result.components if row.component_a}),
        "b": sorted({row.component_b for row in result.components if row.component_b}),
        "canonical": sorted({row.canonical_component for row in result.components if row.canonical_component}),
    }


def _component_inventory(result: ComparisonResult) -> dict[str, object]:
    document_a = _inventory_for_source(result.components, "A")
    document_b = _inventory_for_source(result.components, "B")
    unmatched_a = [item for item in document_a if item["status"] == "Geen match"]
    unmatched_b = [item for item in document_b if item["status"] == "Geen match"]
    return {
        "document_a": document_a,
        "document_b": document_b,
        "unmatched_a": unmatched_a,
        "unmatched_b": unmatched_b,
        "dropdown_options_b": unmatched_b,
        "all_options_b": document_b,
    }


def _build_mapping_workspace(
    project_id: str,
    doc_a,
    doc_b,
    result: ComparisonResult,
    aliases: list[dict[str, object]],
) -> dict[str, object]:
    inventory = _component_inventory(result)
    source_components = inventory["document_a"]
    target_components = inventory["document_b"]
    source_by_key = {normalize_key(str(item["component"])): item for item in source_components}
    target_by_key = {normalize_key(str(item["component"])): item for item in target_components}

    relations: dict[tuple[str, str], dict[str, object]] = {}
    for alias in aliases:
        alias_a = str(alias["source_a"])
        alias_b = str(alias["source_b"])
        key_a = normalize_key(alias_a)
        key_b = normalize_key(alias_b)
        if key_a in source_by_key and key_b in target_by_key:
            source_name, target_name = alias_a, alias_b
        elif key_b in source_by_key and key_a in target_by_key:
            source_name, target_name = alias_b, alias_a
            key_a, key_b = key_b, key_a
        else:
            continue
        relations[(key_a, key_b)] = {
            "source": source_name,
            "target": target_name,
            "canonical": str(alias["canonical"]),
            "status": "matched",
            "confidence": 1.0,
            "recommended": False,
            "alias_id": alias["id"],
        }

    suggested_pairs: dict[tuple[str, str], dict[str, object]] = {}
    for row in result.components:
        if not row.component_a or not row.component_b:
            continue
        key = (normalize_key(row.component_a), normalize_key(row.component_b))
        if key in relations:
            continue
        source_item = source_by_key.get(key[0])
        target_item = target_by_key.get(key[1])
        if not source_item or not target_item:
            continue
        suggestion = suggested_pairs.setdefault(
            key,
            {
                "source": row.component_a,
                "target": row.component_b,
                "canonical": row.canonical_component or row.component_a,
                "occurrences": 0,
            },
        )
        suggestion["occurrences"] = int(suggestion["occurrences"]) + 1

    for key, suggestion in suggested_pairs.items():
        source_item = source_by_key[key[0]]
        target_item = target_by_key[key[1]]
        confidence = _mapping_confidence(source_item, target_item, direct_pair=True)
        relations[key] = {
            "source": suggestion["source"],
            "target": suggestion["target"],
            "canonical": suggestion["canonical"],
            "status": "proposed" if confidence >= 0.72 else "review",
            "confidence": confidence,
            "recommended": confidence >= 0.72,
            "alias_id": None,
        }

    related_sources = {key[0] for key in relations}
    related_targets = {key[1] for key in relations}
    free_targets = [key for key in target_by_key if key not in related_targets]
    for source_key, source_item in source_by_key.items():
        if source_key in related_sources or not free_targets:
            continue
        ranked = sorted(
            (
                (_mapping_confidence(source_item, target_by_key[target_key]), target_key)
                for target_key in free_targets
            ),
            reverse=True,
        )
        confidence, target_key = ranked[0]
        if confidence < 0.58:
            continue
        target_item = target_by_key[target_key]
        relations[(source_key, target_key)] = {
            "source": source_item["component"],
            "target": target_item["component"],
            "canonical": source_item["canonical_suggestion"],
            "status": "proposed" if confidence >= 0.72 else "review",
            "confidence": confidence,
            "recommended": confidence >= 0.72,
            "alias_id": None,
        }
        free_targets.remove(target_key)

    relation_values = list(relations.values())
    source_relations: dict[str, list[dict[str, object]]] = defaultdict(list)
    target_relations: dict[str, list[dict[str, object]]] = defaultdict(list)
    for relation in relation_values:
        source_relations[normalize_key(str(relation["source"]))].append(relation)
        target_relations[normalize_key(str(relation["target"]))].append(relation)

    def component_payload(item: dict[str, object], side: str) -> dict[str, object]:
        relation_list = (
            source_relations if side == "source" else target_relations
        ).get(normalize_key(str(item["component"])), [])
        statuses = {str(relation["status"]) for relation in relation_list}
        if "matched" in statuses:
            mapping_status = "matched"
        elif "proposed" in statuses:
            mapping_status = "proposed"
        elif "review" in statuses:
            mapping_status = "review"
        else:
            mapping_status = "unmapped"
        return {
            "name": item["component"],
            "code": item["code"],
            "count": item["count"],
            "total": item["total"],
            "comparison_status": item["status"],
            "mapping_status": mapping_status,
        }

    mapped_sources = {
        normalize_key(str(relation["source"]))
        for relation in relation_values
        if relation["status"] == "matched"
    }
    proposed_sources = {
        normalize_key(str(relation["source"]))
        for relation in relation_values
        if relation["status"] == "proposed"
    } - mapped_sources
    review_sources = {
        normalize_key(str(relation["source"]))
        for relation in relation_values
        if relation["status"] == "review"
    } - mapped_sources - proposed_sources
    total_sources = len(source_components)
    unmapped_count = max(total_sources - len(mapped_sources | proposed_sources | review_sources), 0)
    progress = round((len(mapped_sources) / total_sources) * 100) if total_sources else 0

    return {
        "project": {
            "id": project_id,
            "source_a": _document_display_name(doc_a),
            "source_b": _document_display_name(doc_b),
            "provider_a": doc_a.provider or "Systeem 1",
            "provider_b": doc_b.provider or "Systeem 2",
        },
        "source": [component_payload(item, "source") for item in source_components],
        "target": [component_payload(item, "target") for item in target_components],
        "relations": relation_values,
        "stats": {
            "progress": progress,
            "mapped": len(mapped_sources),
            "proposed": len(proposed_sources),
            "review": len(review_sources),
            "unmapped": unmapped_count,
            "total": total_sources,
        },
    }


def _mapping_confidence(
    source_item: dict[str, object],
    target_item: dict[str, object],
    *,
    direct_pair: bool = False,
) -> float:
    source_name = normalize_key(str(source_item["component"]))
    target_name = normalize_key(str(target_item["component"]))
    name_score = SequenceMatcher(None, source_name, target_name).ratio()
    count_score = 1.0 if source_item["count"] == target_item["count"] else 0.0
    source_total = abs(Decimal(source_item["total_raw"]))
    target_total = abs(Decimal(target_item["total_raw"]))
    largest_total = max(source_total, target_total, Decimal("0.01"))
    amount_score = float(max(Decimal("0"), Decimal("1") - abs(source_total - target_total) / largest_total))
    score = (name_score * 0.55) + (count_score * 0.20) + (amount_score * 0.25)
    if direct_pair:
        score += 0.12
    return round(min(score, 0.99), 2)


def _document_display_name(document) -> str:
    name = Path(document.source_name).stem
    name = re.sub(r"^document_[ab]_", "", name, flags=re.IGNORECASE)
    name = name.replace("_", " ").strip()
    return name or document.source


def _inventory_for_source(
    components: list[ComponentComparison],
    source: str,
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for component in components:
        label = component.component_a if source == "A" else component.component_b
        amount = component.amount_a if source == "A" else component.amount_b
        counterpart = component.component_b if source == "A" else component.component_a
        if not label:
            continue

        key = (normalize_key(label), label)
        item = grouped.setdefault(
            key,
            {
                "component": label,
                "code": _component_code(label),
                "count": 0,
                "total_raw": Decimal("0"),
                "statuses": set(),
                "counterparts": set(),
                "canonical": set(),
            },
        )
        item["count"] += 1
        if amount is not None:
            item["total_raw"] += amount
        item["statuses"].add(component.status)
        if counterpart:
            item["counterparts"].add(counterpart)
        if component.canonical_component:
            item["canonical"].add(component.canonical_component)

    rows: list[dict[str, object]] = []
    for item in grouped.values():
        statuses = item["statuses"]
        rows.append(
            {
                "component": item["component"],
                "code": item["code"],
                "count": item["count"],
                "total": _format_amount(item["total_raw"]),
                "total_raw": item["total_raw"],
                "status": _inventory_status(statuses),
                "counterpart": ", ".join(sorted(item["counterparts"])) or "-",
                "canonical": ", ".join(sorted(item["canonical"])) or "-",
                "canonical_suggestion": _mapping_canonical_suggestion(str(item["component"]), ""),
            }
        )
    return sorted(rows, key=lambda item: (item["status"] != "Geen match", str(item["component"]).lower()))


def _inventory_status(statuses: set[str]) -> str:
    if len(statuses) != 1:
        return "Gemengd"
    status = next(iter(statuses))
    if status == "OK":
        return "Gematcht"
    if status == "VERSCHIL":
        return "Verschil"
    if status in {"ALLEEN_IN_A", "ALLEEN_IN_B"}:
        return "Geen match"
    return "Gemengd"


def _component_code(label: str) -> str:
    match = re.match(r"^(?P<code>[A-Za-z]?\d{2,8}[A-Za-z]?)\s+", label)
    return match.group("code") if match else ""


def _mapping_candidates(result: ComparisonResult) -> list[dict[str, str]]:
    groups: dict[tuple[str, str, str], dict[str, object]] = defaultdict(
        lambda: {"a": set(), "b": set(), "employee_name": "", "birth_date": "", "employee_code": ""}
    )
    for row in result.components:
        key = (row.employee_name or "", row.birth_date or "", row.employee_code or "")
        group = groups[key]
        group["employee_name"] = row.employee_name or ""
        group["birth_date"] = row.birth_date or ""
        group["employee_code"] = row.employee_code or ""
        if row.status == "ALLEEN_IN_A" and row.component_a:
            group["a"].add(row.component_a)
        elif row.status == "ALLEEN_IN_B" and row.component_b:
            group["b"].add(row.component_b)

    candidates: list[dict[str, str]] = []
    for group in groups.values():
        source_a_values = sorted(group["a"])
        source_b_values = sorted(group["b"])
        for source_a in source_a_values:
            for source_b in source_b_values:
                candidates.append(
                    {
                        "employee_name": str(group["employee_name"]),
                        "birth_date": str(group["birth_date"]),
                        "employee_code": str(group["employee_code"]),
                        "source_a": source_a,
                        "source_b": source_b,
                        "canonical": _mapping_canonical_suggestion(source_a, source_b),
                    }
                )
    return candidates[:100]


def _mapping_canonical_suggestion(source_a: str, source_b: str) -> str:
    candidate = source_a or source_b
    return re.sub(r"^[A-Za-z]?\d{2,8}[A-Za-z]?\s+", "", candidate).strip() or candidate


def _overview_row(
    *,
    employee_name: str | None,
    birth_date: str | None,
    employee_code: str | None,
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

    component_options_a = sorted({component.component_a for component in components if component.component_a})
    component_options_b = sorted({component.component_b for component in components if component.component_b})

    return {
        "employee_name": employee_name or "Onbekende medewerker",
        "birth_date": birth_date or "",
        "employee_code": employee_code or "",
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
        "component_options_a": component_options_a,
        "component_options_b": component_options_b,
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
