import type { QueuedFile } from "../types";

interface Props {
  files: QueuedFile[];
  onRemove: (id: string) => void;
  disabled: boolean;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function FileList({ files, onRemove, disabled }: Props) {
  if (!files.length) return null;

  const total = files.reduce((sum, item) => sum + item.file.size, 0);

  return (
    <section className="panel" aria-labelledby="queue-heading">
      <div className="panel__header">
        <h2 id="queue-heading" className="panel__title">
          {files.length} {files.length === 1 ? "file" : "files"} ready
        </h2>
        <span className="panel__meta">{formatBytes(total)} total</span>
      </div>
      <ul className="file-list">
        {files.map(({ id, file }) => (
          <li key={id} className="file-list__item">
            <span className="file-list__name" title={file.name}>
              {file.name}
            </span>
            <span className="file-list__size">{formatBytes(file.size)}</span>
            <button
              type="button"
              className="button button--ghost"
              disabled={disabled}
              onClick={() => onRemove(id)}
              aria-label={`Remove ${file.name}`}
            >
              Remove
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
