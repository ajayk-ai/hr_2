"""Deterministic filename construction and filing order.

Naming is done here rather than by the LLM. The model is good at reading a
document and bad at being consistent across a batch; filenames need to be stable,
collision-free and safe to write to a filesystem, which is a job for code you can
unit-test. The model supplies facts, this module turns them into names.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.domain.document_types import (
    SENSITIVE_ID_TYPES,
    DocumentType,
    order_index,
)
from app.domain.schemas import ExtractedFields
from app.utils.redaction import mask_identifier

MAX_NAME_SEGMENT = 40
MAX_FILENAME_LENGTH = 120

#: Extensions we are willing to emit. Anything else is normalised to `.bin` so a
#: crafted upload name can never introduce an executable extension into the ZIP.
_ALLOWED_EXTENSIONS = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".gif", ".bmp", ".heic"}
)

_EXTENSION_BY_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/heic": ".heic",
}

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def normalise_name_segment(value: str | None, *, max_length: int = MAX_NAME_SEGMENT) -> str:
    """Fold to ASCII, strip punctuation and PascalCase the words.

    >>> normalise_name_segment("  Ravi  Kumar Sharmá ")
    'RaviKumarSharma'
    """
    if not value:
        return ""
    ascii_form = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    words = [w for w in _NON_ALNUM.split(ascii_form) if w]
    pascal = "".join(w[:1].upper() + w[1:] for w in words)
    return pascal[:max_length]


def names_are_compatible(left: str, right: str) -> bool:
    """Whether two printed names plausibly belong to the same person.

    Indian documents abbreviate inconsistently and legitimately: a PAN card
    carries "AJAY K" where the Aadhaar spells "AJAY KANAGARAJ", and a resume may
    drop the middle name entirely. Treating those as different people would bury
    HR in false alarms; treating *every* difference as the same person would miss
    the case that matters -- somebody else's document filed against this
    candidate.

    The rule: every token of the shorter name must be accounted for in the
    longer one, either exactly or as an initial. That accepts abbreviation and
    omission, and rejects a genuinely different name.

    An empty name on either side returns True. Absence of evidence is not
    evidence of a mismatch, and the document is already flagged for review.
    """
    left_tokens = _name_tokens(left)
    right_tokens = _name_tokens(right)
    if not left_tokens or not right_tokens:
        return True

    shorter, longer = sorted((left_tokens, right_tokens), key=len)
    unmatched = list(longer)
    for token in shorter:
        match = next(
            (
                other
                for other in unmatched
                # A single letter is an initial and matches any token it begins.
                if other == token
                or (len(token) == 1 and other.startswith(token))
                or (len(other) == 1 and token.startswith(other))
            ),
            None,
        )
        if match is None:
            return False
        unmatched.remove(match)
    return True


def _name_tokens(value: str) -> list[str]:
    ascii_form = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return [token.upper() for token in _NON_ALNUM.split(ascii_form) if token]


def extension_for(original_filename: str, content_type: str) -> str:
    """Pick a safe extension for the output file.

    The sniffed content type wins over the uploaded filename's extension. The
    filename is attacker-controlled and routinely wrong in ordinary use too -- a
    phone camera PNG saved as ``IMG_2931.jpg`` -- whereas the content type was
    derived from the file's own magic bytes. The uploaded extension is consulted
    only for content types we have no mapping for.
    """
    canonical = _EXTENSION_BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower())
    if canonical:
        return canonical

    suffix = ""
    if "." in original_filename:
        suffix = "." + original_filename.rsplit(".", 1)[-1].lower()
    return suffix if suffix in _ALLOWED_EXTENSIONS else ".bin"


def build_identifier(
    document_type: DocumentType, fields: ExtractedFields, *, mask_sensitive: bool = True
) -> str:
    """The distinguishing segment of the filename for this document type.

    Returns an empty string when the type has no natural identifier (a photograph
    or a resume), in which case the caller omits the segment entirely.
    """

    def _mask(value: str) -> str:
        if mask_sensitive and document_type in SENSITIVE_ID_TYPES:
            return mask_identifier(value, visible=4)
        return _NON_ALNUM.sub("", value)

    match document_type:
        case DocumentType.AADHAAR if fields.aadhaar_number:
            return _mask(fields.aadhaar_number)
        case DocumentType.PAN if fields.pan_number:
            return _NON_ALNUM.sub("", fields.pan_number).upper()
        case DocumentType.PASSPORT if fields.passport_number:
            return _NON_ALNUM.sub("", fields.passport_number).upper()
        case DocumentType.SALARY_SLIP:
            return _period(fields)
        case DocumentType.BANK_STATEMENT:
            parts = [_mask(fields.bank_account_number) if fields.bank_account_number else ""]
            parts.append(_period(fields))
            return "-".join(p for p in parts if p)
        case DocumentType.CANCELLED_CHEQUE if fields.bank_account_number:
            return _mask(fields.bank_account_number)
        case (
            DocumentType.SSLC_CERTIFICATE
            | DocumentType.HSC_CERTIFICATE
            | DocumentType.DIPLOMA_CERTIFICATE
            | DocumentType.MARKSHEET
            | DocumentType.DEGREE_CERTIFICATE
            | DocumentType.PG_CERTIFICATE
        ):
            qualification = normalise_name_segment(fields.qualification, max_length=20)
            year = str(fields.exam_year) if fields.exam_year else ""
            return "-".join(p for p in (qualification, year) if p)
        case (
            DocumentType.OFFER_LETTER
            | DocumentType.APPOINTMENT_ORDER
            | DocumentType.RELIEVING_LETTER
            | DocumentType.EXPERIENCE_LETTER
        ):
            employer = normalise_name_segment(fields.employer_name, max_length=24)
            year = _year(fields)
            return "-".join(p for p in (employer, year) if p)
        case _:
            return ""


def _period(fields: ExtractedFields) -> str:
    """`YYYYMM` from the explicit pay period, falling back to document_date."""
    if fields.pay_period_year and fields.pay_period_month:
        return f"{fields.pay_period_year:04d}{fields.pay_period_month:02d}"
    if fields.pay_period_year:
        return f"{fields.pay_period_year:04d}"
    match = re.match(r"(\d{4})-(\d{2})", fields.document_date or "")
    if match:
        return f"{match.group(1)}{match.group(2)}"
    return _year(fields)


def _year(fields: ExtractedFields) -> str:
    if fields.pay_period_year:
        return str(fields.pay_period_year)
    if fields.exam_year:
        return str(fields.exam_year)
    match = re.match(r"(\d{4})", fields.document_date or "")
    return match.group(1) if match else ""


def build_filename(
    *,
    candidate_name: str,
    document_type: DocumentType,
    fields: ExtractedFields,
    original_filename: str,
    content_type: str,
    sequence: int,
    request_id: str = "",
    mask_sensitive: bool = True,
) -> str:
    """Compose the final in-ZIP filename.

    The ``NN_`` prefix is not decoration: ZIP entries have no intrinsic display
    order, so every extractor and file browser re-sorts alphabetically. The prefix
    is what makes the requested filing order survive extraction.

    ``request_id`` -- the same id already shown in the UI and used to name the
    ZIP itself (``documents_<id>.zip``) -- is appended to every file it contains.
    Two files named identically by two different HR uploads is otherwise a real
    collision risk once files get extracted out of their ZIP and consolidated
    into one shared folder; the tag also lets any loose file be traced back to
    the request that produced it. It is reserved from the truncation budget
    rather than truncated itself, so it is never the part that gets cut off.
    """
    name = (
        normalise_name_segment(candidate_name)
        or normalise_name_segment(original_filename.rsplit(".", 1)[0])
        or "Candidate"
    )
    identifier = build_identifier(document_type, fields, mask_sensitive=mask_sensitive)
    extension = extension_for(original_filename, content_type)

    segments = [name, document_type.value]
    if identifier:
        segments.append(identifier)
    stem = "_".join(segments)

    prefix = f"{sequence:02d}_"
    id_tag = f"_{request_id[:8]}" if request_id else ""
    budget = MAX_FILENAME_LENGTH - len(prefix) - len(extension) - len(id_tag)
    return f"{prefix}{stem[:budget]}{id_tag}{extension}"


def deduplicate(filenames: list[str]) -> list[str]:
    """Append ``-2``, ``-3`` ... to repeated names, preserving order.

    Two payslips for the same month, or two documents whose identifier could not
    be read, will otherwise collide and silently overwrite inside the ZIP.
    """
    seen: dict[str, int] = {}
    result: list[str] = []
    for filename in filenames:
        key = filename.lower()
        if key not in seen:
            seen[key] = 1
            result.append(filename)
            continue
        seen[key] += 1
        stem, _, extension = filename.rpartition(".")
        stem = stem or filename
        suffix = f"-{seen[key]}"
        candidate = f"{stem}{suffix}.{extension}" if extension else f"{filename}{suffix}"
        result.append(candidate)
    return result


@dataclass(frozen=True, slots=True)
class SortKey:
    """Total ordering for a processed document.

    Primary key is the canonical document-type order. Within a type, documents are
    chronological (oldest first) so a run of payslips or marksheets reads forward;
    documents with no date sort last, then by upload order for stability.
    """

    type_rank: int
    chronological: str
    upload_index: int

    def as_tuple(self) -> tuple[int, str, int]:
        return (self.type_rank, self.chronological, self.upload_index)


def sort_key(
    document_type: DocumentType, fields: ExtractedFields, upload_index: int
) -> tuple[int, str, int]:
    period = _period(fields)
    # "~" sorts after every digit, pushing undated documents to the end of their group.
    return SortKey(order_index(document_type), period or "~", upload_index).as_tuple()
