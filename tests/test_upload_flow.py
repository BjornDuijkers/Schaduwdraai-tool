from __future__ import annotations

import io
import os
import re
import tempfile
import unittest
from pathlib import Path

import fitz

from app.main import app
from app.storage import list_component_aliases, load_project


PDF_A = """
Naam: Jan Jansen
Medewerkernummer: 1001
Geboortedatum: 01-02-1980
1000 Bruto salaris 3.000,00
Vakantiegeld 250,00
Netto loon 2.250,00
"""

PDF_B = """
Naam: Jan Jansen
Medewerkernummer: 2002
Geboortedatum: 01-02-1980
1000 Bruto salaris 3.000,00
Reservering vakantietoeslag 250,00
Netto loon 2.240,00
"""


class UploadFlowTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("SCHADUWDRAAI_DB", None)

    def test_compare_route_creates_report_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SCHADUWDRAAI_DB"] = str(Path(tmp) / "test.db")
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
        self.assertIn(b"Review verschillen", response.data)
        self.assertIn(b"verschillen", response.data)
        html = response.data.decode("utf-8")
        self.assertGreater(html.index("Waarschuwingen"), html.index("Afwijkingen per loonstrook"))
        self.assertGreater(html.index("Waarschuwingen"), html.index("Nieuwe vergelijking"))

    def test_mapping_page_loads_project_and_batch_mapping_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SCHADUWDRAAI_DB"] = str(Path(tmp) / "test.db")
            pdf_a = Path(tmp) / "a.pdf"
            pdf_b = Path(tmp) / "b.pdf"
            _write_pdf(pdf_a, PDF_A)
            _write_pdf(pdf_b, PDF_B)

            client = app.test_client()
            comparison = client.post(
                "/compare",
                data={
                    "document_a": (io.BytesIO(pdf_a.read_bytes()), "a.pdf"),
                    "document_b": (io.BytesIO(pdf_b.read_bytes()), "b.pdf"),
                    "tolerance": "0.01",
                },
                content_type="multipart/form-data",
            )
            project_match = re.search(rb"/review/([a-f0-9]{12})", comparison.data)
            self.assertIsNotNone(project_match)
            project_id = project_match.group(1).decode("ascii")

            mapping_page = client.get(f"/?project_id={project_id}")
            self.assertEqual(mapping_page.status_code, 200)
            self.assertIn(b"Looncomponent mapping", mapping_page.data)
            self.assertIn(project_id.encode("ascii"), mapping_page.data)
            self.assertIn(b"Vakantiegeld", mapping_page.data)
            self.assertIn(b"mapping.js", mapping_page.data)

            batch = client.post(
                f"/api/projects/{project_id}/component-aliases/batch",
                json={
                    "mappings": [
                        {
                            "canonical": "Vakantiegeld",
                            "source_a": "Vakantiegeld",
                            "source_b": "Reservering vakantietoeslag",
                        }
                    ]
                },
            )
            self.assertEqual(batch.status_code, 200)
            self.assertTrue(batch.json["ok"])
            self.assertEqual(batch.json["saved"], 1)
            aliases = list_component_aliases()
            self.assertEqual(len(aliases), 1)
            self.assertEqual(aliases[0]["canonical"], "Vakantiegeld")

    def test_review_api_and_issue_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SCHADUWDRAAI_DB"] = str(Path(tmp) / "test.db")
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

            project_match = re.search(rb"/review/([a-f0-9]{12})", response.data)
            self.assertIsNotNone(project_match)
            project_id = project_match.group(1).decode("ascii")
            project = load_project(project_id)
            self.assertIsNotNone(project)
            _doc_a, _doc_b, result = project
            deviation = next(row for row in result.components if row.status != "OK")

            update = client.post(
                f"/api/projects/{project_id}/review/{deviation.deviation_id}",
                json={
                    "status": "exporteren als issue",
                    "note": "Controleer nettoloon.",
                    "export_issue": True,
                },
            )
            self.assertEqual(update.status_code, 200)
            self.assertTrue(update.json["ok"])

            alias_response = client.post(
                "/api/component-aliases",
                json={
                    "canonical": "Vakantiegeld",
                    "source_a": "Vakantiegeld",
                    "source_b": "Reservering vakantietoeslag",
                },
            )
            self.assertEqual(alias_response.status_code, 200)
            alias_id = alias_response.json["alias"]["id"]

            review_response = client.get(f"/review/{project_id}")
            self.assertEqual(review_response.status_code, 200)
            self.assertIn(b"Controleer nettoloon.", review_response.data)
            html = review_response.data.decode("utf-8")
            self.assertGreater(html.index("Waarschuwingen"), html.index("Reviewregels"))
            self.assertIn('role="tablist"', html)
            self.assertIn('id="tabpanel-review"', html)
            self.assertIn('id="tabpanel-mapping"', html)
            self.assertIn("Componentmapping leren", html)
            self.assertGreater(html.index("Componentmapping leren"), html.index("Reviewregels"))
            self.assertIn("Ongekoppelde componenten koppelen", html)
            self.assertIn("Alle gevonden componenten", html)
            self.assertIn('select name="source_b"', html)
            self.assertIn("Suggesties uit deze vergelijking", html)
            self.assertIn("Gebruik als mapping", html)
            self.assertIn("Reservering vakantietoeslag", html)
            self.assertNotIn('select name="status"', html)
            self.assertIn('data-status-value="akkoord"', html)
            self.assertIn('data-status-value="exporteren als issue"', html)

            alias_response = client.post(
                f"/api/projects/{project_id}/component-aliases",
                json={
                    "canonical": "Vakantiegeld",
                    "source_a": "Vakantiegeld",
                    "source_b": "Reservering vakantietoeslag",
                },
            )
            self.assertEqual(alias_response.status_code, 200)
            self.assertTrue(alias_response.json["reload"])
            alias_id = alias_response.json["alias"]["id"]

            project_after_mapping = load_project(project_id)
            self.assertIsNotNone(project_after_mapping)
            _mapped_a, _mapped_b, mapped_result = project_after_mapping
            self.assertEqual(mapped_result.summary["componenten_alleen_in_a"], 0)
            self.assertEqual(mapped_result.summary["componenten_alleen_in_b"], 0)

            styles = Path("app/static/styles.css").read_text(encoding="utf-8")
            self.assertIn('.status-button[data-status-value="open"]', styles)
            self.assertIn('.status-button[data-status-value="akkoord"]', styles)
            self.assertIn('.status-button[data-status-value="uitzoeken"]', styles)
            self.assertIn('.status-button[data-status-value="foutieve match"]', styles)
            self.assertIn('.status-button[data-status-value="exporteren als issue"]', styles)

            delete_response = client.delete(f"/api/component-aliases/{alias_id}")
            self.assertEqual(delete_response.status_code, 200)
            self.assertEqual(list_component_aliases(), [])

            issues_response = client.get(f"/download/{project_id}/issues.xlsx")
            self.assertEqual(issues_response.status_code, 200)
            self.assertTrue(issues_response.data.startswith(b"PK"))
            issues_response.close()


def _write_pdf(path: Path, text: str) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    document.save(path)
    document.close()


if __name__ == "__main__":
    unittest.main()
