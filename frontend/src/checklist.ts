import type { DocumentType, FileReport } from "./types";

/**
 * The candidate onboarding checklist, grouped the way HR actually reads a
 * joining file rather than by the underlying document taxonomy.
 *
 * This is deliberately static and frontend-only. Every fact it needs --
 * which document type each line item maps to -- is already present on
 * `report.files[].document_type`, so no backend schema change was needed to
 * add it; only the taxonomy itself gained the document types this checklist
 * refers to (SSLC, HSC, DiplomaCertificate, PGCertificate, AppointmentOrder,
 * MedicalFitnessCertificate, RevisionLetter).
 */
export interface ChecklistItem {
  label: string;
  /** OR-group: satisfied by any file matching one of these types. */
  types: DocumentType[];
  /** How many matching files are required. Default 1. */
  minCount?: number;
  /** Shown for context regardless of status, e.g. "either one is fine". */
  note?: string;
  /**
   * Present but not required *yet*. Renders as a neutral reminder rather than
   * a red "missing" flag when absent -- for the one item on this list (medical
   * fitness) that the source checklist says is brought physically at joining,
   * not submitted with the rest.
   */
  deferred?: boolean;
}

export interface ChecklistCategory {
  id: string;
  title: string;
  items: ChecklistItem[];
  /**
   * The category can't be scored pass/fail -- how many appointment/relieving
   * letters *should* exist depends on how many employers the candidate has had
   * and which one is current, neither of which this system knows. Counts are
   * shown for HR to judge rather than a fabricated status.
   */
  countOnly?: boolean;
  note?: string;
}

export const CHECKLIST: ChecklistCategory[] = [
  {
    id: "personal",
    title: "Personal documents",
    items: [
      { label: "Aadhaar Card", types: ["Aadhaar"] },
      { label: "PAN Card", types: ["PAN"] },
      {
        label: "Bank Details",
        types: ["BankStatement", "CancelledCheque"],
        note: "Passbook or a cancelled cheque leaf -- either is fine.",
      },
    ],
  },
  {
    id: "education",
    title: "Educational details",
    items: [
      { label: "SSLC", types: ["SSLC"] },
      { label: "HSC", types: ["HSC"] },
      { label: "Diploma", types: ["DiplomaCertificate"] },
      { label: "UG", types: ["DegreeCertificate"] },
      { label: "PG", types: ["PGCertificate"] },
    ],
  },
  {
    id: "employment",
    title: "Employment documents",
    countOnly: true,
    note: "Appointment order and relieving order from previous employers. Current company is exempted, so this is a count for you to judge rather than a pass/fail.",
    items: [
      { label: "Appointment Order", types: ["AppointmentOrder"] },
      { label: "Relieving Order", types: ["RelievingLetter"] },
    ],
  },
  {
    id: "payslip",
    title: "Payslips",
    items: [{ label: "Last 3 months' payslips", types: ["SalarySlip"], minCount: 3 }],
  },
  {
    id: "photo-medical",
    title: "Photo & medical",
    items: [
      { label: "Passport size photograph", types: ["Photograph"] },
      {
        label: "Medical fitness certificate",
        types: ["MedicalFitnessCertificate"],
        deferred: true,
        note: "Physical copy brought at joining -- not required in this upload.",
      },
    ],
  },
  {
    id: "revision",
    title: "Revision letter",
    items: [
      { label: "Last year's revision letter (with CTC breakup)", types: ["RevisionLetter"] },
    ],
  },
];

export type ItemStatus = "received" | "missing" | "partial" | "deferred";

export interface ItemResult {
  item: ChecklistItem;
  count: number;
  status: ItemStatus;
  remark: string | null;
}

export function evaluateItem(item: ChecklistItem, files: FileReport[]): ItemResult {
  // A file counts only if it actually made it into the ZIP: excludes both
  // failures and duplicates, which share the `output_filename === null`
  // signal the backend already uses for the same distinction.
  const count = files.filter(
    (f) => f.output_filename !== null && item.types.includes(f.document_type),
  ).length;
  const minCount = item.minCount ?? 1;

  if (item.deferred) {
    return { item, count, status: count > 0 ? "received" : "deferred", remark: item.note ?? null };
  }
  if (count >= minCount) {
    return { item, count, status: "received", remark: item.note ?? null };
  }
  if (count > 0) {
    return { item, count, status: "partial", remark: `${count} of ${minCount} received` };
  }
  return { item, count, status: "missing", remark: item.note ?? null };
}

export interface CategoryResult {
  category: ChecklistCategory;
  items: ItemResult[];
  /** Count of items still needing attention -- always 0 for a `countOnly` category. */
  outstanding: number;
}

export function evaluateChecklist(files: FileReport[]): CategoryResult[] {
  return CHECKLIST.map((category) => {
    const items = category.items.map((item) => evaluateItem(item, files));
    const outstanding = category.countOnly
      ? 0
      : items.filter((r) => r.status === "missing" || r.status === "partial").length;
    return { category, items, outstanding };
  });
}

//: Every document type any checklist item names, computed once rather than per
//: render -- membership-tested for every file in `evaluateOtherDocuments`.
const COVERED_TYPES: ReadonlySet<DocumentType> = new Set(
  CHECKLIST.flatMap((category) => category.items.flatMap((item) => item.types)),
);

/**
 * Files the candidate sent that this checklist has no box for -- a Resume, a
 * Passport, an Offer Letter, or anything the model filed as `Other`/`Unknown`.
 *
 * The pipeline classifies and renames every upload unconditionally; nothing is
 * ever silently dropped because it wasn't on the checklist. Without this list
 * those files would still be in the ZIP and in the per-file table below, but
 * the checklist itself would look like it accounted for less than the full
 * batch, which reads as "the rest went missing" rather than "the rest simply
 * wasn't asked for."
 */
export function evaluateOtherDocuments(files: FileReport[]): FileReport[] {
  return files.filter(
    (f) => f.output_filename !== null && !COVERED_TYPES.has(f.document_type),
  );
}
