import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { MainLayout } from "./components/layout/MainLayout";
import { ChatPanel } from "./components/chat/ChatPanel";
import { ToastContainer } from "./components/shared/Toast";
import { ConfirmProvider } from "./components/shared/ConfirmDialog";
import { ErrorBoundary } from "./components/shared/ErrorBoundary";
import { LoginPage } from "./components/auth/LoginPage";
import { useAuthStore } from "./stores/authStore";

const DocumentList = lazy(() =>
  import("./components/documents/DocumentList").then((module) => ({
    default: module.DocumentList,
  })),
);
const SettingsPage = lazy(() =>
  import("./components/settings/SettingsPage").then((module) => ({
    default: module.SettingsPage,
  })),
);
const MemoryList = lazy(() =>
  import("./components/memories/MemoryList").then((module) => ({
    default: module.MemoryList,
  })),
);

function RouteFallback() {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{ padding: 24, color: "var(--muted)" }}
    >
      页面加载中…
    </div>
  );
}

export default function App() {
  const checkAuth = useAuthStore((s) => s.checkAuth);
  const loading = useAuthStore((s) => s.loading);
  const authenticated = useAuthStore((s) => s.authenticated);
  const clearToken = useAuthStore((s) => s.clearToken);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  useEffect(() => {
    const saved = localStorage.getItem("rag_agent_theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    } else {
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      document.documentElement.setAttribute("data-theme", prefersDark ? "dark" : "light");
    }
  }, []);

  useEffect(() => {
    const handler = () => clearToken();
    window.addEventListener("auth:required", handler);
    return () => window.removeEventListener("auth:required", handler);
  }, [clearToken]);

  if (loading) {
    return (
      <div className="auth-gate-loading">
        <div className="auth-gate-card">
          <p>加载中…</p>
        </div>
      </div>
    );
  }

  if (!authenticated) {
    return <LoginPage />;
  }

  return (
    <ConfirmProvider>
      <BrowserRouter>
        <ErrorBoundary>
          <Suspense fallback={<RouteFallback />}>
            <Routes>
              <Route element={<MainLayout />}>
                <Route path="/" element={<ChatPanel />} />
                <Route path="/documents" element={<DocumentList />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/memories" element={<MemoryList />} />
              </Route>
            </Routes>
          </Suspense>
        </ErrorBoundary>
      </BrowserRouter>
      <ToastContainer />
    </ConfirmProvider>
  );
}
