from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import (
    Component,
    ComponentComparison,
    ComponentMapping,
    ComparisonResult,
    EmployeeComparison,
    ParsedDocument,
    Payslip,
)
from .text_utils import normalize_key


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "instance" / "schaduwdraai.db"
REVIEW_STATUSES = {"open", "akkoord", "uitzoeken", "foutieve match", "exporteren als issue"}
ISSUE_STATUS = "exporteren als issue"


def db_path() -> Path:
    configured = os.environ.get("SCHADUWDRAAI_DB")
    return Path(configured) if configured else DEFAULT_DB_PATH


def init_db(path: Path | None = None) -> Path:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                document_a TEXT NOT NULL,
                document_b TEXT NOT NULL,
                doc_a_json TEXT NOT NULL,
                doc_b_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                tolerance TEXT NOT NULL DEFAULT '0.01',
                uploaded_mappings_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_project_column(connection, "tolerance", "TEXT NOT NULL DEFAULT '0.01'")
        _ensure_project_column(connection, "uploaded_mappings_json", "TEXT NOT NULL DEFAULT '[]'")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS review_items (
                project_id TEXT NOT NULL,
                deviation_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                note TEXT NOT NULL DEFAULT '',
                export_issue INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, deviation_id),
                FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS component_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical TEXT NOT NULL,
                source_a TEXT NOT NULL,
                source_b TEXT NOT NULL,
                key_a TEXT NOT NULL,
                key_b TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(key_a, key_b)
            )
            """
        )
        connection.commit()
    return target


def _ensure_project_column(connection: sqlite3.Connection, column_name: str, definition: str) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(projects)").fetchall()
    }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE projects ADD COLUMN {column_name} {definition}")


def save_project(
    project_id: str,
    doc_a: ParsedDocument,
    doc_b: ParsedDocument,
    result: ComparisonResult,
    *,
    tolerance: Decimal | str = Decimal("0.01"),
    uploaded_mappings: list[ComponentMapping] | None = None,
    path: Path | None = None,
) -> None:
    target = init_db(path)
    uploaded_mappings = uploaded_mappings or []
    with closing(sqlite3.connect(target)) as connection:
        connection.execute(
            """
            INSERT INTO projects (
                project_id, document_a, document_b, doc_a_json, doc_b_json, result_json,
                tolerance, uploaded_mappings_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(project_id) DO UPDATE SET
                document_a=excluded.document_a,
                document_b=excluded.document_b,
                doc_a_json=excluded.doc_a_json,
                doc_b_json=excluded.doc_b_json,
                result_json=excluded.result_json,
                tolerance=excluded.tolerance,
                uploaded_mappings_json=excluded.uploaded_mappings_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                project_id,
                doc_a.source_name,
                doc_b.source_name,
                json.dumps(_parsed_document_to_dict(doc_a), ensure_ascii=False),
                json.dumps(_parsed_document_to_dict(doc_b), ensure_ascii=False),
                json.dumps(_comparison_result_to_dict(result), ensure_ascii=False),
                str(tolerance),
                json.dumps([_component_mapping_to_dict(mapping) for mapping in uploaded_mappings], ensure_ascii=False),
            ),
        )
        connection.commit()


