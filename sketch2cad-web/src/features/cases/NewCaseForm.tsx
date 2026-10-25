import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { ApiError, api } from "../../api/client";
import type { components } from "../../api/schema";

export type CaseFile = components["schemas"]["CaseFileOut"];
export type IncidentType = components["schemas"]["IncidentType"];

/** Spanish labels of the incident types, in display order. */
export const INCIDENT_TYPE_LABELS: Record<IncidentType, string> = {
  traffic_collision: "Siniestro vial",
  crime_scene: "Escena del crimen",
  inspection: "Inspección ocular",
  other: "Otro",
};

export const INCIDENT_TYPES = Object.keys(INCIDENT_TYPE_LABELS) as IncidentType[];

type Field = "incident_type" | "incident_date" | "description" | "declared_location" | "organization";

type FieldErrors = Partial<Record<Field, string>>;

const FIELDS: readonly Field[] = [
  "incident_type",
  "incident_date",
  "description",
  "declared_location",
  "organization",
];

const REQUIRED_MESSAGES: Partial<Record<Field, string>> = {
  incident_type: "Selecciona el tipo de hecho",
  incident_date: "Ingresa la fecha del hecho",
  description: "Ingresa una descripción",
  declared_location: "Ingresa la ubicación declarada",
};

const INVALID_MESSAGES: Record<Field, string> = {
  incident_type: "Selecciona un tipo de hecho válido",
  incident_date: "Ingresa una fecha válida",
  description: "Revisa la descripción",
  declared_location: "Revisa la ubicación declarada",
  organization: "Revisa la organización",
};

const GENERIC_ERROR = "No se pudo crear el expediente. Inténtalo de nuevo.";

const styles = {
  form: {
    display: "flex",
    flexDirection: "column",
    gap: 12,
    padding: 24,
    background: "#fff",
    border: "1px solid #D5DCE3",
    borderRadius: 8,
    maxWidth: 560,
  },
  heading: { margin: 0, fontSize: 20 },
  field: { display: "flex", flexDirection: "column", gap: 4 },
  label: { fontWeight: 600, fontSize: 14 },
  input: { padding: "8px 10px", fontSize: 15, border: "1px solid #9AA8B5", borderRadius: 6 },
  fieldError: { margin: 0, fontSize: 13, color: "#8A1C1C" },
  alert: { margin: 0, padding: "8px 10px", color: "#8A1C1C", background: "#FDECEC", borderRadius: 4 },
  actions: { display: "flex", gap: 12 },
  primary: {
    height: 40,
    padding: "0 20px",
    background: "#0B6E8A",
    color: "#fff",
    border: 0,
    borderRadius: 6,
    fontSize: 15,
    fontWeight: 600,
    cursor: "pointer",
  },
  secondary: {
    height: 40,
    padding: "0 20px",
    background: "#fff",
    color: "#14202B",
    border: "1px solid #9AA8B5",
    borderRadius: 6,
    fontSize: 15,
    cursor: "pointer",
  },
} satisfies Record<string, CSSProperties>;

