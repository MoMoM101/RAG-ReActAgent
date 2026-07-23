import { useState } from "react";
import { useAuthStore } from "../../stores/authStore";

export function PasswordChangePanel() {
  const changePassword = useAuthStore((state) => state.changePassword);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setMessage("");
    setError("");
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致");
      return;
    }
    setSubmitting(true);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      setMessage("密码已更新，其他 Refresh Token 已失效");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "密码修改失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="settings-section" onSubmit={submit}>
      <div className="settings-section-title">账户安全</div>
      <p className="settings-hint">修改当前登录账号的密码，不限制密码长度或字符类型。</p>
      <div className="settings-field">
        <label>当前密码</label>
        <input
          type="password"
          autoComplete="current-password"
          placeholder="当前密码"
          value={currentPassword}
          onChange={(event) => setCurrentPassword(event.target.value)}
          disabled={submitting}
        />
      </div>
      <div className="settings-field">
        <label>新密码</label>
        <input
          type="password"
          autoComplete="new-password"
          placeholder="新密码"
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
          disabled={submitting}
        />
      </div>
      <div className="settings-field">
        <label>确认新密码</label>
        <input
          type="password"
          autoComplete="new-password"
          placeholder="再次输入新密码"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          disabled={submitting}
        />
      </div>
      <button
        type="submit"
        className="save-btn"
        disabled={submitting || !currentPassword || !newPassword || !confirmation}
      >
        {submitting ? "修改中..." : "修改密码"}
      </button>
      {error && <p className="auth-gate-error">{error}</p>}
      {message && <p style={{ color: "var(--success)", fontSize: 13 }}>{message}</p>}
    </form>
  );
}
