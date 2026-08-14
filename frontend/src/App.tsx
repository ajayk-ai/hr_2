import { useState } from "react";

import { FileDropzone } from "./components/FileDropzone";
import { FileList } from "./components/FileList";
import { ProcessingStatus } from "./components/ProcessingStatus";
import { ResultPanel } from "./components/ResultPanel";
import { useDocumentProcessor } from "./hooks/useDocumentProcessor";

export default function App() {
  const [candidateName, setCandidateName] = useState("");
  const {
    queue,
    rejected,
    status,
    error,
    result,
    addFiles,
    removeFile,
    process,
    cancel,
    reset,
    download,
  } = useDocumentProcessor();

  const busy = status === "processing";

  return (
    <main className="page">
      <header className="page__header">
        <div className="brand">
          <img src="/bulllogo.png" alt="" className="brand__logo" />
          <div>
            <h1 className="page__title">Bull Doc Genie</h1>
            <p className="page__subtitle">
              Upload one candidate&rsquo;s documents. They are classified, renamed and
              returned as an ordered ZIP. Nothing is stored on the server.
            </p>
          </div>
        </div>
      </header>

      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          void process(candidateName);
        }}
      >
        <FileDropzone onFiles={addFiles} disabled={busy} />

        {rejected.length > 0 && (
          <ul className="alert alert--warning" aria-live="polite">
            {rejected.map((item) => (
              <li key={item.name}>
                <strong>{item.name}</strong> &mdash; {item.reason}
              </li>
            ))}
          </ul>
        )}

        <FileList files={queue} onRemove={removeFile} disabled={busy} />

        <div className="field">
          <label className="field__label" htmlFor="candidate-name">
            Candidate name <span className="field__optional">(optional)</span>
          </label>
          <input
            id="candidate-name"
            className="field__input"
            type="text"
            value={candidateName}
            disabled={busy}
            placeholder="Read from the documents if left blank"
            onChange={(event) => setCandidateName(event.target.value)}
          />
          <p className="field__help">
            Setting this overrides the name read from the documents, which is useful
            when a scan is poor or the spelling is inconsistent across files.
          </p>
        </div>

        {busy ? (
          <ProcessingStatus fileCount={queue.length} onCancel={cancel} />
        ) : (
          <div className="actions">
            <button
              type="submit"
              className="button button--primary"
              disabled={queue.length === 0}
            >
              {queue.length === 0
                ? "Process files"
                : `Process ${queue.length} ${queue.length === 1 ? "file" : "files"}`}
            </button>
            {queue.length > 0 && (
              <button type="button" className="button button--ghost" onClick={reset}>
                Clear
              </button>
            )}
          </div>
        )}

        {error && (
          <p className="alert alert--error" role="alert">
            {error}
          </p>
        )}
      </form>

      {result && <ResultPanel result={result} onDownload={download} onReset={reset} />}
    </main>
  );
}
