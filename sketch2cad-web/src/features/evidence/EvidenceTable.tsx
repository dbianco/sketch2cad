// Evidence table of a plan (US-10, REQ-08, spec section 8.3). The register lives at the case file
// (`GET/POST /case-files/{id}/evidence`, `PATCH /evidence/{id}`); this plan places an item with a
// point or symbol entity whose `evidence_id` names it. The table joins the register with the
// editor store's entities: one row per placement (number, label, local X/Y in metres, metric
// origin) and one "sin ubicar" row per item that this plan does not place.
//
// The number shown is always the register's; the server copies it into the `evidence_number`
// attribute of a marker symbol that places the item (on create and relabel). Assigning an item to the selected point or symbol is a
// `relabel` with `evidence_id`, a field the store does not model, so it goes through
// runServerOperation, which reloads the entities afterwards.
import { useEffect, useId, useState, type FormEvent } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import type { components } from "../../api/schema";
import type { PlanStore } from "../../editor/plan/planStore";
import type { EditorStore } from "../../editor/store/entities";
import { runServerOperation, type ServerOperationApi } from "../../editor/store/serverOps";
import type { EditorEntity, EntityId, Point2 } from "../../editor/store/types";
import { EVIDENCE_MESSAGES as M, METRIC_ORIGIN_LABELS, type MetricOrigin } from "./messages";

export type EvidenceOut = components["schemas"]["EvidenceOut"];

/** Metres with exactly two decimals and a decimal comma: 12.5 -> "12,50", -0.5 -> "-0,50". */
export function formatMetres(value: number): string {
  const text = value.toFixed(2);
  return (/^-0\.00$/.test(text) ? "0.00" : text).replace(".", ",");
}

/** Position of a point or symbol entity in the local frame; null for other types. */
export function placementOf(entity: EditorEntity): Point2 | null {
  const geometry = entity.geometry;
  if (geometry.kind === "point") return geometry.coords_m[0] ?? null;
  if (geometry.kind === "symbol") return geometry.anchor_m;
  return null;
}

export interface EvidenceRow {
  key: string;
  evidence: EvidenceOut;
  /** Placing entity; null for evidence this plan does not place. */
  entityId: EntityId | null;
  position: Point2 | null;
  origin: MetricOrigin | null;
}

/** Rows of the table: the register by number, each item once per placing entity. */
export function evidenceRows(
  evidence: readonly EvidenceOut[],
  entities: Readonly<Record<EntityId, EditorEntity>>,
  order: readonly EntityId[],
): EvidenceRow[] {
  const placed = new Map<string, EditorEntity[]>();
  for (const id of order) {
    const entity = entities[id];
    const evidenceId = entity?.remote?.evidence_id;
    if (entity === undefined || entity.deleted || evidenceId == null) continue;
    placed.set(evidenceId, [...(placed.get(evidenceId) ?? []), entity]);
  }
  return [...evidence]
    .sort((a, b) => a.number - b.number)
    .flatMap((item): EvidenceRow[] => {
      const placements = placed.get(item.id) ?? [];
      if (placements.length === 0) {
        return [{ key: item.id, evidence: item, entityId: null, position: null, origin: null }];
      }
      return placements.map((entity) => ({
        key: `${item.id}:${entity.id}`,
        evidence: item,
        entityId: entity.id,
        position: placementOf(entity),
        origin: entity.remote?.metric_origin ?? "none",
      }));
    });
}

export interface EvidenceTableProps {
  caseId: string;
  api: ServerOperationApi;
  store: StoreApi<EditorStore>;
  plan: StoreApi<PlanStore>;
}

