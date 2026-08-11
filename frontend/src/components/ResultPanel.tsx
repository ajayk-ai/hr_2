import { useMemo, useState } from "react";

import { evaluateChecklist } from "../checklist";
import type { FileReport, ProcessingReport, ProcessingResult } from "../types";
import { Checklist } from "./Checklist";
import { formatBytes } from "./FileList";

interface Props {
  result: ProcessingResult;
  onDownload: () => void;
  onReset: () => void;
}

/**
 * Per-file outcome, ranked by how much attention it needs.
 *
 * A duplicate is deliberately its own state rather than a failure. It has no
 * output filename, and rendering that as "not included" alongside a genuine OCR
 * error tells the reader something went wrong when nothing did.
 */
type Outcome = "failed" | "attention" | "duplicate" | "filed";

function outcomeOf(report: FileReport, needsReview: Set<string>): Outcome {
  if (report.error) return "failed";
  if (report.duplicate_of) return "duplicate";
  if (report.output_filename && needsReview.has(report.output_filename)) return "attention";
  return "filed";
}

const OUTCOME_LABEL: Record<Outcome, string> = {
  failed: "Failed",
  attention: "Check this",
  duplicate: "Duplicate",
  filed: "Filed",
};

function confidenceTier(value: number): "high" | "medium" | "low" {
  if (value >= 0.85) return "high";
  if (value >= 0.55) return "medium";
  return "low";
}

/**
 * Batch-level findings, ordered by consequence rather than by where they happen
 * to live in the report.
 *
 * The severity split is the point. A document that may belong to somebody else
 * and a note that some text was truncated are not the same kind of news, and
 * rendering both as one shade of amber trains people to skim past both.
 *
 * `report.missing_document_types` is deliberately not surfaced here. It is a
 * flat presence check against the API's generic default required set, which
 * cannot express "either a passbook or a cancelled cheque" or "three payslips,
 * not one" -- the `<Checklist>` component below encodes those rules and would
 * visibly contradict a flat finding shown at the same time. The field remains
 * in the API response for callers that want the simpler check.
 */
interface Finding {
  severity: "critical" | "warning" | "info";
  title: string;
  items: string[];
}

function collectFindings(report: ProcessingReport): Finding[] {
  const findings: Finding[] = [];

  if (report.name_mismatches.length) {
    findings.push({
      severity: "critical",
      title: "Name does not match the rest of the batch",
      items: report.name_mismatches,
    });
  }
  if (report.identifier_warnings.length) {
    findings.push({
      severity: "critical",
      title: "Identifier failed validation",
      items: report.identifier_warnings,
    });
  }
  if (report.duplicate_document_types.length) {
    findings.push({
      severity: "warning",
      title: "More than one of the same document type",
      items: [report.duplicate_document_types.join(", ")],
    });
  }
  if (report.duplicate_uploads > 0) {
    findings.push({
      severity: "info",
      title: `${report.duplicate_uploads} identical ${
        report.duplicate_uploads === 1 ? "upload was" : "uploads were"
      } skipped`,
      items: ["The same file was attached more than once; one copy is in the ZIP."],
    });
  }
  return findings;
}

