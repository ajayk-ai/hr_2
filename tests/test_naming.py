from __future__ import annotations

import pytest

from app.domain.document_types import DocumentType
from app.domain.naming import (
    build_filename,
    build_identifier,
    deduplicate,
    extension_for,
    normalise_name_segment,
    sort_key,
)
from app.domain.schemas import ExtractedFields


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Ravi  Kumar Sharma ", "RaviKumarSharma"),
        ("RAVI KUMAR", "RAVIKUMAR"),
        ("Ravi-Kumar_Sharma", "RaviKumarSharma"),
        ("Ravi Sharmá", "RaviSharma"),
        ("श्री Ravi", "Ravi"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalise_name_segment(raw: str | None, expected: str) -> None:
    assert normalise_name_segment(raw) == expected


def test_normalise_name_segment_truncates() -> None:
    assert len(normalise_name_segment("A" * 100)) == 40


class TestExtensionFor:
    def test_uses_the_content_type(self) -> None:
        assert extension_for("scan.PDF", "application/pdf") == ".pdf"

    def test_works_without_any_extension(self) -> None:
        assert extension_for("scan", "image/png") == ".png"

    def test_the_sniffed_type_beats_a_mismatched_extension(self) -> None:
        # A phone PNG saved as IMG_2931.jpg must be filed as the PNG it actually is.
        assert extension_for("IMG_2931.jpg", "image/png") == ".png"
        assert extension_for("scan.pdf", "image/jpeg") == ".jpg"

    def test_rejects_dangerous_extensions(self) -> None:
        # A crafted upload name must not smuggle an executable extension into the ZIP.
        assert extension_for("payload.exe", "application/pdf") == ".pdf"
        assert extension_for("payload.exe", "application/octet-stream") == ".bin"

    def test_falls_back_to_the_extension_for_unmapped_types(self) -> None:
        assert extension_for("scan.tif", "application/octet-stream") == ".tif"


class TestBuildIdentifier:
    def test_aadhaar_is_masked_by_default(self) -> None:
        fields = ExtractedFields(aadhaar_number="123412341234")
        assert build_identifier(DocumentType.AADHAAR, fields) == "XXXXXXXX1234"

    def test_aadhaar_can_be_unmasked_explicitly(self) -> None:
        fields = ExtractedFields(aadhaar_number="1234 1234 1234")
        assert (
            build_identifier(DocumentType.AADHAAR, fields, mask_sensitive=False) == "123412341234"
        )

    def test_pan_is_kept_whole(self) -> None:
        fields = ExtractedFields(pan_number="abcde1234f")
        assert build_identifier(DocumentType.PAN, fields) == "ABCDE1234F"

    def test_salary_slip_uses_the_pay_period(self) -> None:
        fields = ExtractedFields(pay_period_month=3, pay_period_year=2024)
        assert build_identifier(DocumentType.SALARY_SLIP, fields) == "202403"

    def test_salary_slip_falls_back_to_the_document_date(self) -> None:
        fields = ExtractedFields(document_date="2024-07-31")
        assert build_identifier(DocumentType.SALARY_SLIP, fields) == "202407"

    def test_marksheet_combines_qualification_and_year(self) -> None:
        fields = ExtractedFields(qualification="B Tech", exam_year=2018)
        assert build_identifier(DocumentType.MARKSHEET, fields) == "BTech-2018"

    def test_photograph_has_no_identifier(self) -> None:
        assert build_identifier(DocumentType.PHOTOGRAPH, ExtractedFields()) == ""

    def test_missing_fields_degrade_to_empty(self) -> None:
        assert build_identifier(DocumentType.AADHAAR, ExtractedFields()) == ""


class TestBuildFilename:
    def test_composes_all_segments(self) -> None:
        name = build_filename(
            candidate_name="Ravi Kumar",
            document_type=DocumentType.PAN,
            fields=ExtractedFields(pan_number="ABCDE1234F"),
            original_filename="IMG_2931.pdf",
            content_type="application/pdf",
            sequence=3,
        )
        assert name == "03_RaviKumar_PAN_ABCDE1234F.pdf"

    def test_omits_an_empty_identifier(self) -> None:
        name = build_filename(
            candidate_name="Ravi Kumar",
            document_type=DocumentType.PHOTOGRAPH,
            fields=ExtractedFields(),
            original_filename="photo.jpg",
            content_type="image/jpeg",
            sequence=1,
        )
        assert name == "01_RaviKumar_Photograph.jpg"

    def test_falls_back_to_the_original_filename_when_no_name_is_known(self) -> None:
        name = build_filename(
            candidate_name="",
            document_type=DocumentType.RESUME,
            fields=ExtractedFields(),
            original_filename="ravi_cv_final.pdf",
            content_type="application/pdf",
            sequence=1,
        )
        assert name == "01_RaviCvFinal_Resume.pdf"

    def test_falls_back_to_candidate_when_nothing_is_usable(self) -> None:
        name = build_filename(
            candidate_name="",
            document_type=DocumentType.UNKNOWN,
            fields=ExtractedFields(),
            original_filename="___.pdf",
            content_type="application/pdf",
            sequence=1,
        )
        assert name == "01_Candidate_Unknown.pdf"

    def test_stays_within_the_length_budget(self) -> None:
        name = build_filename(
            candidate_name="A" * 60,
            document_type=DocumentType.MARKSHEET,
            fields=ExtractedFields(qualification="Q" * 60, exam_year=2018),
            original_filename="x.pdf",
            content_type="application/pdf",
            sequence=12,
        )
        assert len(name) <= 120
        assert name.endswith(".pdf")

    def test_never_emits_a_path_separator(self) -> None:
        name = build_filename(
            candidate_name="../../etc/passwd",
            document_type=DocumentType.OTHER,
            fields=ExtractedFields(),
            original_filename="../../evil.pdf",
            content_type="application/pdf",
            sequence=1,
        )
        assert "/" not in name and "\\" not in name


class TestDeduplicate:
    def test_leaves_distinct_names_untouched(self) -> None:
        names = ["01_A_PAN.pdf", "02_A_Aadhaar.pdf"]
        assert deduplicate(names) == names

    def test_suffixes_collisions(self) -> None:
        names = ["01_A_SalarySlip.pdf"] * 3
        assert deduplicate(names) == [
            "01_A_SalarySlip.pdf",
            "01_A_SalarySlip-2.pdf",
            "01_A_SalarySlip-3.pdf",
        ]

    def test_collision_check_is_case_insensitive(self) -> None:
        # Windows and macOS filesystems would otherwise overwrite on extraction.
        assert deduplicate(["A_PAN.pdf", "a_pan.PDF"])[1] == "a_pan-2.PDF"


class TestSortKey:
    def test_orders_by_canonical_document_type(self) -> None:
        items = [
            (DocumentType.MARKSHEET, ExtractedFields(), 0),
            (DocumentType.PHOTOGRAPH, ExtractedFields(), 1),
            (DocumentType.PAN, ExtractedFields(), 2),
        ]
        ordered = sorted(items, key=lambda i: sort_key(*i))
        assert [i[0] for i in ordered] == [
            DocumentType.PHOTOGRAPH,
            DocumentType.PAN,
            DocumentType.MARKSHEET,
        ]

    def test_payslips_are_chronological_within_their_group(self) -> None:
        items = [
            (
                DocumentType.SALARY_SLIP,
                ExtractedFields(pay_period_year=2024, pay_period_month=3),
                0,
            ),
            (
                DocumentType.SALARY_SLIP,
                ExtractedFields(pay_period_year=2024, pay_period_month=1),
                1,
            ),
            (
                DocumentType.SALARY_SLIP,
                ExtractedFields(pay_period_year=2023, pay_period_month=12),
                2,
            ),
        ]
        ordered = sorted(items, key=lambda i: sort_key(*i))
        assert [i[1].pay_period_month for i in ordered] == [12, 1, 3]

    def test_undated_documents_sort_last_within_their_group(self) -> None:
        items = [
            (DocumentType.SALARY_SLIP, ExtractedFields(), 0),
            (
                DocumentType.SALARY_SLIP,
                ExtractedFields(pay_period_year=2024, pay_period_month=1),
                1,
            ),
        ]
        ordered = sorted(items, key=lambda i: sort_key(*i))
        assert ordered[0][2] == 1

    def test_upload_order_breaks_ties(self) -> None:
        items = [
            (DocumentType.OTHER, ExtractedFields(), 5),
            (DocumentType.OTHER, ExtractedFields(), 2),
        ]
        ordered = sorted(items, key=lambda i: sort_key(*i))
        assert [i[2] for i in ordered] == [2, 5]
