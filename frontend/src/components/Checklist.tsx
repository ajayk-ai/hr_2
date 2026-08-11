import { useMemo } from "react";

import { evaluateChecklist, evaluateOtherDocuments, type ItemResult, type ItemStatus } from "../checklist";
import type { FileReport } from "../types";

interface Props {
  files: FileReport[];
}

const STATUS_LABEL: Record<ItemStatus, string> = {
  received: "Received",
  missing: "Missing",
  partial: "Partial",
  deferred: "At joining",
};

function ItemRow({ result, countOnly }: { result: ItemResult; countOnly: boolean }) {
  const { item, count, status, remark } = result;

  return (
    <li className="checklist-item">
      <span className="checklist-item__label">{item.label}</span>
      {countOnly ? (
        <span className="tag tag--muted">{count} received</span>
      ) : (
        <span className={`badge badge--checklist-${status}`}>{STATUS_LABEL[status]}</span>
      )}
      {remark && <span className="checklist-item__remark">{remark}</span>}
    </li>
  );
}

/**
 * The candidate document checklist, organised into the categories HR reviews
 * a joining file by rather than the flat "missing types" list the API also
 * reports. Supersedes that flat list in this view -- showing both would mean
 * two differently-scoped opinions about completeness on screen at once (the
 * flat list, for instance, has no concept of "any one of these two", so it
 * would call Bank Details missing in cases this checklist correctly does not).
 */
export function Checklist({ files }: Props) {
  const categories = useMemo(() => evaluateChecklist(files), [files]);
  const others = useMemo(() => evaluateOtherDocuments(files), [files]);
  const totalOutstanding = categories.reduce((sum, c) => sum + c.outstanding, 0);

  return (
    <section className="checklist" aria-labelledby="checklist-heading">
      <div className="checklist__header">
        <h3 id="checklist-heading" className="result__subheading">
          Document checklist
        </h3>
        {totalOutstanding > 0 ? (
          <span className="tag tag--medium">
            {totalOutstanding} {totalOutstanding === 1 ? "item" : "items"} outstanding
          </span>
        ) : (
          <span className="tag tag--high">Complete</span>
        )}
      </div>

      <div className="checklist__grid">
        {categories.map(({ category, items }) => (
          <div key={category.id} className="checklist-category">
            <p className="checklist-category__title">{category.title}</p>
            {category.note && <p className="checklist-category__note">{category.note}</p>}
            <ul className="checklist-list">
              {items.map((result) => (
                <ItemRow key={result.item.label} result={result} countOnly={!!category.countOnly} />
              ))}
            </ul>
          </div>
        ))}
      </div>

      {others.length > 0 && (
        <div className="checklist-category checklist-category--other">
          <p className="checklist-category__title">
            Additional documents ({others.length})
          </p>
          <p className="checklist-category__note">
            Sent, classified and included in the ZIP, but not part of the checklist above.
          </p>
          <ul className="checklist-list">
            {others.map((file, index) => (
              // Two uploads can share a document type with no distinguishing
              // identifier, so the index is part of the key.
              <li key={`${file.output_filename}-${index}`} className="checklist-item">
                <span className="checklist-item__label">{file.document_type}</span>
                <code className="filename checklist-item__filename">{file.output_filename}</code>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
