// Review page (prototype screen 5, US-14): summary of the plan (status, metric status, revision,
// entities by layer), the plan's events and the decision bar (TransitionBar). The events are the
// first page of `GET /case-files/{id}/events?plan_id=`; they are reloaded after every transition.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api as defaultApi, type ApiClient } from "../../api/client";
import type { components } from "../../api/schema";
import type { PlanOut } from "../../editor/plan/planStore";
import { useAuth } from "../auth/authStore";
import { planEditorPath } from "../calibration/CalibrationPage";
import { METRIC_STATUS_LABELS, PLAN_MESSAGES as M, PLAN_STATUS_LABELS } from "./messages";
import { TransitionBar } from "./TransitionBar";

type EventOut = components["schemas"]["EventOut"];

/** Route of the review page of a plan. */
export function planReviewPath(caseId: string, planId: string): string {
  return `/cases/${encodeURIComponent(caseId)}/plans/${encodeURIComponent(planId)}/review`;
}

interface Review {
  plan: PlanOut;
  /** Entity count per layer of the current revision; `null` when it could not be loaded. */
  layers: [string, number][] | null;
  events: EventOut[];
}

type Loaded = { kind: "loading" } | { kind: "error" } | ({ kind: "ready" } & Review);

async function loadReview(api: Pick<ApiClient, "get">, planId: string): Promise<Review> {
  const plan = await api.get("/api/v1/plans/{plan_id}", { path: { plan_id: planId } });
  const revisionId = plan.current_revision_id;
  const [entities, events] = await Promise.all([
    revisionId === null
      ? Promise.resolve(null)
      : api
          .get("/api/v1/revisions/{revision_id}/entities", { path: { revision_id: revisionId } })
          .catch(() => null),
    api
      .get("/api/v1/case-files/{case_id}/events", {
        path: { case_id: plan.case_file_id },
        query: { plan_id: plan.id },
      })
      .catch(() => null),
  ]);
  const counts = new Map<string, number>();
  for (const entity of entities?.entities ?? []) {
    counts.set(entity.layer, (counts.get(entity.layer) ?? 0) + 1);
  }
  return {
    plan,
    layers: entities === null ? null : [...counts].sort(([a], [b]) => a.localeCompare(b)),
    events: events?.items ?? [],
  };
}

const page = { padding: 32, display: "flex", flexDirection: "column", gap: 16, maxWidth: 720 } as const;
const timeFormat = new Intl.DateTimeFormat("es", { dateStyle: "short", timeStyle: "short" });

export interface ReviewPageProps {
  api?: Pick<ApiClient, "get" | "post">;
}

export function ReviewPage({ api = defaultApi }: ReviewPageProps) {
  const { caseId = "", planId = "" } = useParams();
  const { user } = useAuth();
  const [state, setState] = useState<Loaded>({ kind: "loading" });

  /** Bumped after each transition to reload the plan and its events. */
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    loadReview(api, planId).then(
      (review) => active && setState({ kind: "ready", ...review }),
      () => active && setState({ kind: "error" }),
    );
    return () => {
      active = false;
    };
  }, [api, planId, version]);

  if (state.kind === "loading") return <p role="status">{M.loading}</p>;
  if (state.kind === "error") return <p role="alert">{M.loadFailed}</p>;
  const { plan, layers, events } = state;
  const summary: [string, string][] = [
    [M.status, PLAN_STATUS_LABELS[plan.status]],
    [M.metricStatus, METRIC_STATUS_LABELS[plan.metric_status]],
    [M.revision, plan.current_revision === null ? "—" : String(plan.current_revision.number)],
  ];

  return (
    <section aria-label={M.reviewTitle} style={page}>
      <h1 style={{ margin: 0, fontSize: 26 }}>
        {M.reviewTitle} · {plan.title}
      </h1>
      <Link to={planEditorPath(caseId, plan.id)}>{M.openEditor}</Link>
      <dl style={{ margin: 0 }}>
        {summary.map(([term, value]) => (
          <div key={term} style={{ display: "flex", gap: 8 }}>
            <dt>{term}</dt>
            <dd style={{ margin: 0, fontWeight: 600 }}>{value}</dd>
          </div>
        ))}
      </dl>
      {layers !== null && (
        <section aria-label={M.entitiesByLayer}>
          <h2 style={{ margin: 0, fontSize: 18 }}>{M.entitiesByLayer}</h2>
          {layers.length === 0 ? (
            <p>{M.noEntities}</p>
          ) : (
            <ul>
              {layers.map(([layer, count]) => (
                <li key={layer}>
                  {layer}: {count}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      <section aria-label={M.events}>
        <h2 style={{ margin: 0, fontSize: 18 }}>{M.events}</h2>
        {events.length === 0 ? (
          <p>{M.noEvents}</p>
        ) : (
          <ol>
            {events.map((event) => (
              <li key={event.id}>
                {timeFormat.format(new Date(event.occurred_at))} {event.kind}
                {event.reason !== null && ` · ${event.reason}`}
              </li>
            ))}
          </ol>
        )}
      </section>
      {user !== null && (
        <TransitionBar plan={plan} role={user.role} api={api} onTransition={() => setVersion((v) => v + 1)} />
      )}
    </section>
  );
}
