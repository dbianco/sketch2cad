// Metric status badge of the editor header (US-11, REQ-07, spec section 11.3). It reads
// `GET /plans/{id}/metric-status` and opens a panel with the entities that keep the plan from
// `metric` (type, layer and reason, plus the explanation of a failed constraint) and the
// dimensions whose declared and measured values disagree. Clicking an item selects the entity on
// the canvas when the editor store has it.
//
// The status is read again whenever the store settles: when its queue drains (the edits reached
// the server) and when its entities are replaced with an empty queue (`load`, which the
// server-computed operations of serverOps.ts call after they apply).
import { useEffect, useId, useState } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import type { ApiClient } from "../../api/client";
import type { components } from "../../api/schema";
import type { EditorStore } from "../../editor/store/entities";
import type { EditorEntity } from "../../editor/store/types";
import {
  CONSTRAINT_ERROR_LABELS,
  ENTITY_TYPE_LABELS,
  METRIC_PANEL_MESSAGES as M,
  METRIC_REASON_LABELS,
  METRIC_STATUS_LABELS,
  type MetricStatus,
} from "./messages";

export type MetricStatusOut = components["schemas"]["MetricStatusOut"];

interface BadgeStyle {
  background: string;
  color: string;
  icon: string;
}

/** Colour and icon of each status; the text says the status too, so colour is never alone. */
export const METRIC_BADGE_STYLES: Readonly<Record<MetricStatus, BadgeStyle>> = {
  metric: { background: "#DDF3E4", color: "#1B5E20", icon: "✓" },
  approximate: { background: "#FDEBC8", color: "#7A3E00", icon: "!" },
  non_metric: { background: "#F9DEDC", color: "#8C1D18", icon: "✕" },
};

/** Text of the badge for `status`, e.g. "Estado métrico: Aproximado". */
export function metricBadgeText(status: MetricStatus): string {
  return `${M.badgePrefix}: ${METRIC_STATUS_LABELS[status]}`;
}

/** Explanation of the entity's failed constraint (`attributes.constraint_error`), if any. */
export function constraintErrorText(entity: EditorEntity | undefined): string | null {
  const code = entity?.attributes.constraint_error;
  if (typeof code !== "string") return null;
  return CONSTRAINT_ERROR_LABELS[code] ?? CONSTRAINT_ERROR_LABELS.invalid_constraint ?? null;
}

type Loaded = { kind: "loading" } | { kind: "error" } | { kind: "ready"; value: MetricStatusOut };

/** Read the plan's metric status now and every time the store settles. */
function useMetricStatus(
  api: Pick<ApiClient, "get">,
  store: StoreApi<EditorStore>,
  planId: string,
): Loaded {
  const [state, setState] = useState<Loaded>({ kind: "loading" });

  useEffect(() => {
    let disposed = false;
    let latest = 0;
    async function fetchStatus(): Promise<void> {
      latest += 1;
      const request = latest;
      try {
        const value = await api.get("/api/v1/plans/{plan_id}/metric-status", {
          path: { plan_id: planId },
        });
        if (!disposed && request === latest) setState({ kind: "ready", value });
      } catch {
        if (!disposed && request === latest) setState({ kind: "error" });
      }
    }
    void fetchStatus();
    const unsubscribe = store.subscribe((next, prev) => {
      if (next.queue.ops.length > 0) return;
      const drained = prev.queue.ops.length > 0;
      const reloaded = next.entities !== prev.entities && next.history.past.length === 0;
      if (drained || reloaded) void fetchStatus();
    });
    return () => {
      disposed = true;
      unsubscribe();
    };
  }, [api, store, planId]);

  return state;
}

export interface MetricStatusPanelProps {
  planId: string;
  api: Pick<ApiClient, "get">;
  store: StoreApi<EditorStore>;
}

export function MetricStatusPanel({ planId, api, store }: MetricStatusPanelProps) {
  const state = useMetricStatus(api, store, planId);
  const byId = useStore(store, (s) => s.entities.byId);
  const [open, setOpen] = useState(false);
  const panelId = useId();

  if (state.kind === "loading") return <span role="status">{M.loading}</span>;
  if (state.kind === "error") return <span role="alert">{M.loadFailed}</span>;
  const { status, blocking_entities, reasons, discrepant_dimensions } = state.value;
  const style = METRIC_BADGE_STYLES[status];

  const select = (id: string) => {
    if (byId[id] !== undefined) store.getState().select([id]);
  };

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        style={{
          padding: "3px 10px",
          border: `1px solid ${style.color}`,
          borderRadius: 12,
          background: style.background,
          color: style.color,
          fontSize: 13,
          cursor: "pointer",
        }}
      >
        <span aria-hidden="true" style={{ fontWeight: 700, marginRight: 6 }}>
          {style.icon}
        </span>
        {metricBadgeText(status)}
      </button>
      {open ? (
        <section
          id={panelId}
          aria-label={M.panelTitle}
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 4px)",
            zIndex: 10,
            width: 360,
            maxHeight: 420,
            overflowY: "auto",
            background: "#fff",
            border: "1px solid #D5DCE3",
            borderRadius: 6,
            padding: 12,
            fontSize: 13,
          }}
        >
          {status === "non_metric" ? <p>{M.nonMetric}</p> : null}
          {status === "metric" ? <p>{M.metric}</p> : null}
          {status === "approximate" ? (
            <EntityList
              title={M.blocking}
              empty={M.noBlocking}
              ids={blocking_entities}
              byId={byId}
              onSelect={select}
              detail={(id) => METRIC_REASON_LABELS[reasons[id] ?? ""] ?? reasons[id] ?? ""}
            />
          ) : null}
          {discrepant_dimensions.length > 0 ? (
            <EntityList
              title={M.discrepant}
              empty=""
              ids={discrepant_dimensions}
              byId={byId}
              onSelect={select}
              detail={() => M.discrepantItem}
            />
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

interface EntityListProps {
  title: string;
  empty: string;
  ids: readonly string[];
  byId: Readonly<Record<string, EditorEntity>>;
  onSelect(id: string): void;
  detail(id: string): string;
}

function EntityList({ title, empty, ids, byId, onSelect, detail }: EntityListProps) {
  const headingId = useId();
  return (
    <>
      <h2 id={headingId} style={{ fontSize: 14, margin: "4px 0" }}>
        {title}
      </h2>
      {ids.length === 0 ? (
        <p>{empty}</p>
      ) : (
        <ul aria-labelledby={headingId} style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {ids.map((id) => {
            const entity = byId[id];
            const explanation = constraintErrorText(entity);
            return (
              <li key={id}>
                <button
                  type="button"
                  onClick={() => onSelect(id)}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    padding: "6px 8px",
                    margin: "2px 0",
                    border: "1px solid #C3CDD6",
                    borderRadius: 6,
                    background: "#fff",
                    cursor: "pointer",
                  }}
                >
                  <b>{entity === undefined ? M.unknownEntity : ENTITY_TYPE_LABELS[entity.type]}</b>
                  {entity === undefined ? null : ` · ${entity.layer}`}
                  {` · ${detail(id)}`}
                  {explanation === null ? null : (
                    <span style={{ display: "block", color: "#8C1D18" }}>{explanation}</span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
