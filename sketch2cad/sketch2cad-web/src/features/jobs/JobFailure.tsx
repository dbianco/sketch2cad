// Failure of a background job (REQ-15, US-18, H5-6): the `cause` and the `action` of a failed
// `JobOut` (`error`), announced as an alert, with a button to dismiss it. It only reports: it
// disables nothing, so the plan stays editable after a failed job. A failed job without `error`
// gets generic texts, so a failure is never shown as an empty or reassuring message.
import type { ReactNode } from "react";
import type { components } from "../../api/schema";

export type JobError = components["schemas"]["JobErrorOut"];
type JobOut = components["schemas"]["JobOut"];

export const JOB_FAILURE_TEXT = {
  title: "El trabajo falló",
  cause: "Causa:",
  action: "Qué hacer:",
  dismiss: "Descartar aviso",
  unknownCause: "El trabajo terminó con error sin indicar la causa.",
  unknownAction: "Vuelve a intentarlo; si se repite, avisa al administrador.",
} as const;

/** Cause and action of a failed job, generic when the server sent none. */
export function jobFailureOf(job: Pick<JobOut, "error">): JobError {
  return (
    job.error ?? { cause: JOB_FAILURE_TEXT.unknownCause, action: JOB_FAILURE_TEXT.unknownAction }
  );
}

export interface JobFailureProps {
  error: JobError;
  /** Heading of the alert; JOB_FAILURE_TEXT.title by default. */
  title?: string;
  /** Called by the dismiss button; without it there is no button. */
  onDismiss?: () => void;
  /** Extra content after the action, such as a link to where the user can fix it. */
  children?: ReactNode;
}

export function JobFailure({
  error,
  title = JOB_FAILURE_TEXT.title,
  onDismiss,
  children,
}: JobFailureProps) {
  return (
    <div
      role="alert"
      className="job-failure"
      style={{ border: "1px solid #B42318", background: "#FEF3F2", padding: 8, borderRadius: 4 }}
    >
      <p style={{ margin: 0, fontWeight: 600 }}>{title}</p>
      <p style={{ margin: "4px 0 0" }}>
        <strong>{JOB_FAILURE_TEXT.cause}</strong> {error.cause}
      </p>
      <p style={{ margin: "4px 0 0" }}>
        <strong>{JOB_FAILURE_TEXT.action}</strong> {error.action}
      </p>
      {children}
      {onDismiss !== undefined && (
        <button type="button" style={{ marginTop: 6 }} onClick={onDismiss}>
          {JOB_FAILURE_TEXT.dismiss}
        </button>
      )}
    </div>
  );
}
