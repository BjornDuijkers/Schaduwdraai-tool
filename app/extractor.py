from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which

from .models import ExtractionResult, PageText, TextWord


def extract_pdf_text(
    pdf_path: Path,
    *,
    source_name: str,
    work_dir: Path,
    allow_ocr: bool = True,
    ocr_language: str = "nld+eng",
) -> ExtractionResult:
    pages, warnings = _extract_text_layer(pdf_path)
    total_chars = sum(len(page.text.strip()) for page in pages)

    if total_chars >= 80:
        return ExtractionResult(
            source_name=source_name,
            pages=pages,
            warnings=warnings,
            text_layer_present=True,
        )

    warnings.append(
        "Er is weinig of geen tekstlaag gevonden. Dit document lijkt een scan of afbeelding-PDF."
    )

    if allow_ocr:
        ocr_pages, ocr_warning = _extract_with_ocrmypdf(
            pdf_path, work_dir=work_dir, language=ocr_language
        )
        if ocr_pages:
            warnings.append("OCR uitgevoerd met OCRmyPDF.")
            return ExtractionResult(
                source_name=source_name,
                pages=ocr_pages,
                warnings=warnings,
                text_layer_present=False,
            )

        rapid_pages, rapid_warning = _extract_with_rapidocr(pdf_path, work_dir=work_dir)
        if rapid_pages:
            warnings.append("OCR uitgevoerd met RapidOCR.")
            return ExtractionResult(
                source_name=source_name,
                pages=rapid_pages,
                warnings=warnings,
                text_layer_present=False,
            )
        if ocr_warning:
            warnings.append(ocr_warning)
        if rapid_warning:
            warnings.append(rapid_warning)

    warnings.append(
        "Geen bruikbare tekst gevonden. Installeer RapidOCR of OCRmyPDF/Tesseract voor gescande PDF's."
    )
    return ExtractionResult(
        source_name=source_name,
        pages=pages,
        warnings=warnings,
        text_layer_present=False,
    )


def _extract_text_layer(pdf_path: Path) -> tuple[list[PageText], list[str]]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF ontbreekt. Installeer dependencies met: python -m pip install -r requirements.txt"
        ) from exc

    warnings: list[str] = []
    pages: list[PageText] = []

    try:
        with fitz.open(pdf_path) as document:
            for index, page in enumerate(document, start=1):
                text = page.get_text("text") or ""
                words = [
                    TextWord(
                        x0=float(word[0]),
                        y0=float(word[1]),
                        x1=float(word[2]),
                        y1=float(word[3]),
                        text=str(word[4]),
                    )
                    for word in page.get_text("words")
                ]
                pages.append(
                    PageText(page_number=index, text=text, method="text", words=words)
                )
    except Exception as exc:  # PyMuPDF raises several document-specific errors.
        raise RuntimeError(f"PDF kon niet gelezen worden: {pdf_path.name}: {exc}") from exc

    if not pages:
        warnings.append("PDF bevat geen pagina's.")
    return pages, warnings


def _extract_with_ocrmypdf(
    pdf_path: Path, *, work_dir: Path, language: str
) -> tuple[list[PageText], str | None]:
    executable = which("ocrmypdf")
    if not executable:
        return [], "OCRmyPDF is niet gevonden op PATH; OCR is overgeslagen."

    sidecar = work_dir / f"{pdf_path.stem}.ocr.txt"
    output_pdf = work_dir / f"{pdf_path.stem}.ocr.pdf"
    command = [
        executable,
        "--force-ocr",
        "--sidecar",
        str(sidecar),
        "-l",
        language,
        str(pdf_path),
        str(output_pdf),
    ]

    try:
        completed = subprocess.run(
            command,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except Exception as exc:
        return [], f"OCR kon niet gestart worden: {exc}"

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        return [], f"OCRmyPDF gaf een fout terug: {stderr[:500]}"

    if not sidecar.exists():
        return [], "OCRmyPDF is uitgevoerd, maar leverde geen sidecar-tekstbestand op."

    text = sidecar.read_text(encoding="utf-8", errors="replace")
    page_texts = text.split("\f")
    pages = [
        PageText(page_number=index, text=page_text, method="ocr")
        for index, page_text in enumerate(page_texts, start=1)
        if page_text.strip()
    ]
    return pages, None


def _extract_with_rapidocr(
    pdf_path: Path, *, work_dir: Path
) -> tuple[list[PageText], str | None]:
    try:
        import fitz
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return [], "RapidOCR is niet geinstalleerd; OCR-fallback is overgeslagen."

    work_dir.mkdir(parents=True, exist_ok=True)
    ocr = RapidOCR()
    pages: list[PageText] = []

    try:
        with fitz.open(pdf_path) as document:
            for page_index, page in enumerate(document, start=1):
                image_path = work_dir / f"{pdf_path.stem}.rapidocr.page-{page_index}.png"
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                pixmap.save(image_path)
                result, _elapsed = ocr(str(image_path))
                words = _rapidocr_words(result)
                text = _words_to_line_text(words)
                if text.strip():
                    pages.append(
                        PageText(
                            page_number=page_index,
                            text=text,
                            method="rapidocr",
                            words=words,
                        )
                    )
    except Exception as exc:
        return [], f"RapidOCR kon het document niet lezen: {exc}"

    if not pages:
        return [], "RapidOCR leverde geen tekst op."
    return pages, None


def _rapidocr_words(result: object) -> list[TextWord]:
    words: list[TextWord] = []
    if not result:
        return words

    for item in result:
        try:
            box, text, _score = item
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
        except Exception:
            continue
        if not str(text).strip():
            continue
        words.append(
            TextWord(
                x0=min(xs),
                y0=min(ys),
                x1=max(xs),
                y1=max(ys),
                text=str(text).strip(),
            )
        )
    return words


def _words_to_line_text(words: list[TextWord]) -> str:
    lines: list[list[TextWord]] = []
    for word in sorted(words, key=lambda item: (item.y0, item.x0)):
        if lines and abs(lines[-1][0].y0 - word.y0) <= 8:
            lines[-1].append(word)
        else:
            lines.append([word])

    return "\n".join(
        " ".join(word.text for word in sorted(line, key=lambda item: item.x0))
        for line in lines
    )
