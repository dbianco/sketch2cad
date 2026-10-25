import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router";
import { CaseListPage } from "../features/cases/CaseListPage";
import { LoginPage } from "../features/auth/LoginPage";
import { useAuth } from "../features/auth/authStore";

export const LOGIN_PATH = "/login";
export const HOME_PATH = "/";

function NotFoundPage() {
  return (
    <>
      <h1>Sketch2CAD</h1>
      <p>Página no encontrada.</p>
    </>
  );
}

/** Render `children` only for an authenticated user; anyone else goes to the login page. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status !== "authenticated") {
    return <Navigate to={LOGIN_PATH} replace />;
  }
  return children;
}

/** Keep an already authenticated user away from the login page. */
function PublicOnly({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "authenticated") {
    return <Navigate to={HOME_PATH} replace />;
  }
  return children;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route
        path={LOGIN_PATH}
        element={
          <PublicOnly>
            <LoginPage />
          </PublicOnly>
        }
      />
      <Route
        path={HOME_PATH}
        element={
          <RequireAuth>
            <CaseListPage />
          </RequireAuth>
        }
      />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
