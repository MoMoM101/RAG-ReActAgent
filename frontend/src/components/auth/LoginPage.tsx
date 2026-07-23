import { useState } from "react";
import { useAuthStore } from "../../stores/authStore";

export function LoginPage() {
  const login = useAuthStore((s) => s.login);
  const changePassword = useAuthStore((s) => s.changePassword);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showChangePwd, setShowChangePwd] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (newPassword !== confirmPassword) {
      setError("两次输入的新密码不一致");
      return;
    }
    if (newPassword.length < 12) {
      setError("新密码至少需要 12 个字符");
      return;
    }
    setLoading(true);
    try {
      await login(username, password);
      await changePassword(password, newPassword);
      setShowChangePwd(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "修改密码失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-gate-loading">
      <div className="auth-gate-card">
        <h2>RAG Agent</h2>
        {showChangePwd ? (
          <>
            <p>首次登录需要更换密码</p>
            {error && <p className="auth-gate-error">{error}</p>}
            <form onSubmit={handleChangePassword}>
              <input
                placeholder="用户名"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoFocus
              />
              <input
                type="password"
                placeholder="当前密码"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <input
                type="password"
                placeholder="新密码（至少 12 位）"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                minLength={12}
              />
              <input
                type="password"
                placeholder="确认新密码"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
              />
              <button type="submit" disabled={loading}>
                {loading ? "处理中…" : "修改密码并登录"}
              </button>
              <button
                type="button"
                disabled={loading}
                style={{
                  background: "transparent",
                  color: "var(--fg)",
                  marginTop: 8,
                }}
                onClick={() => { setShowChangePwd(false); setError(""); }}
              >
                返回登录
              </button>
            </form>
          </>
        ) : (
          <>
            <p>知识库智能问答系统</p>
            {error && <p className="auth-gate-error">{error}</p>}
            <form onSubmit={handleLogin}>
              <input
                placeholder="用户名"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoFocus
              />
              <input
                type="password"
                placeholder="密码"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <button type="submit" disabled={loading}>
                {loading ? "登录中…" : "登录"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
