from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import fitz

from app.main import app


PDF_A = """
Naam: Jan Jansen
Geboortedatum: 01-02-1980
1000 Bruto salaris 3.000,00
Netto loon 2.250,00
"""

PDF_B = """
Naam: Jan Jansen
Geboortedatum: 01-02-1980
1000 Bruto salaris 3.000,00
Netto loon 2.240,00
"""


class UploadFlowTest(unittest.TestCase):
    def test_compare_route_creates_report_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_a = Path(tmp) / "a.pdf"
            pdf_b = Path(tmp) / "b.pdf"
            _write_pdf(pdf_a, PDF_A)
            _write_pdf(pdf_b, PDF_B)

            client = app.test_client()
            response = client.post(
                "/compare",
                data={
                    "document_a": (io.BytesIO(pdf_a.read_bytes()), "a.pdf"),
                    "document_b": (io.BytesIO(pdf_b.read_bytes()), "b.pdf"),
                    "tolerance": "0.01",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Download Excelrapport", response.data)
        self.assertIn(b"verschillen", response.data)


def _write_pdf(path: Path, text: str) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    document.save(path)
    document.close()


if __name__ == "__main__":
    unittest.main()