export function EvidenceTable({ caseId, api, store, plan }: EvidenceTableProps) {
  const [evidence, setEvidence] = useState<EvidenceOut[] | "loading" | "error">("loading");
  const [message, setMessage] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ key: string; label: string } | null>(null);
  const [newLabel, setNewLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const byId = useStore(store, (s) => s.entities.byId);
  const order = useStore(store, (s) => s.entities.order);
  const selection = useStore(store, (s) => s.selection);
  const readOnly = useStore(plan, (s) => s.readOnly);
  const revisionId = useStore(plan, (s) => s.revisionId);
  const titleId = useId();
  const newLabelId = useId();

  const [reloads, setReloads] = useState(0);
  const reload = () => setReloads((count) => count + 1);

  useEffect(() => {
    let active = true;
    async function load(): Promise<void> {
      try {
        const list = await api.get("/api/v1/case-files/{case_id}/evidence", {
          path: { case_id: caseId },
        });
        if (active) setEvidence(list.items);
      } catch {
        if (active) setEvidence("error");
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [api, caseId, reloads]);

  const selected = selection.length === 1 ? byId[selection[0] ?? ""] : undefined;
  const canAssign =
    !readOnly &&
    revisionId !== null &&
    selected !== undefined &&
    !selected.deleted &&
    (selected.type === "point" || selected.type === "symbol");

  async function run(action: () => Promise<string | null>): Promise<void> {
    setBusy(true);
    setMessage(null);
    const failure = await action();
    setMessage(failure);
    setBusy(false);
  }

  const create = (event: FormEvent) => {
    event.preventDefault();
    const label = newLabel.trim();
    if (label === "") {
      setMessage(M.labelRequired);
      return;
    }
    void run(async () => {
      try {
        await api.post("/api/v1/case-files/{case_id}/evidence", {
          path: { case_id: caseId },
          body: { label },
        });
      } catch {
        return M.saveFailed;
      }
      setNewLabel("");
      reload();
      return null;
    });
  };

  const saveLabel = (item: EvidenceOut, label: string) => {
    if (label.trim() === "") {
      setMessage(M.labelRequired);
      return;
    }
    void run(async () => {
      try {
        await api.patch("/api/v1/evidence/{evidence_id}", {
          path: { evidence_id: item.id },
          body: { label: label.trim() },
        });
      } catch {
        return M.saveFailed;
      }
      setEditing(null);
      reload();
      return null;
    });
  };

  const assign = (item: EvidenceOut) => {
    if (!canAssign || selected === undefined || revisionId === null) return;
    const target = selected.id;
    void run(async () => {
      const result = await runServerOperation(api, store, { revisionId }, target, {
        operation: "relabel",
        evidence_id: item.id,
      });
      return result.ok ? null : result.message;
    });
  };

  const select = (row: EvidenceRow) => {
    if (row.entityId !== null) store.getState().select([row.entityId]);
  };

  if (evidence === "loading") return <p role="status">{M.loading}</p>;
  if (evidence === "error") return <p role="alert">{M.loadFailed}</p>;
  const rows = evidenceRows(evidence, byId, order);

  return (
    <section aria-labelledby={titleId} style={{ fontSize: 13 }}>
      <h2 id={titleId} style={{ fontSize: 15, margin: "4px 0" }}>
        {M.title}
      </h2>
      {message === null ? null : <p role="alert">{message}</p>}
      {rows.length === 0 ? (
        <p>{M.empty}</p>
      ) : (
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr>
              <th scope="col">{M.number}</th>
              <th scope="col">{M.label}</th>
              <th scope="col">{M.x}</th>
              <th scope="col">{M.y}</th>
              <th scope="col">{M.origin}</th>
              <th scope="col">{M.actions}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const n = row.evidence.number;
              const isEditing = editing?.key === row.key;
              return (
                <tr
                  key={row.key}
                  onClick={() => select(row)}
                  style={{
                    cursor: row.entityId === null ? "default" : "pointer",
                    background:
                      row.entityId !== null && selection.includes(row.entityId) ? "#E3F1F5" : undefined,
                  }}
                >
                  <th scope="row">
                    {row.entityId === null ? (
                      n
                    ) : (
                      <button type="button" aria-label={M.selectRow(n)}>
                        {n}
                      </button>
                    )}
                  </th>
                  <td>
                    {isEditing ? (
                      <input
                        aria-label={M.newLabel(n)}
                        value={editing.label}
                        onClick={(event) => event.stopPropagation()}
                        onChange={(event) => setEditing({ key: row.key, label: event.target.value })}
                      />
                    ) : (
                      row.evidence.label
                    )}
                  </td>
                  <td>{row.position === null ? M.unplaced : formatMetres(row.position[0])}</td>
                  <td>{row.position === null ? M.unplaced : formatMetres(row.position[1])}</td>
                  <td>{row.origin === null ? M.unplaced : METRIC_ORIGIN_LABELS[row.origin]}</td>
                  <td onClick={(event) => event.stopPropagation()}>
                    {isEditing ? (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => saveLabel(row.evidence, editing.label)}
                        >
                          {M.save}
                        </button>
                        <button type="button" onClick={() => setEditing(null)}>
                          {M.cancel}
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => setEditing({ key: row.key, label: row.evidence.label })}
                      >
                        {M.editLabel}
                      </button>
                    )}
                    <button
                      type="button"
                      aria-label={`${M.assign}: ${String(n)}`}
                      title={canAssign ? undefined : M.assignHint}
                      disabled={busy || !canAssign}
                      onClick={() => assign(row.evidence)}
                    >
                      {M.assign}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <form onSubmit={create} aria-label={M.addTitle} style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <label htmlFor={newLabelId}>{M.addLabel}</label>
        <input
          id={newLabelId}
          value={newLabel}
          aria-required="true"
          onChange={(event) => setNewLabel(event.target.value)}
        />
        <button type="submit" disabled={busy}>
          {M.add}
        </button>
      </form>
    </section>
  );
}
