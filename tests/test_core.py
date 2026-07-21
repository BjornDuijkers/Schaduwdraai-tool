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

    def test_db_grid_profile_extracts_one_payslip_per_page(self) -> None:
        doc = parse_document(
            ExtractionResult(
                "Loonstroken DB 01-2026.pdf",
                [
                    _db_grid_page(1, "104966", "19-03-1981", "T FEITSMA", Decimal("5350.00")),
                    _db_grid_page(2, "105876", "12-09-1965", "OJC GIRAUD", Decimal("7736.00")),
                ],
            ),
            source="A",
        )

        self.assertEqual(len(doc.payslips), 2)
        self.assertEqual(doc.payslips[0].employee_code, "104966")
        self.assertEqual(doc.payslips[0].employee_name, "T Feitsma")
        self.assertEqual(doc.payslips[0].birth_date, "1981-03-19")
        self.assertEqual(doc.payslips[0].period, "01/2026")
        labels = {component.label: component.amount for component in doc.payslips[0].components}
        self.assertEqual(labels["LOON/SALARIS"], Decimal("5350.00"))
        self.assertEqual(labels["BRUTO"], Decimal("5594.40"))
        self.assertNotIn("HEFF.PL.LOON", labels)

    def test_afas_profile_groups_identity_and_calculation_pages(self) -> None:
        doc = parse_document(
            ExtractionResult(
                "AFAS Valid DB.pdf",
                [
                    _afas_identity_page(1, "104966", "19-03-1981", "T. Feitsma"),
                    _afas_calculation_page(2, "T. Feitsma", Decimal("5350.00")),
                ],
            ),
            source="B",
        )

        self.assertEqual(len(doc.payslips), 1)
        payslip = doc.payslips[0]
        self.assertEqual(payslip.page_numbers, [1, 2])
        self.assertEqual(payslip.employee_code, "104966")
        self.assertEqual(payslip.employee_name, "T. Feitsma")
        self.assertEqual(payslip.birth_date, "1981-03-19")
        self.assertEqual(len(payslip.components), 3)
        self.assertEqual(payslip.components[0].label, "Salaris (Uit uren gewerkt)")

    def test_compare_aggregates_duplicate_db_payslips_by_employee_period(self) -> None:
        doc_a = parse_document(
            ExtractionResult(
                "Loonstroken DB 01-2026.pdf",
                [
                    _db_grid_page(1, "108025", "30-03-1993", "JMA VERBERK", Decimal("3106.13")),
                    _db_grid_page(2, "108025", "30-03-1993", "JMA VERBERK", Decimal("1227.27")),
                ],
            ),
            source="A",
        )
        doc_b = parse_document(
            ExtractionResult(
                "AFAS Valid DB.pdf",
                [
                    _afas_identity_page(1, "108025", "30-03-1993", "J.M.A. Verberk"),
                    _afas_calculation_page(2, "J.M.A. Verberk", Decimal("4333.40")),
                ],
            ),
            source="B",
        )

        result = compare_documents(doc_a, doc_b)

        self.assertEqual(result.summary["loonstroken_document_a"], 2)
        self.assertEqual(result.summary["gematchte_medewerkers"], 1)
        self.assertEqual(result.summary["alleen_in_a"], 0)
        self.assertEqual(result.summary["alleen_in_b"], 0)
        self.assertEqual(result.employees[0].source_a_pages, "1, 2")
        salary = next(row for row in result.components if row.canonical_component == "Salaris")
        self.assertEqual(salary.status, "OK")
        self.assertEqual(salary.amount_a, Decimal("4333.40"))
        self.assertEqual(salary.amount_b, Decimal("4333.40"))
        self.assertEqual(result.warnings, [])

