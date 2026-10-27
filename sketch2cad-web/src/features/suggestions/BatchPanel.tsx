// Batch review panel of the pending suggestions (US-12, REQ-17, jotdown H5), opened from the
// editor header. It counts the suggestions by confidence band (the highlight threshold of
// `suggestions.min_confidence_highlight`), accepts or rejects all of them, or one at a time, with
// `POST /revisions/{id}/entities:bulk-review`, and selects an item on the canvas when it is
// clicked. "Aceptar todas" / "Rechazar todas" send every pending id in one request (in chunks of
// BULK_REVIEW_MAX_IDS when there are more). It renders nothing unless AI assistance is on.
//
// Like the server-computed tools (editor/store/serverOps.ts) it sends the pending queue first and
// then reloads the revision's entities, so accepted suggestions become normal entities and
// rejected ones disappear; the reload clears the undo history. Reviewing needs the edit lease:
// without it the buttons are disabled and the panel says why.
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { ApiError, type ApiClient } from "../../api/client";
import type { components } from "../../api/schema";
import { holdsLease, type PlanStore } from "../../editor/plan/planStore";
import type { EditorStore } from "../../editor/store/entities";
import {
  SERVER_OPERATION_TEXT,
  flushFailure,
  reloadEntities,
  serverOperationMessage,
} from "../../editor/store/serverOps";
import { flushQueue } from "../../editor/store/sync";
import type { EntityId } from "../../editor/store/types";
import { problemCode } from "../plans/messages";
import { isLowConfidence, useAiAssistEnabled, useHighlightThreshold } from "./aiAssist";
import { selectSuggestionIds } from "./SuggestionLayer";

type ReviewStatus = components["schemas"]["BulkReviewIn"]["review_status"];

/** Most ids of one bulk-review request (the API's limit). */
export const BULK_REVIEW_MAX_IDS = 2000;

const ratio = (value: number) => value.toFixed(2).replace(".", ",");
/**
 * A confidence truncated (not rounded) to two decimals, so that the shown value agrees with its
 * band: 0.698 under a 0.70 threshold reads "0,69 · baja", not "0,70 · baja". The tiny epsilon
 * keeps values such as 0.29 (0.28999… in binary) from dropping a hundredth.
 */
const confidenceText = (value: number) => ratio(Math.floor(value * 100 + 1e-9) / 100);
const count = (n: number) => (n === 1 ? "1 sugerencia" : `${String(n)} sugerencias`);

export const BATCH_TEXT = {
  toggle: (n: number) => `Sugerencias (${String(n)})`,
  title: "Sugerencias de líneas",
  close: "Cerrar el panel de sugerencias",
  high: (t: number) => `Confianza alta (≥ ${ratio(t)})`,
  low: (t: number) => `Confianza baja (< ${ratio(t)}, resaltadas)`,
  acceptAll: "Aceptar todas",
  rejectAll: "Rechazar todas",
  accept: "Aceptar",
  reject: "Rechazar",
  item: (n: number) => `Sugerencia ${String(n)}`,
  acceptItem: (n: number) => `Aceptar sugerencia ${String(n)}`,
  rejectItem: (n: number) => `Rechazar sugerencia ${String(n)}`,
  confidence: (c: number | null) =>
    c === null ? "confianza desconocida" : `confianza ${confidenceText(c)}`,
  lowTag: "baja",
  none: "No hay sugerencias pendientes.",
  needsLease: "Para aceptar o rechazar sugerencias necesitas el permiso de edición del plano.",
  working: "Aplicando la revisión…",
  done: (status: ReviewStatus, changed: number, unchanged: number) =>
    `${status === "accepted" ? "Aceptadas" : "Rechazadas"}: ${count(changed)}.` +
    (unchanged > 0 ? ` ${count(unchanged)} ya lo estaban.` : ""),
  invalid: (n: number) =>
    `${count(n)} ya no se pueden revisar (cambiaron o se eliminaron); no se aplicó ningún cambio. ` +
    "Recarga la página para ver el estado actual.",
  partial: (changed: number) =>
    `Se revisaron ${count(changed)} antes del error; el resto no se aplicó.`,
} as const;

