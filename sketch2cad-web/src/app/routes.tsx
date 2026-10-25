import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router";
import { CaseListPage } from "../features/cases/CaseListPage";
import {
  PlanCalibrationPage,
  PlanEditorPage,
  SourceCalibrationPage,
} from "../features/calibration/CalibrationPage";
import { LoginPage } from "../features/auth/LoginPage";
import { UploadPage } from "../features/sources/UploadPage";
import { useAuth } from "../features/auth/authStore";

export const LOGIN_PATH = "/login";
export const HOME_PATH = "/";
export const UPLOAD_PATH = "/cases/:caseId/upload";
export const SOURCE_CALIBRATION_PATH = "/cases/:caseId/sources/:sourceId/calibration";
export const PLAN_CALIBRATION_PATH = "/cases/:caseId/plans/:planId/calibration";
export const PLAN_EDITOR_PATH = "/cases/:caseId/plans/:planId/editor";

const PROTECTED_ROUTES = [
  { path: SOURCE_CALIBRATION_PATH, page: <SourceCalibrationPage /> },
  { path: PLAN_CALIBRATION_PATH, page: <PlanCalibrationPage /> },
  { path: PLAN_EDITOR_PATH, page: <PlanEditorPage /> },
];

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
      <Route
        path={UPLOAD_PATH}
        element={
          <RequireAuth>
            <UploadPage />
          </RequireAuth>
        }
      />
      {PROTECTED_ROUTES.map(({ path, page }) => (
        <Route key={path} path={path} element={<RequireAuth>{page}</RequireAuth>} />
      ))}
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
