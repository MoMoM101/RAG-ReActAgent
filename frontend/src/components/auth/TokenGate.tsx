import { useEffect, useState } from "react";
import { useAuthStore } from "../../stores/authStore";

export function TokenGate({ children }: { children: React.ReactNode }) {
  const {
    authenticated,
    loading,
    login,
    changePasswordAtLogin,
    checkAuth,
  } = useAuthStore();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    void checkAuth();
  }, [checkAuth]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password) return;

    setSubmitting(true);
    setError("");
    try {
      await login(username.trim(), password);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败，请重试");
    } finally {
      setSubmitting(false);
    }
  };

  const handlePasswordChange = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password || !newPassword || !confirmation) return;
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致");
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      await changePasswordAtLogin(username.trim(), password, newPassword);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "密码修改失败，请重试");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="auth-gate-loading">
        <div className="auth-gate-card">
          <h2>RAG Agent</h2>
          <p>正在验证登录状态...</p>
        </div>
      </div>
    );
  }

  if (!authenticated) {
    return (
      <div className="auth-gate-loading">
        <form
          className="auth-gate-card"
          onSubmit={changingPassword ? handlePasswordChange : handleSubmit}
        >
          <h2>RAG Agent</h2>
          <p>
            {changingPassword
              ? "验证当前密码后设置新密码"
              : "使用管理员或授权用户账号登录"}
          </p>
          <input
            type="text"
            autoComplete="username"
            placeholder="用户名"
            value={username}
            onChange={(event) => {
              setUsername(event.target.value);
              setError("");
            }}
            disabled={submitting}
          />
          <input
            type="password"
            autoComplete="current-password"
            placeholder={changingPassword ? "当前密码" : "密码"}
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              setError("");
            }}
            disabled={submitting}
          />
          {changingPassword && (
            <>
              <input
                type="password"
                autoComplete="new-password"
                placeholder="新密码"
                value={newPassword}
                onChange={(event) => {
                  setNewPassword(event.target.value);
                  setError("");
                }}
                disabled={submitting}
              />
              <input
                type="password"
                autoComplete="new-password"
                placeholder="再次输入新密码"
                value={confirmation}
                onChange={(event) => {
                  setConfirmation(event.target.value);
                  setError("");
                }}
                disabled={submitting}
              />
            </>
          )}
          <div className="auth-gate-actions">
            {changingPassword ? (
              <>
                <button
                  type="button"
                  className="auth-gate-secondary"
                  onClick={() => {
                    setChangingPassword(false);
                    setNewPassword("");
                    setConfirmation("");
                    setError("");
                  }}
                  disabled={submitting}
                >
                  返回登录
                </button>
                <button
                  type="submit"
                  disabled={
                    submitting
                    || !username.trim()
                    || !password
                    || !newPassword
                    || !confirmation
                  }
                >
                  {submitting ? "修改中..." : "确认修改"}
                </button>
              </>
            ) : (
              <>
                <button
                  type="submit"
                  disabled={submitting || !username.trim() || !password}
                >
                  {submitting ? "登录中..." : "登录"}
                </button>
                <button
                  type="button"
                  className="auth-gate-secondary"
                  onClick={() => {
                    setChangingPassword(true);
                    setError("");
                  }}
                  disabled={submitting}
                >
                  修改密码
                </button>
              </>
            )}
          </div>
          {error && <p className="auth-gate-error">{error}</p>}
          <p className="auth-gate-hint">
            首次启动账号由 BOOTSTRAP_ADMIN_USERNAME 和
            BOOTSTRAP_ADMIN_PASSWORD 创建
          </p>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
