import { unzipSync, strFromU8 } from "fflate";

import type { ApiErrorBody, ProcessingReport, ProcessingResult } from "../types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const API_TOKEN = import.meta.env.VITE_API_TOKEN ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function authHeaders(): HeadersInit {
  return API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {};
}

/**
 * Turn a non-2xx response into a typed error.
 *
 * The backend returns `{error: {code, message}}`, but a proxy or gateway failing
 * in front of it will not, so the body is parsed defensively.
 */
async function toApiError(response: Response): Promise<ApiError> {
  let message = `Request failed with status ${response.status}.`;
  let code = "unknown_error";
  try {
    const body = (await response.json()) as Partial<ApiErrorBody>;
    if (body.error?.message) {
      message = body.error.message;
      code = body.error.code ?? code;
    }
  } catch {
    // Body was not JSON; the status-derived message stands.
  }
  return new ApiError(message, code, response.status);
}

function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const match = /filename="?([^"]+)"?/.exec(header);
  return match?.[1] ?? fallback;
}

/**
 * Upload documents and return both the ZIP and the report inside it.
 *
 * `report.json` is read out of the archive in the browser rather than fetched
 * separately: the server holds no state between requests, so a second call would
 * mean re-running OCR and the LLM over the same files at full cost.
 */
export async function processDocuments(
  files: File[],
  options: {
    candidateName?: string;
    prNumber?: string;
    requiredDocuments?: string[];
    signal?: AbortSignal;
  } = {},
): Promise<ProcessingResult> {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  if (options.candidateName?.trim()) {
    form.append("candidate_name", options.candidateName.trim());
  }
  if (options.prNumber?.trim()) {
    form.append("pr_number", options.prNumber.trim());
  }
  if (options.requiredDocuments?.length) {
    form.append("required_documents", JSON.stringify(options.requiredDocuments));
  }

  const response = await fetch(`${BASE_URL}/api/v1/documents/process`, {
    method: "POST",
    body: form,
    headers: authHeaders(),
    ...(options.signal ? { signal: options.signal } : {}),
  });

  if (!response.ok) throw await toApiError(response);

  const archive = await response.blob();
  const bytes = new Uint8Array(await archive.arrayBuffer());

  let report: ProcessingReport;
  try {
    const entries = unzipSync(bytes, { filter: (f) => f.name === "report.json" });
    const raw = entries["report.json"];
    if (!raw) throw new Error("report.json missing from the archive");
    report = JSON.parse(strFromU8(raw)) as ProcessingReport;
  } catch {
    throw new ApiError(
      "The server returned an archive that could not be read.",
      "malformed_archive",
      response.status,
    );
  }

  return {
    report,
    archive,
    suggestedFilename: filenameFromDisposition(
      response.headers.get("Content-Disposition"),
      report.pr_number
        ? `documents_PR-${report.pr_number}.zip`
        : `documents_${report.request_id.slice(0, 8)}.zip`,
    ),
  };
}

export interface DocumentTypesResponse {
  filing_order: string[];
  default_required: string[];
  supported_mime_types: string[];
}

export async function fetchDocumentTypes(): Promise<DocumentTypesResponse> {
  const response = await fetch(`${BASE_URL}/api/v1/document-types`, {
    headers: authHeaders(),
  });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as DocumentTypesResponse;
}

/** Trigger a browser download without leaving the object URL leaked. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