interface Pending {
  id: EntityId;
  confidence: number | null;
  low: boolean;
}

type Outcome =
  | { kind: "done"; text: string }
  | { kind: "error"; text: string };

/** Spanish message of a failed bulk review. */
export function bulkReviewMessage(error: unknown): string {
  if (
    error instanceof ApiError &&
    error.status === 422 &&
    problemCode(error) === "bulk_review_invalid"
  ) {
    const ids = error.problem.invalid_ids;
    return BATCH_TEXT.invalid(Array.isArray(ids) ? ids.length : 0);
  }
  return serverOperationMessage(error);
}

export interface BatchPanelProps {
  api: Pick<ApiClient, "get" | "post" | "patch" | "delete">;
  store: StoreApi<EditorStore>;
  plan: StoreApi<PlanStore>;
}

export function BatchPanel({ api, store, plan }: BatchPanelProps) {
  const enabled = useAiAssistEnabled(api);
  const threshold = useHighlightThreshold(api, !enabled);
  const ids = useStore(store, selectSuggestionIds);
  const byId = useStore(store, (s) => s.entities.byId);
  const selection = useStore(store, (s) => s.selection);
  const canReview = useStore(plan, (s) => !s.readOnly && holdsLease(s));
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const panelId = useId();
  const hintId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);

  // Esc closes the panel and gives the focus back to the header button, so it can always be hidden.
  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key !== "Escape") return;
      setOpen(false);
      toggleRef.current?.focus();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const pending = useMemo<Pending[]>(
    () =>
      ids
        .map((id) => {
          const confidence = byId[id]?.remote?.confidence ?? null;
          return { id, confidence, low: isLowConfidence(confidence, threshold) };
        })
        .sort((a, b) => (a.confidence ?? -1) - (b.confidence ?? -1)),
    [ids, byId, threshold],
  );

  if (!enabled) return null;
  const lowCount = pending.filter((item) => item.low).length;
  const disabled = busy || !canReview;

  async function review(reviewIds: readonly EntityId[], status: ReviewStatus): Promise<void> {
    const revisionId = plan.getState().revisionId ?? store.getState().revisionId;
    if (revisionId === null || reviewIds.length === 0) return;
    setBusy(true);
    setOutcome(null);
    const context = { revisionId };
    let changed = 0;
    let unchanged = 0;
    let failure: string | null = null;
    const flushed = await flushQueue(api, store, context);
    if (flushed.status !== "drained") failure = flushFailure(flushed).message;
    for (let start = 0; failure === null && start < reviewIds.length; start += BULK_REVIEW_MAX_IDS) {
      try {
        const result = await api.post("/api/v1/revisions/{revision_id}/entities:bulk-review", {
          path: { revision_id: revisionId },
          body: { ids: reviewIds.slice(start, start + BULK_REVIEW_MAX_IDS), review_status: status },
        });
        changed += result.changed;
        unchanged += result.unchanged;
      } catch (error) {
        failure = bulkReviewMessage(error) + (changed > 0 ? ` ${BATCH_TEXT.partial(changed)}` : "");
      }
    }
    if (failure === null || changed > 0) {
      const reloaded = await reloadEntities(api, store, context);
      if (!reloaded.ok) failure = failure ?? reloaded.message;
    }
    setBusy(false);
    setOutcome(
      failure === null
        ? { kind: "done", text: BATCH_TEXT.done(status, changed, unchanged) }
        : { kind: "error", text: failure },
    );
  }

  const describedBy = canReview ? undefined : hintId;
  return (
    <div style={{ position: "relative" }}>
      <button
        ref={toggleRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        {BATCH_TEXT.toggle(pending.length)}
      </button>
      {open ? (
        <section
          id={panelId}
          aria-label={BATCH_TEXT.title}
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 4px)",
            zIndex: 10,
            width: 380,
            maxHeight: "70vh",
            overflowY: "auto",
            background: "#fff",
            border: "1px solid #D5DCE3",
            borderRadius: 6,
            padding: 12,
            fontSize: 13,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ fontSize: 14, margin: "0 0 6px" }}>{BATCH_TEXT.title}</h2>
            <button
              type="button"
              aria-label={BATCH_TEXT.close}
              title={BATCH_TEXT.close}
              onClick={() => {
                setOpen(false);
                toggleRef.current?.focus();
              }}
            >
              {"\u00d7"}
            </button>
          </div>
          <dl style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: "2px 12px", margin: 0 }}>
            <dt>{BATCH_TEXT.high(threshold)}</dt>
            <dd data-testid="band-high" style={{ margin: 0 }}>
              {pending.length - lowCount}
            </dd>
            <dt>{BATCH_TEXT.low(threshold)}</dt>
            <dd data-testid="band-low" style={{ margin: 0 }}>
              {lowCount}
            </dd>
          </dl>
          {canReview ? null : (
            <p id={hintId} style={{ color: "#7A3E00" }}>
              {BATCH_TEXT.needsLease}
            </p>
          )}
          <p style={{ color: "#555" }}>{SERVER_OPERATION_TEXT.noUndo}</p>
          <div style={{ display: "flex", gap: 8, margin: "8px 0" }}>
            <button
              type="button"
              disabled={disabled || pending.length === 0}
              aria-describedby={describedBy}
              onClick={() => void review(pending.map((item) => item.id), "accepted")}
            >
              {BATCH_TEXT.acceptAll}
            </button>
            <button
              type="button"
              disabled={disabled || pending.length === 0}
              aria-describedby={describedBy}
              onClick={() => void review(pending.map((item) => item.id), "rejected")}
            >
              {BATCH_TEXT.rejectAll}
            </button>
          </div>
          <p role="status" aria-live="polite" style={{ margin: "4px 0" }}>
            {busy ? BATCH_TEXT.working : outcome?.kind === "done" ? outcome.text : null}
          </p>
          {outcome?.kind === "error" ? (
            <p role="alert" style={{ color: "#8C1D18", margin: "4px 0" }}>
              {outcome.text}
            </p>
          ) : null}
          {pending.length === 0 ? (
            <p>{BATCH_TEXT.none}</p>
          ) : (
            <ol aria-label={BATCH_TEXT.title} style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {pending.map((item, index) => (
                <li
                  key={item.id}
                  className={item.low ? "suggestion-item suggestion--low-confidence" : "suggestion-item"}
                  style={{ display: "flex", gap: 4, alignItems: "center", margin: "2px 0" }}
                >
                  <button
                    type="button"
                    aria-pressed={selection.includes(item.id)}
                    onClick={() => store.getState().select([item.id])}
                    style={{
                      flex: 1,
                      textAlign: "left",
                      background: item.low ? "#FFF6BF" : "#fff",
                      border: "1px solid #C3CDD6",
                      borderRadius: 6,
                      padding: "4px 8px",
                    }}
                  >
                    <b>{BATCH_TEXT.item(index + 1)}</b>
                    {` · ${BATCH_TEXT.confidence(item.confidence)}`}
                    {item.low ? ` · ${BATCH_TEXT.lowTag}` : null}
                  </button>
                  <button
                    type="button"
                    aria-label={BATCH_TEXT.acceptItem(index + 1)}
                    aria-describedby={describedBy}
                    disabled={disabled}
                    onClick={() => void review([item.id], "accepted")}
                  >
                    {BATCH_TEXT.accept}
                  </button>
                  <button
                    type="button"
                    aria-label={BATCH_TEXT.rejectItem(index + 1)}
                    aria-describedby={describedBy}
                    disabled={disabled}
                    onClick={() => void review([item.id], "rejected")}
                  >
                    {BATCH_TEXT.reject}
                  </button>
                </li>
              ))}
            </ol>
          )}
        </section>
      ) : null}
    </div>
  );
}
