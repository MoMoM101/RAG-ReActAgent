import { useState, useEffect, useCallback, useRef } from "react";
import { getSettings, updateSettings, checkDimension, rebuildCollections, clearAllData, testConnection, subscribeRebuildProgress, getRebuildStatus, type SettingsResponse, type TestConnectionResult, type DimensionCheckResult, type RebuildProgressEvent } from "../../api/settings";
import { useToastStore } from "../../stores/toastStore";
import { CheckIcon } from "../shared/Icons";
import { Skeleton } from "../shared/Skeleton";

/* ── Provider presets ────────────────────────────── */

const LLM_PRESETS: Record<string, { label: string; url: string }> = {
  openai:    { label: "OpenAI",             url: "https://api.openai.com/v1" },
  deepseek:  { label: "DeepSeek",           url: "https://api.deepseek.com/v1" },
  zhipu:     { label: "智谱 GLM",           url: "https://open.bigmodel.cn/api/paas/v4" },
  moonshot:  { label: "Moonshot 月之暗面",   url: "https://api.moonshot.cn/v1" },
  qwen:      { label: "通义千问",            url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  groq:      { label: "Groq",               url: "https://api.groq.com/openai/v1" },
  ollama:    { label: "Ollama 本地",         url: "http://localhost:11434/v1" },
};

const EMB_PRESETS: Record<string, { label: string; url: string }> = {
  openai:   { label: "OpenAI",              url: "https://api.openai.com/v1" },
  deepseek: { label: "DeepSeek",            url: "https://api.deepseek.com/v1" },
  zhipu:    { label: "智谱 GLM",            url: "https://open.bigmodel.cn/api/paas/v4" },
  qwen:     { label: "通义千问",             url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
};

interface ProviderProfile { model: string; api_key: string; base_url: string; }

/* ── localStorage helpers ────────────────────────── */

const LS_LLM = "rag_agent_llm_profiles";
const LS_EMB = "rag_agent_emb_profiles";
function loadProfiles(key: string): Record<string, ProviderProfile> {
  try { return JSON.parse(localStorage.getItem(key) || "{}"); } catch { return {}; }
}
function saveProfiles(key: string, profiles: Record<string, ProviderProfile>) {
  localStorage.setItem(key, JSON.stringify(profiles));
}

function isBuiltin(llm: string) { return llm in LLM_PRESETS; }
function isEmbBuiltin(emb: string) { return emb in EMB_PRESETS; }

/* ── Component ──────────────────────────────────── */

export function SettingsPage() {
  const [config, setConfig] = useState<SettingsResponse | null>(null);
  const [saved, setSaved] = useState(false);
  const [dimMismatch, setDimMismatch] = useState<DimensionCheckResult | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildMessage, setRebuildMessage] = useState("");
  const rebuildCleanupRef = useRef<(() => void) | null>(null);
  const terminalReceivedRef = useRef(false);
  const rebuildingRef = useRef(false);  // sync guard against double-click
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Cleanup EventSource and polling on unmount
  useEffect(() => {
    return () => {
      rebuildCleanupRef.current?.();
      if (pollIntervalRef.current !== null) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, []);
  const [theme, setTheme] = useState<"system" | "dark" | "light">(() => {
    const t = localStorage.getItem("rag_agent_theme");
    return t === "dark" || t === "light" ? t : "system";
  });
  const [loading, setLoading] = useState(true);
  const [llmCustom, setLlmCustom] = useState("");
  const [embCustom, setEmbCustom] = useState("");
  const addToast = useToastStore((s) => s.addToast);

  useEffect(() => {
    getSettings()
      .then((c) => {
        setConfig(c);
        if (!isBuiltin(c.llm.provider) && c.llm.provider !== "") setLlmCustom(c.llm.provider);
        if (!isEmbBuiltin(c.embedding.provider) && c.embedding.provider !== "") setEmbCustom(c.embedding.provider);
      })
      .catch(() => addToast({ type: "error", message: "加载配置失败" }))
      .finally(() => setLoading(false));

    // 自动检查维度 + 最近重建状态
    checkDimension().then(setDimMismatch).catch(() => {});
    getRebuildStatus().then((r) => {
      if (r.status === "failed") {
        setRebuildMessage(`上次重建失败: ${r.error || "未知错误"}`);
      }
    }).catch(() => {});
  }, []); // eslint-disable-line

  /* ── Save ──────────────────────────────────────── */
  const handleSave = async () => {
    if (!config) return;
    try {
      const payload = { ...config };
      const llmProv = config.llm.provider === "custom" ? llmCustom : config.llm.provider;
      const embProv = config.embedding.provider === "custom" ? embCustom : config.embedding.provider;
      payload.llm.provider = llmProv;
      payload.embedding.provider = embProv;

      // Persist provider profile to localStorage
      const llmProfiles = loadProfiles(LS_LLM);
      llmProfiles[llmProv] = { model: payload.llm.model, api_key: payload.llm.api_key, base_url: payload.llm.base_url };
      saveProfiles(LS_LLM, llmProfiles);

      const embProfiles = loadProfiles(LS_EMB);
      embProfiles[embProv] = { model: payload.embedding.model, api_key: payload.embedding.api_key, base_url: payload.embedding.base_url };
      saveProfiles(LS_EMB, embProfiles);

      const saveResult = await updateSettings(payload);
      setSaved(true);
      addToast({ type: "success", message: "设置已保存" });
      setTimeout(() => setSaved(false), 2500);

      // 优先使用 save 响应中的维度信息
      if (saveResult.dimension) {
        if (!saveResult.dimension.ok) {
          // API 连接失败：提示用户但无法判断维度是否匹配
          addToast({
            type: "warning",
            message: `Embedding 连接失败，无法校验维度: ${saveResult.dimension.error?.slice(0, 80)}。如果切换了模型，请手动确认并重建索引。`,
          });
        } else if (saveResult.dimension.mismatch) {
          setDimMismatch(saveResult.dimension);
        }
      } else {
        // 降级：独立调用 checkDimension
        try {
          const dimResult = await checkDimension();
          if (dimResult.ok && dimResult.mismatch) {
            setDimMismatch(dimResult);
          } else if (!dimResult.ok) {
            addToast({
              type: "warning",
              message: `Embedding 连接失败: ${dimResult.error?.slice(0, 80)}`,
            });
          }
        } catch {
          addToast({ type: "warning", message: "维度校验请求失败，如切换了模型请手动重建索引" });
        }
      }
    } catch {
      addToast({ type: "error", message: "保存失败，请检查后端是否运行" });
    }
  };

  const handleThemeChange = (val: "system" | "dark" | "light") => {
    setTheme(val);
    if (val === "system") {
      localStorage.removeItem("rag_agent_theme");
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      document.documentElement.setAttribute("data-theme", prefersDark ? "dark" : "light");
    } else {
      localStorage.setItem("rag_agent_theme", val);
      document.documentElement.setAttribute("data-theme", val);
    }
  };

  /* ── Provider switch: clear or restore profile ─── */
  const switchLLM = useCallback((val: string) => {
    if (!config) return;
    if (val === "custom") {
      setConfig({ ...config, llm: { provider: "custom", model: "", api_key: "", base_url: "" } });
      setLlmCustom("");
      return;
    }
    const preset = LLM_PRESETS[val];
    const profiles = loadProfiles(LS_LLM);
    const saved = profiles[val];
    setConfig({
      ...config,
      llm: {
        provider: val,
        model: saved?.model || "",
        api_key: saved?.api_key || "",
        base_url: saved?.base_url || preset.url,
      },
    });
    setLlmCustom("");
  }, [config]);

  const switchEmb = useCallback((val: string) => {
    if (!config) return;
    if (val === "custom") {
      setConfig({ ...config, embedding: { provider: "custom", model: "", api_key: "", base_url: "" } });
      setEmbCustom("");
      return;
    }
    const preset = EMB_PRESETS[val];
    const profiles = loadProfiles(LS_EMB);
    const saved = profiles[val];
    setConfig({
      ...config,
      embedding: {
        provider: val,
        model: saved?.model || "",
        api_key: saved?.api_key || "",
        base_url: saved?.base_url || preset.url,
      },
    });
    setEmbCustom("");
  }, [config]);

  const updateLLM = (k: string, v: string) =>
    config && setConfig({ ...config, llm: { ...config.llm, [k]: v } });
  const updateEmb = (k: string, v: string) =>
    config && setConfig({ ...config, embedding: { ...config.embedding, [k]: v } });
  /* ── Test button helper ─────────────────────────── */
  const [testLLM, setTestLLM] = useState<TestConnectionResult | null>(null);
  const [testEmb, setTestEmb] = useState<TestConnectionResult | null>(null);
  const [testingLLM, setTestingLLM] = useState(false);
  const [testingEmb, setTestingEmb] = useState(false);

  const doTest = async (
    section: { provider: string; model: string; api_key: string; base_url: string },
    kind: "llm" | "embedding",
    setResult: (r: TestConnectionResult) => void,
    setTesting: (v: boolean) => void,
  ) => {
    setTesting(true);
    setResult(null as any);
    try {
      const r = await testConnection({ ...section, kind });
      setResult(r);
    } catch {
      setResult({ ok: false, latency_ms: 0, detail: "请求失败" });
    }
    setTesting(false);
  };

  /* ── Dimension rebuild / clear handlers ─────────── */
  const finishRebuilding = (message?: string) => {
    rebuildingRef.current = false;
    setRebuilding(false);
    setRebuildMessage(message || "");
    if (pollIntervalRef.current !== null) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  };

  const handleRebuildResult = (r: RebuildProgressEvent) => {
    if (r.status === "completed") {
      finishRebuilding();
      if (r.failed_count && r.failed_count > 0) {
        addToast({
          type: "info",
          message: `重建完成，${r.chunk_count || 0} 个切片，${r.failed_count} 份文档失败 (chunk_size=${r.actual_chunk_size})`,
        });
      } else {
        addToast({
          type: "success",
          message: `重建完成，${r.chunk_count || 0} 个切片 (chunk_size=${r.actual_chunk_size})`,
        });
      }
      checkDimension().then((dim) => {
        if (dim.mismatch) {
          addToast({ type: "error", message: "校验失败: 向量维度仍不一致，请重新重建" });
        }
        setDimMismatch(dim);
      }).catch(() => {});
    } else if (r.status === "failed") {
      finishRebuilding();
      addToast({ type: "error", message: `重建失败: ${r.error || "未知错误"}` });
    }
  };

  const startPolling = () => {
    if (pollIntervalRef.current !== null) return;
    pollIntervalRef.current = setInterval(() => {
      getRebuildStatus().then((r) => {
        if (r.status === "completed" || r.status === "failed") {
          handleRebuildResult(r);
        } else if (!terminalReceivedRef.current) {
          setRebuildMessage("重建进行中...");
        }
      }).catch(() => {
        // retry on next interval
      });
    }, 2000);
  };

  const handleRebuild = async () => {
    if (rebuildingRef.current) return;  // prevent double-click
    rebuildingRef.current = true;
    setRebuilding(true);
    setRebuildMessage("正在启动...");
    terminalReceivedRef.current = false;
    try {
      const result = await rebuildCollections();
      if (result.status === "rejected") {
        finishRebuilding();
        addToast({ type: "error", message: result.reason || "重建已被拒绝" });
        return;
      }
      addToast({ type: "info", message: "重建已启动，请等待完成..." });

      const cleanup = subscribeRebuildProgress(
        (event: RebuildProgressEvent) => {
          switch (event.status) {
            case "preflight":
              setRebuildMessage(event.message || "正在检测模型兼容性...");
              break;
            case "rebuilding":
              setRebuildMessage(
                event.filename
                  ? `(${event.current}/${event.total}) ${event.filename}`
                  : event.message || "正在重建..."
              );
              break;
            case "switching":
              setRebuildMessage("正在切换索引...");
              break;
            case "completed":
            case "failed":
              terminalReceivedRef.current = true;
              handleRebuildResult(event);
              break;
            case "timeout":
              terminalReceivedRef.current = true;
              // SSE queue didn't deliver the terminal event.
              // Check once, then fall through to polling if still running.
              getRebuildStatus().then((r) => {
                if (r.status === "completed" || r.status === "failed") {
                  handleRebuildResult(r);
                } else {
                  startPolling();
                }
              }).catch(() => { startPolling(); });
              break;
          }
        },
        () => {
          if (!terminalReceivedRef.current) {
            // SSE connection lost without a terminal event — fall back
            // to polling until the rebuild finishes.
            startPolling();
          }
          rebuildCleanupRef.current = null;
        },
      );
      rebuildCleanupRef.current = cleanup;
    } catch {
      finishRebuilding();
      addToast({ type: "error", message: "重建启动失败" });
    }
  };

  const handleClear = async () => {
    setRebuilding(true);
    try {
      const result = await clearAllData();
      const d = result.deleted;
      addToast({ type: "success", message: `已清空 ${d.documents} 份文档、${d.memories} 条记忆、${d.conversations} 个对话` });
      setDimMismatch(null);
    } catch {
      addToast({ type: "error", message: "数据清除失败" });
    }
    setRebuilding(false);
  };

  /* ── Render ────────────────────────────────────── */
  if (loading || !config) {
    return (
      <div className="chat-main">
        <div className="chat-header"><span className="chat-header-title">系统设置</span></div>
        <div className="settings-content">
          {loading ? (
            <>
              <Skeleton height={40} width={120} />
              <div style={{ marginBottom: 24 }} />
              <Skeleton height={250} />
              <div style={{ marginBottom: 16 }} />
              <Skeleton height={220} />
            </>
          ) : (
            <p style={{ color: "var(--danger)" }}>无法加载配置，请检查后端服务</p>
          )}
        </div>
      </div>
    );
  }

  const llmSel = isBuiltin(config.llm.provider) ? config.llm.provider
    : config.llm.provider === "" ? Object.keys(LLM_PRESETS)[0] : "custom";
  const embSel = isEmbBuiltin(config.embedding.provider) ? config.embedding.provider
    : config.embedding.provider === "" ? Object.keys(EMB_PRESETS)[0] : "custom";

  return (
    <div className="chat-main">
      <div className="chat-header"><span className="chat-header-title">系统设置</span></div>

      <div className="settings-content">
        {/* ── LLM ── */}
        <div className="settings-section">
          <div className="settings-section-title">LLM 大语言模型</div>

          <div className="settings-field">
            <label>提供商</label>
            <select value={llmSel} onChange={(e) => switchLLM(e.target.value)}>
              {Object.entries(LLM_PRESETS).map(([k, v]) => (
                <option key={k} value={k}>{v.label}{loadProfiles(LS_LLM)[k] ? " ✓" : ""}</option>
              ))}
              <option value="custom">自定义...</option>
            </select>
          </div>

          {llmSel === "custom" && (
            <div className="settings-field">
              <label>提供商标识</label>
              <input value={llmCustom} onChange={(e) => setLlmCustom(e.target.value)}
                placeholder="例如 deepseek, qwen, my-gateway" />
              <span className="settings-hint">兼容 OpenAI 接口格式即可，保存后下次选择时自动回填</span>
            </div>
          )}

          <div className="settings-field">
            <label>模型</label>
            <input value={config.llm.model} onChange={(e) => updateLLM("model", e.target.value)}
              placeholder="例如 gpt-4o, deepseek-chat" />
          </div>
          <div className="settings-field">
            <label>API Key</label>
            <input type="password" value={config.llm.api_key} onChange={(e) => updateLLM("api_key", e.target.value)}
              placeholder="sk-..." />
          </div>
          <div className="settings-field">
            <label>Base URL</label>
            <input value={config.llm.base_url} onChange={(e) => updateLLM("base_url", e.target.value)}
              placeholder="https://api.openai.com/v1" />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 4 }}>
            <button className="save-btn" style={{ padding: "4px 14px", fontSize: 12 }}
              disabled={testingLLM}
              onClick={() => doTest(config.llm, "llm", setTestLLM, setTestingLLM)}>
              {testingLLM ? "测试中..." : "测试连接"}
            </button>
            {testLLM && (
              <span style={{ fontSize: 13, color: testLLM.ok ? "var(--success)" : "var(--danger)" }}>
                {testLLM.ok ? `✓ 连通 ${testLLM.latency_ms}ms` : `✗ ${testLLM.detail.slice(0, 60)}`}
              </span>
            )}
          </div>
        </div>

        {/* ── Embedding ── */}
        <div className="settings-section">
          <div className="settings-section-title">Embedding 嵌入模型</div>

          <div className="settings-field">
            <label>提供商</label>
            <select value={embSel} onChange={(e) => switchEmb(e.target.value)}>
              {Object.entries(EMB_PRESETS).map(([k, v]) => (
                <option key={k} value={k}>{v.label}{loadProfiles(LS_EMB)[k] ? " ✓" : ""}</option>
              ))}
              <option value="custom">自定义...</option>
            </select>
          </div>

          {embSel === "custom" && (
            <div className="settings-field">
              <label>提供商标识</label>
              <input value={embCustom} onChange={(e) => setEmbCustom(e.target.value)}
                placeholder="例如 jina, cohere, my-embedding" />
              <span className="settings-hint">兼容 OpenAI Embedding 接口格式即可</span>
            </div>
          )}

          <div className="settings-field">
            <label>模型</label>
            <input value={config.embedding.model} onChange={(e) => updateEmb("model", e.target.value)}
              placeholder="例如 text-embedding-3-small" />
          </div>
          <div className="settings-field">
            <label>API Key</label>
            <input type="password" value={config.embedding.api_key} onChange={(e) => updateEmb("api_key", e.target.value)}
              placeholder="sk-..." />
          </div>
          <div className="settings-field">
            <label>Base URL</label>
            <input value={config.embedding.base_url} onChange={(e) => updateEmb("base_url", e.target.value)}
              placeholder="https://api.openai.com/v1" />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 4 }}>
            <button className="save-btn" style={{ padding: "4px 14px", fontSize: 12 }}
              disabled={testingEmb}
              onClick={() => doTest(config.embedding, "embedding", setTestEmb, setTestingEmb)}>
              {testingEmb ? "测试中..." : "测试连接"}
            </button>
            {testEmb && (
              <span style={{ fontSize: 13, color: testEmb.ok ? "var(--success)" : "var(--danger)" }}>
                {testEmb.ok ? `✓ 连通 ${testEmb.latency_ms}ms (dim=${testEmb.detail.split("=")[1] || "?"})` : `✗ ${testEmb.detail.slice(0, 60)}`}
              </span>
            )}
          </div>
        </div>

        {/* ── RAG 检索 ── */}
        <div className="settings-section">
          <div className="settings-section-title">RAG 检索参数</div>
          <div className="settings-field">
            <label>每次检索返回结果数</label>
            <span className="settings-hint">影响 LLM 看到的来源数量，越大 token 消耗越多</span>
            <input
              type="number" min={1} max={20}
              value={config.retrieval_top_k}
              onChange={(e) => setConfig({ ...config, retrieval_top_k: Number(e.target.value) })}
              style={{ marginTop: 6, width: 80 }}
            />
          </div>
          <div className="settings-field" style={{ marginTop: 12 }}>
            <label>联网搜索结果数</label>
            <span className="settings-hint">Bing/DDG 搜索返回的网页条数</span>
            <input
              type="number" min={1} max={10}
              value={config.web_search_max_results}
              onChange={(e) => setConfig({ ...config, web_search_max_results: Number(e.target.value) })}
              style={{ marginTop: 6, width: 80 }}
            />
          </div>
        </div>

        {/* ── 切块 ── */}
        <div className="settings-section">
          <div className="settings-section-title">文档切分参数</div>
          <div className="settings-field">
            <label>切片大小 (token)</label>
            <span className="settings-hint">每个文本块的 token 上限。主流 Embedding 模型上限 512~8192，过大可能导致 API 报错</span>
            <input
              type="number" min={128} max={4096}
              value={config.chunk_size}
              onChange={(e) => setConfig({ ...config, chunk_size: Number(e.target.value) })}
              style={{ marginTop: 6, width: 100 }}
            />
          </div>
          <div className="settings-field" style={{ marginTop: 12 }}>
            <label>切片重叠 (token)</label>
            <span className="settings-hint">相邻切片之间的重叠 token 数，防止关键信息在边界处被截断</span>
            <input
              type="number" min={0} max={2048}
              value={config.chunk_overlap}
              onChange={(e) => setConfig({ ...config, chunk_overlap: Number(e.target.value) })}
              style={{ marginTop: 6, width: 100 }}
            />
          </div>
        </div>

        {/* ── Feature Toggles ── */}
        <div className="settings-section">
          <div className="settings-section-title">功能开关</div>
          <div className="settings-field">
            <label>Reranker 精排</label>
            <span className="settings-hint">Cross-Encoder 对检索结果二次精排，提升准确性</span>
            <label className="toggle-switch" style={{ marginTop: 6 }}>
              <input
                type="checkbox"
                checked={config.rerank_enabled}
                onChange={(e) => setConfig({ ...config, rerank_enabled: e.target.checked })}
              />
              <span className="toggle-slider"></span>
              <span style={{ marginLeft: 8, fontSize: 13 }}>{config.rerank_enabled ? "已开启" : "已关闭"}</span>
            </label>
          </div>
          <div className="settings-field" style={{ marginTop: 12 }}>
            <label>Web Search 联网搜索</label>
            <span className="settings-hint">知识库信息不足时允许 Agent 搜索互联网</span>
            <label className="toggle-switch" style={{ marginTop: 6 }}>
              <input
                type="checkbox"
                checked={config.web_search_enabled}
                onChange={(e) => setConfig({ ...config, web_search_enabled: e.target.checked })}
              />
              <span className="toggle-slider"></span>
              <span style={{ marginLeft: 8, fontSize: 13 }}>{config.web_search_enabled ? "已开启" : "已关闭"}</span>
            </label>
          </div>
        </div>

        {/* ── Theme ── */}
        <div className="settings-section">
          <div className="settings-section-title">主题外观</div>
          <div className="settings-field">
            <label>主题模式</label>
            <select value={theme} onChange={(e) => handleThemeChange(e.target.value as "system" | "dark" | "light")}>
              <option value="system">跟随系统</option>
              <option value="dark">深色</option>
              <option value="light">浅色</option>
            </select>
          </div>
        </div>

        {/* ── Save ── */}
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button className="save-btn" onClick={handleSave}>保存设置</button>
          {saved && (
            <span style={{ fontSize: 13, color: "var(--success)", display: "inline-flex", alignItems: "center", gap: 4 }}>
              <CheckIcon size={13} /> 已保存
            </span>
          )}
        </div>
      </div>

      {/* ── Dimension mismatch dialog ── */}
      {dimMismatch?.mismatch && (
        <div className="modal-overlay" onClick={() => setDimMismatch(null)}>
          <div className="modal-content" style={{ maxWidth: 440 }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: "0 0 12px" }}>向量维度变更</h3>
            <p style={{ margin: "0 0 8px", lineHeight: 1.6 }}>
              检测到向量模型维度从 <strong>{dimMismatch.rag_chunks_dim ?? dimMismatch.profile_dim}</strong> 维
              变为 <strong>{dimMismatch.current_model_dim}</strong> 维，旧数据与新模型不兼容。
            </p>
            <p style={{ margin: "0 0 16px", fontSize: 13, color: "var(--muted)" }}>
              请选择处理方式：
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button
                style={{
                  padding: "8px 16px", fontSize: 13, borderRadius: "var(--radius)",
                  border: "1px solid var(--danger)", background: "transparent", color: "var(--danger)",
                  cursor: "pointer",
                }}
                disabled={rebuilding}
                onClick={handleClear}
              >
                {rebuilding ? "处理中..." : "清除所有数据"}
              </button>
              <button
                className="save-btn"
                disabled={rebuilding}
                onClick={handleRebuild}
              >
                {rebuilding ? (rebuildMessage || "重建中...") : "自动重建索引"}
              </button>
              {rebuilding && rebuildMessage && (
                <div style={{ marginTop: 8, fontSize: 13, color: "var(--text-dim)" }}>
                  {rebuildMessage}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
