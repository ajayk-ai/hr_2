import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, downloadBlob, processDocuments } from "../api/client";
import type { ProcessingResult, QueuedFile } from "../types";

export type Status = "idle" | "processing" | "done" | "error";

const MAX_FILES = 25;
const MAX_FILE_BYTES = 20 * 1024 * 1024;

/** Client-side mirror of the backend's allowlist, for immediate feedback. */
const ACCEPTED_TYPES = new Set([
  "application/pdf",
  "image/jpeg",
  "image/png",
  "image/gif",
  "image/tiff",
  "image/bmp",
  "image/webp",
]);

export interface RejectedFile {
  name: string;
  reason: string;
}

let nextId = 0;

/**
 * Owns the upload queue and the request lifecycle.
 *
 * Validation is duplicated here on purpose: rejecting a 200 MB video in the
 * browser is instant, where the server can only reject it after the upload has
 * already crossed the wire. The server remains the authority.
 */
export function useDocumentProcessor() {
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [rejected, setRejected] = useState<RejectedFile[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ProcessingResult | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // An unmount mid-request would otherwise leave the upload running and the
  // response landing on a dead component.
  useEffect(() => () => abortRef.current?.abort(), []);

  const addFiles = useCallback(
    (incoming: FileList | File[]) => {
      // Validation happens here rather than inside the `setQueue` updater: React
      // may invoke an updater more than once (StrictMode does so deliberately),
      // so an updater that appends to arrays outside itself would double-count.
      const accepted: QueuedFile[] = [];
      const refused: RejectedFile[] = [];
      const seen = new Set(queue.map((q) => `${q.file.name}:${q.file.size}`));

      for (const file of Array.from(incoming)) {
        const key = `${file.name}:${file.size}`;
        if (seen.has(key)) {
          refused.push({ name: file.name, reason: "Already added." });
        } else if (file.size === 0) {
          refused.push({ name: file.name, reason: "File is empty." });
        } else if (file.size > MAX_FILE_BYTES) {
          refused.push({ name: file.name, reason: "Larger than 20 MB." });
        } else if (file.type && !ACCEPTED_TYPES.has(file.type)) {
          // An empty `type` means the OS had no mapping; let the server sniff it.
          refused.push({ name: file.name, reason: "Unsupported file type." });
        } else if (queue.length + accepted.length >= MAX_FILES) {
          refused.push({ name: file.name, reason: `Limit of ${MAX_FILES} files reached.` });
        } else {
          seen.add(key);
          accepted.push({ id: `f${nextId++}`, file });
        }
      }

      if (accepted.length) setQueue((current) => [...current, ...accepted]);
      setRejected(refused);
    },
    [queue],
  );

  const removeFile = useCallback((id: string) => {
    setQueue((current) => current.filter((item) => item.id !== id));
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setQueue([]);
    setRejected([]);
    setResult(null);
    setError(null);
    setStatus("idle");
  }, []);

  const process = useCallback(
    async (candidateName: string, prNumber: string) => {
      if (!queue.length) return;

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setStatus("processing");
      setError(null);
      setResult(null);

      try {
        const outcome = await processDocuments(
          queue.map((item) => item.file),
          { candidateName, prNumber, signal: controller.signal },
        );
        setResult(outcome);
        setStatus("done");
      } catch (cause) {
        if (controller.signal.aborted) {
          setStatus("idle");
          return;
        }
        setError(
          cause instanceof ApiError
            ? cause.message
            : "Could not reach the server. Check that the API is running.",
        );
        setStatus("error");
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [queue],
  );

  const cancel = useCallback(() => abortRef.current?.abort(), []);

  const download = useCallback(() => {
    if (result) downloadBlob(result.archive, result.suggestedFilename);
  }, [result]);

  return {
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
  };
}