def _db_grid_page(
    page_number: int,
    employee_code: str,
    birth_date: str,
    name: str,
    salary: Decimal,
) -> PageText:
    bonus = Decimal("244.40")
    gross = salary + bonus
    text = "\n".join(
        [
            "Werknr.",
            f"{employee_code} {birth_date} 01-08-2012",
            f"Geb. datum {birth_date}",
            "Functie Strook Volgnr. Runnr. Datum run Verl. per.",
            "ALGEMEEN PERIODE 1 1 20-01-2026 01/2026",
            f"DE HEER {name}",
            "S P E C I F I C A T I E O P B O U W T M - P E R I O D E",
        ]
    )
    words = []
    words += _line_words(35, [(35, "Werknr."), (250, "Geb."), (275, "datum")])
    words += _line_words(47, [(40, employee_code), (250, birth_date), (310, "01-08-2012")])
    words += _line_words(104, [(40, "ALGEMEEN"), (250, "PERIODE"), (520, "01/2026")])
    words += _line_words(162, [(62, "DE"), (76, "HEER"), (100, name.split()[0]), (130, name.split()[-1]), (326, "VALID")])
    words += _line_words(
        272,
        [
            (305, "S"), (311, "P"), (316, "E"), (321, "C"), (327, "I"), (332, "F"),
            (338, "I"), (343, "C"), (349, "A"), (354, "T"), (359, "I"), (364, "E"),
            (433, "O"), (440, "P"), (445, "B"), (451, "O"), (457, "U"), (463, "W"),
            (519, "T"), (525, "M"), (532, "-"), (536, "P"), (541, "E"), (546, "R"),
            (552, "I"), (556, "O"), (562, "D"), (569, "E"),
        ],
    )
    words += _line_words(
        284,
        [(98, "Periode"), (142, "Tm-periode"), (295, "Tabel"), (353, "Tarief"), (414, "Tabel"), (469, "Tarief")],
    )
    words += _line_words(
        295,
        [(204, "LOON/SALARIS"), (298, _amount(salary)), (417, _amount(salary)), (547, _amount(salary))],
    )
    words += _line_words(
        305,
        [(204, "BONUS"), (229, "MND"), (363, _amount(bonus)), (482, _amount(bonus)), (553, _amount(bonus))],
    )
    words += _line_words(
        315,
        [(204, "HEFF.PL.LOON"), (298, _amount(gross)), (547, _amount(gross))],
    )
    words += _line_words(
        325,
        [(204, "BRUTO"), (298, _amount(salary)), (363, _amount(bonus)), (547, _amount(gross))],
    )
    return PageText(page_number, text, words=words)


def _afas_identity_page(page_number: int, employee_code: str, birth_date: str, name: str) -> PageText:
    text = "\n".join(
        [
            "Loonstrook",
            "Overige gegevens",
            f"Januari {name}",
            f"Medew.code {employee_code}",
            f"Geboortedatum {birth_date}",
        ]
    )
    words = []
    words += _line_words(34, [(244, "Januari"), (320, name.split()[0]), (350, name.split()[-1])])
    words += _line_words(427, [(60, "Medew.code"), (225, employee_code), (290, "Parttime")])
    words += _line_words(441, [(60, "Geboortedatum"), (225, birth_date)])
    return PageText(page_number, text, words=words)


def _afas_calculation_page(page_number: int, name: str, salary: Decimal) -> PageText:
    holiday = (salary * Decimal("0.08")).quantize(Decimal("0.01"))
    gross = salary + holiday
    text = "\n".join(["Loonstrook", "Loonberekening", f"Januari {name}", "Omschrijving"])
    words = []
    words += _line_words(34, [(244, "Januari"), (320, name.split()[0]), (350, name.split()[-1])])
    words += _line_words(109, [(60, "Loonberekening")])
    words += _line_words(
        133,
        [
            (18, "Omschrijving"),
            (213, "Aantal"),
            (281, "Basis"),
            (336, "Periode"),
            (414, "T/m"),
            (440, "periode"),
            (500, "Normaal"),
            (570, "Bijzonder"),
        ],
    )
    words += _line_words(
        156,
        [(18, "Salaris"), (50, "(Uit"), (80, "uren"), (115, "gewerkt)"), (336, _amount(salary)), (500, _amount(salary))],
    )
    words += _line_words(
        168,
        [(18, "Periodieke"), (70, "uitbetaling"), (135, "vakantiegeld"), (336, _amount(holiday)), (500, _amount(holiday))],
    )
    words += _line_words(201, [(18, "Brutoloon"), (336, _amount(gross))])
    return PageText(page_number, text, words=words)


def _line_words(y: float, items: list[tuple[float, str]]) -> list[TextWord]:
    return [_word(x, y, text) for x, text in items]


def _word(x: float, y: float, text: str) -> TextWord:
    return TextWord(x, y, x + max(len(text) * 4.2, 8), y + 8, text)


def _amount(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


if __name__ == "__main__":
    unittest.main()
