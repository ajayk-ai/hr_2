/** Mirrors `app/domain/schemas.py`. Keep the two in sync. */

export type DocumentType =
  | "Photograph"
  | "Aadhaar"
  | "PAN"
  | "Passport"
  | "Resume"
  | "OfferLetter"
  | "AppointmentOrder"
  | "RelievingLetter"
  | "ExperienceLetter"
  | "SalarySlip"
  | "RevisionLetter"
  | "BankStatement"
  | "CancelledCheque"
  | "SSLC"
  | "HSC"
  | "DiplomaCertificate"
  | "Marksheet"
  | "DegreeCertificate"
  | "PGCertificate"
  | "MedicalFitnessCertificate"
  | "Other"
  | "Unknown";

export interface FileReport {
  original_filename: string;
  output_filename: string | null;
  document_type: DocumentType;
  confidence: number;
  content_type: string;
  size_bytes: number;
  ocr_confidence: number | null;
  page_count: number | null;
  extracted_fields: Record<string, string | number>;
  reasoning: string;
  warnings: string[];
  error: string | null;
  /** Set when this upload was byte-identical to an earlier one, naming that file. */
  duplicate_of: string | null;
}

export interface ProcessingReport {
  request_id: string;
  generated_at: string;
  candidate_name: string | null;
  /** HR-supplied requisition/PR number, when given. The candidate's tracking id. */
  pr_number: string | null;
  files: FileReport[];
  missing_document_types: DocumentType[];
  duplicate_document_types: DocumentType[];
  /** Uploads skipped as byte-identical copies of another file. */
  duplicate_uploads: number;
  /** Documents whose printed name could not be reconciled with the batch. */
  name_mismatches: string[];
  /** Identifiers that failed a format or checksum check. */
  identifier_warnings: string[];
  needs_review: string[];
  processing_ms: number;
}

export interface ProcessingResult {
  report: ProcessingReport;
  /** The ZIP exactly as the server produced it, ready to hand to the user. */
  archive: Blob;
  suggestedFilename: string;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}

/** A queued upload, before it is sent. */
export interface QueuedFile {
  id: string;
  file: File;
}
