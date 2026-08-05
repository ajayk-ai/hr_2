import type { FileReport, ProcessingResult } from "../types";
import { formatBytes } from "./FileList";

interface Props {
  result: ProcessingResult;
  onDownload: () => void;
  onReset: () => void;
}

function confidenceLabel(value: number): "high" | "medium" | "low" {
  if (value >= 0.85) return "high";
  if (value >= 0.55) return "medium";
  return "low";
}

function FileRow({ report }: { report: FileReport }) {
  const failed = report.error !== null;
  const fields = Object.entries(report.extracted_fields);

  return (
    <li className={`result-row${failed ? " result-row--failed" : ""}`}>
      <div className="result-row__main">
        <div className="result-row__names">
          <span className="result-row__original" title={report.original_filename}>
            {report.original_filename}
          </span>
          <span aria-hidden="true" className="result-row__arrow">
            →
          </span>
          <strong className="result-row__renamed">
            {report.output_filename ?? "not included"}
          </strong>
        </div>
        <div className="result-row__tags">
          <span className="tag">{report.document_type}</span>
          <span className={`tag tag--${confidenceLabel(report.confidence)}`}>
            {(report.confidence * 100).toFixed(0)}% confident
          </span>
          {report.page_count ? (
            <span className="tag tag--muted">
              {report.page_count} {report.page_count === 1 ? "page" : "pages"}
            </span>
          ) : null}
          <span className="tag tag--muted">{formatBytes(report.size_bytes)}</span>
        </div>
      </div>

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
      {report.warnings.map((warning) => (
        <p key={warning} className="alert alert--warning">
          {warning}
        </p>
      ))}
    </li>
  );
}

export function ResultPanel({ result, onDownload, onReset }: Props) {
  const { report } = result;
  const included = report.files.filter((f) => f.output_filename !== null).length;
  const failed = report.files.length - included;

  return (
    <section className="panel" aria-labelledby="result-heading">
      <div className="panel__header">
        <h2 id="result-heading" className="panel__title">
          {report.candidate_name ?? "Candidate"} &middot; {included} filed
          {failed > 0 && `, ${failed} failed`}
        </h2>
        <span className="panel__meta">
          {(report.processing_ms / 1000).toFixed(1)}s &middot; {report.request_id.slice(0, 8)}
        </span>
      </div>

      <div className="actions">
        <button type="button" className="button button--primary" onClick={onDownload}>
          Download ZIP
        </button>
        <button type="button" className="button button--secondary" onClick={onReset}>
          Start over
        </button>
      </div>

      {report.missing_document_types.length > 0 && (
        <p className="alert alert--warning">
          <strong>Missing from this file:</strong>{" "}
          {report.missing_document_types.join(", ")}
        </p>
      )}
      {report.duplicate_document_types.length > 0 && (
        <p className="alert alert--warning">
          <strong>Unexpected duplicates:</strong>{" "}
          {report.duplicate_document_types.join(", ")}
        </p>
      )}
      {report.needs_review.length > 0 && (
        <p className="alert alert--warning">
          <strong>Check before accepting:</strong> {report.needs_review.join(", ")}
        </p>
      )}

      <ul className="result-list">
        {report.files.map((file, index) => (
          // Two uploads can share a name, so the index is part of the key.
          <FileRow key={`${file.original_filename}-${index}`} report={file} />
        ))}
      </ul>
    </section>
  );
}
