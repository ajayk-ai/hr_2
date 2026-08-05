import { useCallback, useRef, useState, type DragEvent } from "react";

interface Props {
  onFiles: (files: FileList | File[]) => void;
  disabled: boolean;
}

/**
 * Drag-and-drop target that is also a real button.
 *
 * The hidden `<input type="file">` is what makes this keyboard- and
 * screen-reader-operable; the drop handling is an enhancement on top of it
 * rather than the only way in.
 */
export function FileDropzone({ onFiles, disabled }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDraggingOver, setDraggingOver] = useState(false);

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setDraggingOver(false);
      if (disabled) return;
      if (event.dataTransfer.files.length) onFiles(event.dataTransfer.files);
    },
    [disabled, onFiles],
  );

  return (
    <div
      className={`dropzone${isDraggingOver ? " dropzone--active" : ""}${
        disabled ? " dropzone--disabled" : ""
      }`}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setDraggingOver(true);
      }}
      onDragLeave={() => setDraggingOver(false)}
      onDrop={handleDrop}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        className="visually-hidden"
        accept="application/pdf,image/jpeg,image/png,image/gif,image/tiff,image/bmp,image/webp"
        disabled={disabled}
        onChange={(event) => {
          if (event.target.files?.length) onFiles(event.target.files);
          // Reset so re-selecting the same file still fires a change event.
          event.target.value = "";
        }}
      />
      <p className="dropzone__title">Drop the candidate&rsquo;s documents here</p>
      <p className="dropzone__hint">
        PDF, JPEG, PNG, TIFF, BMP, WEBP or GIF &middot; up to 25 files &middot; 20 MB each
      </p>
      <button
        type="button"
        className="button button--secondary"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        Browse files
      </button>
    </div>
  );
}