def load_project(
    project_id: str,
    *,
    path: Path | None = None,
) -> tuple[ParsedDocument, ParsedDocument, ComparisonResult] | None:
    target = init_db(path)
    with closing(sqlite3.connect(target)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT doc_a_json, doc_b_json, result_json FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    if row is None:
        return None

    doc_a = _parsed_document_from_dict(json.loads(row["doc_a_json"]))
    doc_b = _parsed_document_from_dict(json.loads(row["doc_b_json"]))
    result = _comparison_result_from_dict(json.loads(row["result_json"]))
    apply_review_items(result, load_review_items(project_id, path=target))
    return doc_a, doc_b, result


def load_project_settings(project_id: str, *, path: Path | None = None) -> dict[str, Any] | None:
    target = init_db(path)
    with closing(sqlite3.connect(target)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT tolerance, uploaded_mappings_json FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "tolerance": Decimal(str(row["tolerance"] or "0.01")),
        "uploaded_mappings": [
            _component_mapping_from_dict(item)
            for item in json.loads(row["uploaded_mappings_json"] or "[]")
        ],
    }


def load_review_items(project_id: str, *, path: Path | None = None) -> dict[str, dict[str, Any]]:
    target = init_db(path)
    with closing(sqlite3.connect(target)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT deviation_id, status, note, export_issue, updated_at
            FROM review_items
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchall()
    return {
        row["deviation_id"]: {
            "status": row["status"],
            "note": row["note"],
            "export_issue": bool(row["export_issue"]),
            "updated_at": row["updated_at"],
        }
        for row in rows
    }


def upsert_review_item(
    project_id: str,
    deviation_id: str,
    *,
    status: str,
    note: str,
    export_issue: bool,
    path: Path | None = None,
) -> dict[str, Any]:
    status = status.strip().lower() or "open"
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Onbekende reviewstatus: {status}.")
    export_issue = export_issue or status == ISSUE_STATUS

    target = init_db(path)
    with closing(sqlite3.connect(target)) as connection:
        connection.execute(
            """
            INSERT INTO review_items (project_id, deviation_id, status, note, export_issue, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(project_id, deviation_id) DO UPDATE SET
                status=excluded.status,
                note=excluded.note,
                export_issue=excluded.export_issue,
                updated_at=CURRENT_TIMESTAMP
            """,
            (project_id, deviation_id, status, note.strip(), int(export_issue)),
        )
        connection.commit()
    return load_review_items(project_id, path=target).get(deviation_id, {})


def apply_review_items(
    result: ComparisonResult,
    review_items: dict[str, dict[str, Any]],
) -> ComparisonResult:
    for component in result.components:
        review = review_items.get(component.deviation_id)
        if not review:
            continue
        component.review_status = review["status"]
        component.review_note = review["note"]
        component.export_issue = bool(review["export_issue"])
    return result


def save_component_alias(
    *,
    canonical: str,
    source_a: str,
    source_b: str,
    path: Path | None = None,
) -> dict[str, Any]:
    canonical = canonical.strip()
    source_a = source_a.strip()
    source_b = source_b.strip()
    if not source_a or not source_b:
        raise ValueError("Vul zowel component A als component B in.")
    if not canonical:
        canonical = source_a or source_b

    key_a = normalize_key(source_a)
    key_b = normalize_key(source_b)
    target = init_db(path)
    with closing(sqlite3.connect(target)) as connection:
        connection.execute(
            """
            INSERT INTO component_aliases (canonical, source_a, source_b, key_a, key_b, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key_a, key_b) DO UPDATE SET
                canonical=excluded.canonical,
                source_a=excluded.source_a,
                source_b=excluded.source_b,
                updated_at=CURRENT_TIMESTAMP
            """,
            (canonical, source_a, source_b, key_a, key_b),
        )
        connection.commit()
    aliases = list_component_aliases(path=target)
    for alias in aliases:
        if alias["key_a"] == key_a and alias["key_b"] == key_b:
            return alias
    return {
        "id": None,
        "canonical": canonical,
        "source_a": source_a,
        "source_b": source_b,
        "key_a": key_a,
        "key_b": key_b,
        "created_at": "",
        "updated_at": "",
    }


def list_component_aliases(path: Path | None = None) -> list[dict[str, Any]]:
    target = init_db(path)
    with closing(sqlite3.connect(target)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, canonical, source_a, source_b, key_a, key_b, created_at, updated_at
            FROM component_aliases
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
    return [
        {
            "id": row["id"],
            "canonical": row["canonical"],
            "source_a": row["source_a"],
            "source_b": row["source_b"],
            "key_a": row["key_a"],
            "key_b": row["key_b"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def delete_component_alias(alias_id: int, path: Path | None = None) -> bool:
    target = init_db(path)
    with closing(sqlite3.connect(target)) as connection:
        cursor = connection.execute(
            "DELETE FROM component_aliases WHERE id = ?",
            (alias_id,),
        )
        connection.commit()
    return cursor.rowcount > 0


def load_component_mappings_from_db(path: Path | None = None) -> list[ComponentMapping]:
    target = init_db(path)
    with closing(sqlite3.connect(target)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT canonical, source_a, source_b
            FROM component_aliases
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
    mappings: list[ComponentMapping] = []
    for row in rows:
        mappings.append(
            ComponentMapping(
                source_a=row["source_a"],
                source_b=row["source_b"],
                canonical=row["canonical"],
            )
        )
        mappings.append(
            ComponentMapping(
                source_a=row["source_b"],
                source_b=row["source_a"],
                canonical=row["canonical"],
            )
        )
    return mappings


def _parsed_document_to_dict(document: ParsedDocument) -> dict[str, Any]:
    return {
        "source": document.source,
        "source_name": document.source_name,
        "warnings": document.warnings,
        "period": document.period,
        "normalized_period": document.normalized_period,
        "provider": document.provider,
        "scenario": document.scenario,
        "payslips": [_payslip_to_dict(payslip) for payslip in document.payslips],
    }


def _parsed_document_from_dict(data: dict[str, Any]) -> ParsedDocument:
    return ParsedDocument(
        source=data["source"],
        source_name=data["source_name"],
        payslips=[_payslip_from_dict(item) for item in data.get("payslips", [])],
        warnings=list(data.get("warnings", [])),
        period=data.get("period"),
        normalized_period=data.get("normalized_period"),
        provider=data.get("provider"),
        scenario=data.get("scenario"),
    )


def _payslip_to_dict(payslip: Payslip) -> dict[str, Any]:
    return {
        "source": payslip.source,
        "source_name": payslip.source_name,
        "employee_name": payslip.employee_name,
        "birth_date": payslip.birth_date,
        "employee_code": payslip.employee_code,
        "period": payslip.period,
        "page_numbers": payslip.page_numbers,
        "warnings": payslip.warnings,
        "raw_text": payslip.raw_text,
        "components": [_component_to_dict(component) for component in payslip.components],
    }


def _payslip_from_dict(data: dict[str, Any]) -> Payslip:
    return Payslip(
        source=data["source"],
        source_name=data["source_name"],
        employee_name=data.get("employee_name"),
        birth_date=data.get("birth_date"),
        employee_code=data.get("employee_code"),
        period=data.get("period"),
        page_numbers=list(data.get("page_numbers", [])),
        components=[_component_from_dict(item) for item in data.get("components", [])],
        warnings=list(data.get("warnings", [])),
        raw_text=data.get("raw_text", ""),
    )


def _component_to_dict(component: Component) -> dict[str, Any]:
    return {
        "code": component.code,
        "label": component.label,
        "normalized_label": component.normalized_label,
        "amount": str(component.amount),
        "raw_line": component.raw_line,
    }


def _component_mapping_to_dict(mapping: ComponentMapping) -> dict[str, str]:
    return {
        "source_a": mapping.source_a,
        "source_b": mapping.source_b,
        "canonical": mapping.canonical,
    }


def _component_mapping_from_dict(data: dict[str, Any]) -> ComponentMapping:
    return ComponentMapping(
        source_a=str(data.get("source_a", "")),
        source_b=str(data.get("source_b", "")),
        canonical=str(data.get("canonical", "")),
    )


def _component_from_dict(data: dict[str, Any]) -> Component:
    return Component(
        code=data.get("code"),
        label=data["label"],
        normalized_label=data["normalized_label"],
        amount=Decimal(str(data["amount"])),
        raw_line=data["raw_line"],
    )


def _comparison_result_to_dict(result: ComparisonResult) -> dict[str, Any]:
    return {
        "employees": [_employee_to_dict(row) for row in result.employees],
        "components": [_component_comparison_to_dict(row) for row in result.components],
        "warnings": result.warnings,
        "summary": result.summary,
    }


def _comparison_result_from_dict(data: dict[str, Any]) -> ComparisonResult:
    return ComparisonResult(
        employees=[_employee_from_dict(row) for row in data.get("employees", [])],
        components=[_component_comparison_from_dict(row) for row in data.get("components", [])],
        warnings=list(data.get("warnings", [])),
        summary=dict(data.get("summary", {})),
    )


def _employee_to_dict(row: EmployeeComparison) -> dict[str, Any]:
    return {
        "status": row.status,
        "employee_name": row.employee_name,
        "birth_date": row.birth_date,
        "employee_code": row.employee_code,
        "source_a_pages": row.source_a_pages,
        "source_b_pages": row.source_b_pages,
        "match_note": row.match_note,
    }


def _employee_from_dict(data: dict[str, Any]) -> EmployeeComparison:
    return EmployeeComparison(
        status=data["status"],
        employee_name=data.get("employee_name"),
        birth_date=data.get("birth_date"),
        employee_code=data.get("employee_code"),
        source_a_pages=data.get("source_a_pages", ""),
        source_b_pages=data.get("source_b_pages", ""),
        match_note=data.get("match_note", ""),
    )


def _component_comparison_to_dict(row: ComponentComparison) -> dict[str, Any]:
    return {
        "employee_name": row.employee_name,
        "birth_date": row.birth_date,
        "employee_code": row.employee_code,
        "deviation_id": row.deviation_id,
        "canonical_component": row.canonical_component,
        "component_a": row.component_a,
        "amount_a": _decimal_to_json(row.amount_a),
        "component_b": row.component_b,
        "amount_b": _decimal_to_json(row.amount_b),
        "difference": _decimal_to_json(row.difference),
        "status": row.status,
        "pages_a": row.pages_a,
        "pages_b": row.pages_b,
        "review_status": row.review_status,
        "review_note": row.review_note,
        "export_issue": row.export_issue,
    }


def _component_comparison_from_dict(data: dict[str, Any]) -> ComponentComparison:
    return ComponentComparison(
        employee_name=data.get("employee_name"),
        birth_date=data.get("birth_date"),
        employee_code=data.get("employee_code"),
        deviation_id=data.get("deviation_id", ""),
        canonical_component=data["canonical_component"],
        component_a=data.get("component_a"),
        amount_a=_decimal_from_json(data.get("amount_a")),
        component_b=data.get("component_b"),
        amount_b=_decimal_from_json(data.get("amount_b")),
        difference=_decimal_from_json(data.get("difference")),
        status=data["status"],
        pages_a=data.get("pages_a", ""),
        pages_b=data.get("pages_b", ""),
        review_status=data.get("review_status", "open"),
        review_note=data.get("review_note", ""),
        export_issue=bool(data.get("export_issue", False)),
    )


def _decimal_to_json(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _decimal_from_json(value: str | None) -> Decimal | None:
    return Decimal(str(value)) if value not in (None, "") else None
