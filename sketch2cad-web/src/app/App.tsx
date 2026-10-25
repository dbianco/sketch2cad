import { Route, Routes } from "react-router";

function HomePage() {
  return <p>Aplicación en construcción.</p>;
}

function NotFoundPage() {
  return <p>Página no encontrada.</p>;
}

export function App() {
  return (
    <>
      <header>
        <h1>Sketch2CAD</h1>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </>
  );
}
