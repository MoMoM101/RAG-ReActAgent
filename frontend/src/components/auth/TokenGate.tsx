import { useEffect, useState } from "react";
import { useAuthStore } from "../../stores/authStore";

export function TokenGate({ children }: { children: React.ReactNode }) {
  const { token, authenticated, loading, setToken, clearToken, checkAuth } =
    useAuthStore();
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;

    setSubmitting(true);
    setError("");
    try {
      const res = await fetch("/api/health", {
        headers: { "X-Admin-Token": trimmed },
      });
      if (res.ok) {
        setToken(trimmed);
      } else {
        setError("令牌无效，请检查后重试");
        clearToken();
      }
    } catch {
      setError("无法连接后端服务，请确认服务已启动");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="auth-gate-loading">
        <div className="auth-gate-card">
          <h2>RAG Agent</h2>
          <p>正在连接...</p>
        </div>
      </div>
    );
  }

  if (!authenticated || !token) {
    return (
      <div className="auth-gate-loading">
        <form className="auth-gate-card" onSubmit={handleSubmit}>
          <h2>RAG Agent</h2>
          <p>请输入管理令牌以继续</p>
          <input
            type="password"
            autoComplete="off"
            placeholder="管理令牌"
            value={input}
            onChange={(e) => { setInput(e.target.value); setError(""); }}
            disabled={submitting}
          />
          <button type="submit" disabled={submitting || !input.trim()}>
            {submitting ? "验证中..." : "验证"}
          </button>
          {error && <p className="auth-gate-error">{error}</p>}
          <p className="auth-gate-hint">
            令牌由后端首次启动时生成，保存在项目 .env 文件中
          </p>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
