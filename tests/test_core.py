from __future__ import annotations

import unittest
import tempfile
from decimal import Decimal
from pathlib import Path

from app.comparator import compare_documents
from app.main import _component_inventory
from app.models import ExtractionResult, PageText, TextWord
from app.parser import extract_employee_code, normalize_period, parse_document
from app.storage import delete_component_alias, list_component_aliases, load_component_mappings_from_db, save_component_alias


SAMPLE_A = """
Naam: Jan Jansen
Geboortedatum: 01-02-1980
Periode: 2026-01
1000 Bruto salaris 3.000,00
2000 Pensioenpremie -150,00
Netto loon 2.250,00
"""

SAMPLE_B = """
Werknemer: Jan Jansen
Geb.datum: 01-02-1980
Loonperiode: 2026-01
1000 Bruto salaris 3.000,00
2000 Pensioenpremie -150,00
Netto loon 2.240,00
"""


class CoreFlowTest(unittest.TestCase):
    def test_parse_and_compare_sample_payslip(self) -> None:
        doc_a = parse_document(
            ExtractionResult("a.pdf", [PageText(1, SAMPLE_A)]),
            source="A",
        )
        doc_b = parse_document(
            ExtractionResult("b.pdf", [PageText(1, SAMPLE_B)]),
            source="B",
        )

        self.assertEqual(len(doc_a.payslips), 1)
        self.assertEqual(doc_a.payslips[0].employee_name, "Jan Jansen")
        self.assertEqual(doc_a.payslips[0].birth_date, "1980-02-01")
        self.assertGreaterEqual(len(doc_a.payslips[0].components), 3)

        result = compare_documents(doc_a, doc_b)
        self.assertEqual(result.summary["gematchte_medewerkers"], 1)
        self.assertEqual(result.summary["componentverschillen"], 1)

        net_row = next(row for row in result.components if "Netto loon" in row.canonical_component)
        self.assertEqual(net_row.status, "VERSCHIL")
        self.assertEqual(net_row.difference, Decimal("-10.00"))

    def test_default_aliases_compare_afas_loket_labels(self) -> None:
        doc_a = parse_document(
            ExtractionResult(
                "a.pdf",
                [
                    PageText(
                        1,
                        """
Loonstrook
P.J. Verbeek
Geboortedatum
27-02-1975
Salaris (Uit uren gewerkt) 6.027,00
Loonheffing -100,00
""",
                    )
                ],
            ),
            source="A",
        )
        doc_b = parse_document(
            ExtractionResult(
                "b.pdf",
                [
                    PageText(
                        1,
                        """
DE HEER PJ VERBEEK
Geb. datum 27-02-1975
LOON/SALARIS 6.027,00
LB/PR.VOLKSVZ. -90,00
""",
                    )
                ],
            ),
            source="B",
        )

        result = compare_documents(doc_a, doc_b)

        self.assertEqual(result.summary["gematchte_medewerkers"], 1)
        labels = {row.canonical_component: row for row in result.components}
        self.assertEqual(labels["Salaris"].status, "OK")
        self.assertEqual(labels["Loonheffing"].status, "VERSCHIL")

    def test_parse_afas_layout_table_components(self) -> None:
        page = PageText(
            1,
            "\n".join(
                [
                    "Februari 2026",
                    "Loonstrook",
                    "Meindert van den Eykel",
                    "Geboortedatum",
                    "31-03-1961",
                ]
            ),
            words=[
                TextWord(18, 10, 90, 18, "Omschrijving"),
                TextWord(213, 10, 250, 18, "Aantal"),
                TextWord(281, 10, 315, 18, "Basis"),
                TextWord(336, 10, 390, 18, "Bruto/netto"),
                TextWord(414, 10, 460, 18, "Normaal"),
                TextWord(530, 10, 585, 18, "Cumulatief"),
                TextWord(18, 30, 45, 38, "Salaris"),
                TextWord(50, 30, 75, 38, "(Uit"),
                TextWord(80, 30, 110, 38, "uren"),
                TextWord(115, 30, 160, 38, "gewerkt)"),
                TextWord(214, 30, 248, 38, "160,00"),
                TextWord(352, 30, 400, 38, "5.510,46"),
                TextWord(417, 30, 465, 38, "5.510,46"),
                TextWord(538, 30, 590, 38, "11.020,92"),
                TextWord(18, 50, 80, 58, "Keuzemodel:"),
                TextWord(85, 50, 140, 58, "inhouding"),
                TextWord(145, 50, 180, 58, "salaris"),
                TextWord(361, 50, 405, 58, "-60,00"),
                TextWord(426, 50, 470, 58, "-60,00"),
                TextWord(18, 63, 130, 71, "(Sportabonnement)"),
                TextWord(18, 90, 75, 98, "Nettoloon"),
                TextWord(352, 90, 400, 98, "3.708,94"),
                TextWord(417, 90, 465, 98, "5.471,94"),
                TextWord(18, 120, 90, 128, "Medewerker"),
                TextWord(94, 120, 130, 128, "Holding"),
            ],
        )

        doc = parse_document(ExtractionResult("afas.pdf", [page]), source="A")

        self.assertEqual(doc.payslips[0].employee_name, "Meindert van den Eykel")
        self.assertEqual(doc.payslips[0].birth_date, "1961-03-31")
        self.assertEqual(doc.payslips[0].period, "Februari 2026")
        self.assertEqual(len(doc.payslips[0].components), 3)
        self.assertEqual(doc.payslips[0].components[1].label, "Keuzemodel: inhouding salaris (Sportabonnement)")
        self.assertEqual(doc.payslips[0].components[2].amount, Decimal("3708.94"))

    def test_unmatched_payslips_are_reported_as_warnings(self) -> None:
        doc_a = parse_document(
            ExtractionResult(
                "a.pdf",
                [
                    PageText(
                        1,
                        """
Naam: Jan Jansen
Geboortedatum: 01-02-1980
1000 Bruto salaris 3.000,00
""",
                    )
                ],
            ),
            source="A",
        )
        doc_b = parse_document(
            ExtractionResult(
                "b.pdf",
                [
                    PageText(
                        1,
                        """
Naam: Piet Pietersen
Geboortedatum: 03-04-1981
1000 Bruto salaris 3.100,00
""",
                    )
                ],
            ),
            source="B",
        )

        result = compare_documents(doc_a, doc_b)

        self.assertEqual(result.summary["gematchte_medewerkers"], 0)
        self.assertEqual(result.summary["alleen_in_a"], 1)
        self.assertEqual(result.summary["alleen_in_b"], 1)
        self.assertEqual(result.summary["waarschuwingen"], 2)
        self.assertTrue(
            any("Geen match gevonden" in warning and "document A" in warning for warning in result.warnings)
        )
        self.assertTrue(
            any("Geen match gevonden" in warning and "document B" in warning for warning in result.warnings)
        )

    def test_extract_employee_code_variants(self) -> None:
        self.assertEqual(extract_employee_code("Medewerkernummer: 12345"), "12345")
        self.assertEqual(extract_employee_code("Pers. nr. 77-AB"), "77-AB")
        self.assertEqual(extract_employee_code("Employee no: E9001"), "E9001")
        self.assertEqual(extract_employee_code("Code: 4567"), "4567")

    def test_match_uses_employee_code_and_birth_date_first(self) -> None:
        doc_a = parse_document(
            ExtractionResult(
                "a.pdf",
                [
                    PageText(
                        1,
                        """
Naam: Jan Jansen
Medewerkernummer: 12345
Geboortedatum: 01-02-1980
1000 Bruto salaris 3.000,00
""",
                    )
                ],
            ),
            source="A",
        )
        doc_b = parse_document(
            ExtractionResult(
                "b.pdf",
                [
                    PageText(
                        1,
                        """
Naam: J. Janssen
Personeelsnummer: 12345
Geboortedatum: 01-02-1980
1000 Bruto salaris 3.000,00
""",
                    )
                ],
            ),
            source="B",
        )

        result = compare_documents(doc_a, doc_b)

        self.assertEqual(result.summary["gematchte_medewerkers"], 1)
        self.assertEqual(result.employees[0].employee_code, "12345")
        self.assertTrue(any("namen verschillen" in warning for warning in result.warnings))

    def test_name_birthdate_fallback_warns_on_employee_code_conflict(self) -> None:
        doc_a = parse_document(
            ExtractionResult(
                "a.pdf",
                [
                    PageText(
                        1,
                        """
Naam: Jan Jansen
Medewerkernummer: 12345
Geboortedatum: 01-02-1980
1000 Bruto salaris 3.000,00
""",
                    )
                ],
            ),
            source="A",
        )
        doc_b = parse_document(
            ExtractionResult(
                "b.pdf",
                [
                    PageText(
                        1,
                        """
Naam: Jan Jansen
Medewerkernummer: 99999
Geboortedatum: 01-02-1980
1000 Bruto salaris 3.000,00
""",
                    )
                ],
            ),
            source="B",
        )

        result = compare_documents(doc_a, doc_b)

        self.assertEqual(result.summary["gematchte_medewerkers"], 1)
        self.assertTrue(any("codes verschillen" in warning for warning in result.warnings))

    def test_employee_code_without_birthdate_does_not_match_by_itself(self) -> None:
        doc_a = parse_document(
            ExtractionResult(
                "a.pdf",
                [
                    PageText(
                        1,
                        """
Naam: Jan Jansen
Medewerkernummer: 777
1000 Bruto salaris 3.000,00
""",
                    )
                ],
            ),
            source="A",
        )
        doc_b = parse_document(
            ExtractionResult(
                "b.pdf",
                [
                    PageText(
                        1,
                        """
Naam: Piet Pietersen
Medewerkernummer: 777
1000 Bruto salaris 3.000,00
""",
                    )
                ],
            ),
            source="B",
        )

        result = compare_documents(doc_a, doc_b)

        self.assertEqual(result.summary["gematchte_medewerkers"], 0)
        self.assertEqual(result.summary["alleen_in_a"], 1)
        self.assertEqual(result.summary["alleen_in_b"], 1)

    def test_learned_component_alias_is_applied_to_next_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "schaduwdraai.db"
            save_component_alias(
                canonical="Vakantiegeld",
                source_a="Vakantiegeld",
                source_b="Reservering vakantietoeslag",
                path=db_path,
            )
            mappings = load_component_mappings_from_db(path=db_path)

        doc_a = parse_document(
            ExtractionResult(
                "a.pdf",
                [
                    PageText(
                        1,
                        """
Naam: Jan Jansen
Geboortedatum: 01-02-1980
Vakantiegeld 250,00
""",
                    )
                ],
            ),
            source="A",
        )
        doc_b = parse_document(
            ExtractionResult(
                "b.pdf",
                [
                    PageText(
                        1,
                        """
Naam: Jan Jansen
Geboortedatum: 01-02-1980
Reservering vakantietoeslag 250,00
""",
                    )
                ],
            ),
            source="B",
        )

        result = compare_documents(doc_a, doc_b, mappings=mappings)

        self.assertEqual(result.summary["componenten_ok"], 1)
        self.assertEqual(result.components[0].canonical_component, "Vakantiegeld")

    def test_learned_component_aliases_can_be_listed_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "schaduwdraai.db"
            alias = save_component_alias(
                canonical="Vakantiegeld",
                source_a="Vakantiegeld",
                source_b="Reservering vakantietoeslag",
                path=db_path,
            )

            aliases = list_component_aliases(path=db_path)
            self.assertEqual(len(aliases), 1)
            self.assertEqual(aliases[0]["id"], alias["id"])
            self.assertEqual(aliases[0]["canonical"], "Vakantiegeld")

            self.assertTrue(delete_component_alias(alias["id"], path=db_path))
            self.assertEqual(list_component_aliases(path=db_path), [])
            self.assertFalse(delete_component_alias(alias["id"], path=db_path))

    def test_component_inventory_groups_statuses_and_unmatched_dropdowns(self) -> None:
        doc_a = parse_document(
            ExtractionResult(
                "a.pdf",
                [
                    PageText(
                        1,
                        """
Naam: Jan Jansen
Geboortedatum: 01-02-1980
1000 Bruto salaris 3.000,00
Vakantiegeld 250,00
Netto loon 2.250,00
""",
                    )
                ],
            ),
            source="A",
        )
        doc_b = parse_document(
            ExtractionResult(
                "b.pdf",
                [
                    PageText(
                        1,
                        """
Naam: Jan Jansen
Geboortedatum: 01-02-1980
1000 Bruto salaris 3.000,00
Reservering vakantietoeslag 250,00
Netto loon 2.240,00
""",
                    )
                ],
            ),
            source="B",
        )

        inventory = _component_inventory(compare_documents(doc_a, doc_b))

        statuses_a = {row["component"]: row["status"] for row in inventory["document_a"]}
        statuses_b = {row["component"]: row["status"] for row in inventory["document_b"]}
        self.assertEqual(statuses_a["1000 Bruto salaris"], "Gematcht")
        self.assertEqual(statuses_a["Netto loon"], "Verschil")
        self.assertEqual(statuses_a["Vakantiegeld"], "Geen match")
        self.assertEqual(statuses_b["Reservering vakantietoeslag"], "Geen match")
        self.assertEqual(inventory["unmatched_a"][0]["component"], "Vakantiegeld")
        self.assertEqual(inventory["dropdown_options_b"][0]["component"], "Reservering vakantietoeslag")

    def test_period_provider_and_scenario_warnings(self) -> None:
        doc_a = parse_document(
            ExtractionResult(
                "AFAS concept.pdf",
                [
                    PageText(
                        1,
                        """
AFAS concept loonstrook
Naam: Jan Jansen
Geboortedatum: 01-02-1980
Periode: Januari 2026
1000 Bruto salaris 3.000,00
""",
                    )
                ],
            ),
            source="A",
        )
        doc_b = parse_document(
            ExtractionResult(
                "Loket productie.pdf",
                [
                    PageText(
                        1,
                        """
Loket definitieve loonstrook
Naam: Jan Jansen
Geboortedatum: 01-02-1980
Periode: Februari 2026
1000 Bruto salaris 3.000,00
""",
                    )
                ],
            ),
            source="B",
        )

        result = compare_documents(doc_a, doc_b)

        self.assertEqual(normalize_period("Februari 2026"), "2026-02")
        self.assertEqual(doc_a.normalized_period, "2026-01")
        self.assertEqual(doc_b.normalized_period, "2026-02")
        self.assertEqual(doc_a.provider, "AFAS")
        self.assertEqual(doc_b.provider, "Loket")
        self.assertTrue(any("Verschillende periodes" in warning for warning in result.warnings))
        self.assertTrue(any("Providerwissel" in warning for warning in result.warnings))
        self.assertTrue(any("scenario" in warning.lower() for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
