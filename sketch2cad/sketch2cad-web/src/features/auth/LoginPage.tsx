import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { authStore, type LoginError } from "./authStore";

const INK = "#14202B";
const ACCENT = "#0B6E8A";
const BACKGROUND = "#F4F6F8";

const styles = {
  page: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: BACKGROUND,
    color: INK,
  },
  form: {
    width: "100%",
    maxWidth: 360,
    display: "flex",
    flexDirection: "column",
    gap: 12,
    padding: 32,
    background: "#fff",
    borderRadius: 8,
    boxShadow: "0 1px 3px rgba(20, 32, 43, 0.15)",
  },
  heading: { margin: "0 0 8px", fontSize: 24 },
  label: { fontWeight: 600, fontSize: 14 },
  input: { padding: "8px 10px", fontSize: 16, border: "1px solid #B8C2CC", borderRadius: 4 },
  button: {
    marginTop: 8,
    padding: "10px 12px",
    fontSize: 16,
    fontWeight: 600,
    color: "#fff",
    background: ACCENT,
    border: "none",
    borderRadius: 4,
    cursor: "pointer",
  },
  error: {
    margin: 0,
    padding: "8px 10px",
    color: "#8A1C1C",
    background: "#FDECEC",
    borderRadius: 4,
  },
} satisfies Record<string, CSSProperties>;

const timeFormat = new Intl.DateTimeFormat("es", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const dateFormat = new Intl.DateTimeFormat("es", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

/** Spanish message for a failed login; the unlock time is shown in the user's time zone. */
export function loginErrorMessage(error: LoginError): string {
  switch (error.kind) {
    case "invalid_credentials":
      return "Usuario o contraseña incorrectos.";
    case "locked": {
      const until = error.lockedUntil === undefined ? null : new Date(error.lockedUntil);
      if (until === null || Number.isNaN(until.getTime())) {
        return "Cuenta bloqueada temporalmente. Inténtalo de nuevo más tarde.";
      }
      return `Cuenta bloqueada temporalmente. Podrás volver a intentarlo a las ${timeFormat.format(until)} del ${dateFormat.format(until)}.`;
    }
    case "unknown":
      return "No se pudo iniciar sesión. Inténtalo de nuevo.";
  }
}

export function LoginPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const usernameRef = useRef<HTMLInputElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    usernameRef.current?.focus();
  }, []);

  useEffect(() => {
    if (error !== null) {
      errorRef.current?.focus();
    }
  }, [error]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    setSubmitting(true);
    setError(null);
    const result = await authStore.login(username, password);
    if (result.ok) {
      void navigate("/", { replace: true });
      return;
    }
    setSubmitting(false);
    setPassword("");
    setError(loginErrorMessage(result.error));
  }

  return (
    <div style={styles.page}>
      <form style={styles.form} onSubmit={(event) => void handleSubmit(event)} noValidate>
        <h1 style={styles.heading}>Iniciar sesión</h1>
        {error !== null && (
          <p ref={errorRef} role="alert" tabIndex={-1} style={styles.error}>
            {error}
          </p>
        )}
        <label htmlFor="login-username" style={styles.label}>
          Usuario
        </label>
        <input
          ref={usernameRef}
          id="login-username"
          name="username"
          type="text"
          autoComplete="username"
          required
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          style={styles.input}
        />
        <label htmlFor="login-password" style={styles.label}>
          Contraseña
        </label>
        <input
          id="login-password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          style={styles.input}
        />
        <button type="submit" disabled={submitting} style={styles.button}>
          Entrar
        </button>
      </form>
    </div>
  );
}
