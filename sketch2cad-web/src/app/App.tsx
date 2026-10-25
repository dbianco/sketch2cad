import { useEffect, useState } from "react";
import { authStore, useAuth } from "../features/auth/authStore";
import { AppRoutes } from "./routes";

function LogoutButton() {
  const [pending, setPending] = useState(false);

  async function handleClick() {
    setPending(true);
    // The store resets to anonymous even if the request fails; the route guard then
    // sends the user to the login page.
    await authStore.logout();
    setPending(false);
  }

  return (
    <button type="button" onClick={() => void handleClick()} disabled={pending}>
      Cerrar sesión
    </button>
  );
}

export function App() {
  const { status, user } = useAuth();

  useEffect(() => {
    if (authStore.getState().status === "unknown") {
      void authStore.loadSession();
    }
  }, []);

  if (status === "unknown" || status === "loading") {
    return (
      <p role="status" aria-live="polite">
        Cargando…
      </p>
    );
  }

  return (
    <>
      {status === "authenticated" && user !== null && (
        <header
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
            height: 56,
            padding: "0 32px",
            background: "#14202B",
            color: "#fff",
          }}
        >
          <span style={{ fontWeight: 700, marginRight: "auto" }}>Sketch2CAD</span>
          <span>{user.display_name ?? user.username}</span>
          <LogoutButton />
        </header>
      )}
      <main>
        <AppRoutes />
      </main>
    </>
  );
}