function Stat({ value, label, tone }: { value: number; label: string; tone: string }) {
  return (
    <div className={`stat stat--${tone}`}>
      <span className="stat__value">{value}</span>
      <span className="stat__label">{label}</span>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  // `navigator.clipboard` is unavailable on insecure origins, which an internal
  // tool may well be served from. Failing silently back to no-op is better than
  // an unhandled rejection in the console.
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  return (
    <button
      type="button"
      className="icon-button"
      onClick={() => void copy()}
      aria-label={`Copy filename ${text}`}
      title="Copy filename"
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function FileRow({ report, outcome }: { report: FileReport; outcome: Outcome }) {
  const fields = Object.entries(report.extracted_fields);

  return (
    <li className={`result-row result-row--${outcome}`}>
      <div className="result-row__head">
        <span className={`badge badge--${outcome}`}>{OUTCOME_LABEL[outcome]}</span>
        <span className="result-row__original" title={report.original_filename}>
          {report.original_filename}
        </span>
      </div>

      {report.output_filename ? (
        <div className="result-row__renamed">
          <code className="filename">{report.output_filename}</code>
          <CopyButton text={report.output_filename} />
        </div>
      ) : report.duplicate_of ? (
        <p className="result-row__note">
          Identical to <strong>{report.duplicate_of}</strong> — not added to the ZIP again.
        </p>
      ) : null}

      {outcome !== "duplicate" && (
        <div className="result-row__tags">
          <span className="tag">{report.document_type}</span>
          <span className={`tag tag--${confidenceTier(report.confidence)}`}>
            {(report.confidence * 100).toFixed(0)}% confident
          </span>
          {report.page_count ? (
            <span className="tag tag--muted">
              {report.page_count} {report.page_count === 1 ? "page" : "pages"}
            </span>
          ) : null}
          <span className="tag tag--muted">{formatBytes(report.size_bytes)}</span>
        </div>
      )}

      {fields.length > 0 && (
        <dl className="field-grid">
          {fields.map(([key, value]) => (
            <div key={key} className="field-grid__pair">
              <dt>{key.replace(/_/g, " ")}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
        </dl>
      )}

      {report.error && <p className="alert alert--error">{report.error}</p>}
      {outcome !== "duplicate" &&
        report.warnings.map((warning) => (
          <p key={warning} className="alert alert--warning">
            {warning}
          </p>
        ))}
    </li>
  );
}

export function ResultPanel({ result, onDownload, onReset }: Props) {
  const { report } = result;
  const [onlyAttention, setOnlyAttention] = useState(false);

  const needsReview = useMemo(() => new Set(report.needs_review), [report.needs_review]);

  const rows = useMemo(
    () => report.files.map((file) => ({ file, outcome: outcomeOf(file, needsReview) })),
    [report.files, needsReview],
  );

  const counts = useMemo(
    () => ({
      filed: rows.filter((r) => r.outcome === "filed" || r.outcome === "attention").length,
      attention: rows.filter((r) => r.outcome === "attention").length,
      failed: rows.filter((r) => r.outcome === "failed").length,
      duplicate: rows.filter((r) => r.outcome === "duplicate").length,
    }),
    [rows],
  );

  const findings = useMemo(() => collectFindings(report), [report]);
  const checklistOutstanding = useMemo(
    () => evaluateChecklist(report.files).reduce((sum, c) => sum + c.outstanding, 0),
    [report.files],
  );
  const flagged = counts.attention + counts.failed;

  const visible = onlyAttention
    ? rows.filter((r) => r.outcome === "attention" || r.outcome === "failed")
    : rows;

  return (
    <section className="panel result" aria-labelledby="result-heading">
      <header className="result__header">
        <div>
          <h2 id="result-heading" className="result__title">
            {report.candidate_name ?? "Candidate"}
          </h2>
          <p className="result__meta">
            {(report.processing_ms / 1000).toFixed(1)}s &middot; request{" "}
            <code>{report.request_id.slice(0, 8)}</code>
          </p>
        </div>
        <div className="actions">
          <button type="button" className="button button--primary" onClick={onDownload}>
            Download ZIP
          </button>
          <button type="button" className="button button--secondary" onClick={onReset}>
            Start over
          </button>
        </div>
      </header>

      <div className="stat-row">
        <Stat value={counts.filed} label="filed" tone="ok" />
        <Stat value={counts.attention} label="to check" tone="attention" />
        <Stat value={counts.failed} label="failed" tone="failed" />
        <Stat value={checklistOutstanding} label="missing" tone="warning" />
      </div>

      {findings.length > 0 && (
        <ul className="findings">
          {findings.map((finding) => (
            <li key={finding.title} className={`finding finding--${finding.severity}`}>
              <p className="finding__title">{finding.title}</p>
              <ul className="finding__items">
                {finding.items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}

      <Checklist files={report.files} />

      <div className="result__toolbar">
        <h3 className="result__subheading">
          {visible.length} of {rows.length} {rows.length === 1 ? "file" : "files"}
        </h3>
        {flagged > 0 && (
          // With 25 uploads, scrolling to find the two that need work is the
          // whole job. This makes it one click.
          <label className="toggle">
            <input
              type="checkbox"
              checked={onlyAttention}
              onChange={(event) => setOnlyAttention(event.target.checked)}
            />
            Only show the {flagged} needing attention
          </label>
        )}
      </div>

      <ul className="result-list">
        {visible.map(({ file, outcome }, index) => (
          // Two uploads can share a name, so the index is part of the key.
          <FileRow key={`${file.original_filename}-${index}`} report={file} outcome={outcome} />
        ))}
      </ul>
    </section>
  );
}
