import { useEffect, useState } from "react";

interface Props {
  fileCount: number;
  onCancel: () => void;
}

/**
 * Live feedback while the batch is in flight.
 *
 * The request is synchronous and can run for tens of seconds on a large batch,
 * with no server-sent progress to report. A static "Processing…" line is
 * indistinguishable from a hung request at that length, so this shows a running
 * clock instead: the number moving is the evidence that work is still happening.
 *
 * The bar is deliberately indeterminate. A percentage would have to be invented,
 * and a progress bar that lies is worse than one that only says "still going".
 */
export function ProcessingStatus({ fileCount, onCancel }: Props) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  // Roughly a second per page of OCR plus a model call. Only shown once the wait
  // is long enough to worry about, so it reassures rather than sets expectations
  // it might miss.
  const slow = elapsed >= 20;

  return (
    <section className="processing" aria-live="polite">
      <div className="processing__bar" role="presentation">
        <div className="processing__bar-fill" />
      </div>

      <div className="processing__body">
        <span className="spinner" aria-hidden="true" />
        <div className="processing__text">
          <p className="processing__title">
            Reading {fileCount} {fileCount === 1 ? "document" : "documents"}…
          </p>
          <p className="processing__detail">
            OCR and classification run in parallel.{" "}
            {slow
              ? "Multi-page PDFs take longer — the connection stays open until every file is done."
              : "This usually takes a few seconds."}
          </p>
        </div>
        <span className="processing__clock" aria-label={`${elapsed} seconds elapsed`}>
          {formatElapsed(elapsed)}
        </span>
        <button type="button" className="button button--secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </section>
  );
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}
