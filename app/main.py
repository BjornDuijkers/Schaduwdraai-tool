from __future__ import annotations

import os
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from .comparator import compare_documents, load_component_mappings
from .extractor import extract_pdf_text
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
            preview_components=result.components[:150],
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


if __name__ == "__main__":
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("SCHADUWDRAAI_PORT", "5057"))
    debug = os.environ.get("SCHADUWDRAAI_DEBUG") == "1"
    app.run(host="127.0.0.1", port=port, debug=debug)
