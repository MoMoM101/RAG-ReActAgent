import { useEffect } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { MainLayout } from "./components/layout/MainLayout";
import { ChatPanel } from "./components/chat/ChatPanel";
import { DocumentList } from "./components/documents/DocumentList";
import { SettingsPage } from "./components/settings/SettingsPage";
import { MemoryList } from "./components/memories/MemoryList";
import { ToastContainer } from "./components/shared/Toast";
import { ConfirmProvider } from "./components/shared/ConfirmDialog";
import { ErrorBoundary } from "./components/shared/ErrorBoundary";
import { TokenGate } from "./components/auth/TokenGate";
import { useAuthStore } from "./stores/authStore";

export default function App() {
  const clearToken = useAuthStore((s) => s.clearToken);

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

  return (
    <TokenGate>
      <ConfirmProvider>
        <BrowserRouter>
          <ErrorBoundary>
            <Routes>
              <Route element={<MainLayout />}>
                <Route path="/" element={<ChatPanel />} />
                <Route path="/documents" element={<DocumentList />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/memories" element={<MemoryList />} />
              </Route>
            </Routes>
          </ErrorBoundary>
        </BrowserRouter>
        <ToastContainer />
      </ConfirmProvider>
    </TokenGate>
  );
}