/** Today's date in the user's local time zone as `YYYY-MM-DD`. */
export function localToday(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${String(now.getFullYear())}-${month}-${day}`;
}

interface Values {
  incident_type: IncidentType | "";
  incident_date: string;
  description: string;
  declared_location: string;
  organization: string;
}

function validate(values: Values): FieldErrors {
  const errors: FieldErrors = {};
  if (values.incident_type === "") {
    errors.incident_type = REQUIRED_MESSAGES.incident_type;
  }
  if (values.incident_date === "") {
    errors.incident_date = REQUIRED_MESSAGES.incident_date;
  }
  if (values.description.trim() === "") {
    errors.description = REQUIRED_MESSAGES.description;
  }
  if (values.declared_location.trim() === "") {
    errors.declared_location = REQUIRED_MESSAGES.declared_location;
  }
  return errors;
}

/** Map the `errors` of a 422 problem to form fields; unknown locations are ignored. */
function serverFieldErrors(error: ApiError): FieldErrors {
  const errors: FieldErrors = {};
  for (const item of error.errors ?? []) {
    const field = item.loc[item.loc.length - 1];
    if (typeof field !== "string" || !FIELDS.includes(field as Field)) {
      continue;
    }
    const key = field as Field;
    const isMissing = item.type === "missing" || item.type === "string_too_short";
    errors[key] ??= (isMissing ? REQUIRED_MESSAGES[key] : undefined) ?? INVALID_MESSAGES[key];
  }
  return errors;
}

export interface NewCaseFormProps {
  onCreated(caseFile: CaseFile): void;
  onCancel(): void;
}

export function NewCaseForm({ onCreated, onCancel }: NewCaseFormProps) {
  const [values, setValues] = useState<Values>(() => ({
    incident_type: "traffic_collision",
    incident_date: localToday(),
    description: "",
    declared_location: "",
    organization: "",
  }));
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const alertRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    formRef.current?.querySelector<HTMLElement>("select, input, textarea")?.focus();
  }, []);

  useEffect(() => {
    if (formError !== null) {
      alertRef.current?.focus();
    }
  }, [formError]);

  function focusFirstInvalid(errors: FieldErrors): void {
    const first = FIELDS.find((field) => errors[field] !== undefined);
    if (first !== undefined) {
      document.getElementById(`new-case-${first}`)?.focus();
    }
  }

  function update<K extends keyof Values>(key: K, value: Values[K]): void {
    setValues((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    setFormError(null);
    const errors = validate(values);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0 || values.incident_type === "") {
      focusFirstInvalid(errors);
      return;
    }
    setSubmitting(true);
    const organization = values.organization.trim();
    try {
      const created = await api.post("/api/v1/case-files", {
        body: {
          incident_type: values.incident_type,
          incident_date: values.incident_date,
          description: values.description.trim(),
          declared_location: values.declared_location.trim(),
          organization: organization === "" ? undefined : organization,
        },
      });
      onCreated(created);
    } catch (error) {
      setSubmitting(false);
      const mapped = error instanceof ApiError && error.status === 422 ? serverFieldErrors(error) : {};
      if (Object.keys(mapped).length > 0) {
        setFieldErrors(mapped);
        focusFirstInvalid(mapped);
      } else {
        setFormError(GENERIC_ERROR);
      }
    }
  }

  function fieldProps(field: Field) {
    const hasError = fieldErrors[field] !== undefined;
    return {
      id: `new-case-${field}`,
      name: field,
      "aria-invalid": hasError ? true : undefined,
      "aria-describedby": hasError ? `new-case-${field}-error` : undefined,
      style: styles.input,
    };
  }

  function errorFor(field: Field) {
    const message = fieldErrors[field];
    return message === undefined ? null : (
      <p id={`new-case-${field}-error`} style={styles.fieldError}>
        {message}
      </p>
    );
  }

  return (
    <form
      ref={formRef}
      aria-labelledby="new-case-heading"
      style={styles.form}
      onSubmit={(event) => void handleSubmit(event)}
      noValidate
    >
      <h2 id="new-case-heading" style={styles.heading}>
        Nuevo expediente
      </h2>
      {formError !== null && (
        <p ref={alertRef} role="alert" tabIndex={-1} style={styles.alert}>
          {formError}
        </p>
      )}
      <div style={styles.field}>
        <label htmlFor="new-case-incident_type" style={styles.label}>
          Tipo de hecho
        </label>
        <select
          {...fieldProps("incident_type")}
          value={values.incident_type}
          onChange={(event) => update("incident_type", event.target.value as IncidentType)}
        >
          {INCIDENT_TYPES.map((type) => (
            <option key={type} value={type}>
              {INCIDENT_TYPE_LABELS[type]}
            </option>
          ))}
        </select>
        {errorFor("incident_type")}
      </div>
      <div style={styles.field}>
        <label htmlFor="new-case-incident_date" style={styles.label}>
          Fecha del hecho
        </label>
        <input
          {...fieldProps("incident_date")}
          type="date"
          value={values.incident_date}
          onChange={(event) => update("incident_date", event.target.value)}
        />
        {errorFor("incident_date")}
      </div>
      <div style={styles.field}>
        <label htmlFor="new-case-description" style={styles.label}>
          Descripción
        </label>
        <textarea
          {...fieldProps("description")}
          rows={3}
          value={values.description}
          onChange={(event) => update("description", event.target.value)}
        />
        {errorFor("description")}
      </div>
      <div style={styles.field}>
        <label htmlFor="new-case-declared_location" style={styles.label}>
          Ubicación declarada
        </label>
        <input
          {...fieldProps("declared_location")}
          type="text"
          value={values.declared_location}
          onChange={(event) => update("declared_location", event.target.value)}
        />
        {errorFor("declared_location")}
      </div>
      <div style={styles.field}>
        <label htmlFor="new-case-organization" style={styles.label}>
          Organización (opcional)
        </label>
        <input
          {...fieldProps("organization")}
          type="text"
          value={values.organization}
          onChange={(event) => update("organization", event.target.value)}
        />
        {errorFor("organization")}
      </div>
      <div style={styles.actions}>
        <button type="submit" disabled={submitting} style={styles.primary}>
          Crear expediente
        </button>
        <button type="button" onClick={onCancel} style={styles.secondary}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
