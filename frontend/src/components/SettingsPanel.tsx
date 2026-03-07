import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Eye, EyeOff, Plus, RefreshCw, Search, Trash2, X } from "lucide-react";
import { useSettings, ModelOption } from "@/stores/settingsStore";
import {
  createProvider,
  deleteProvider,
  fetchProviders,
  fetchRemoteModels,
  Provider,
  RemoteModel,
  updateEnabledModels,
} from "@/services/providerApi";

interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
}

export function SettingsPanel({ open, onClose }: SettingsPanelProps) {
  const { settings, setStreaming, setModel, setThinking, setDevMode, modelOptions, refreshModels, canToggleThinking } =
    useSettings();
  const [providerOpen, setProviderOpen] = useState(false);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [addForm, setAddForm] = useState({ name: "", base_url: "", api_key: "" });
  const [addOpen, setAddOpen] = useState(false);
  const [fetchingModels, setFetchingModels] = useState<string | null>(null);
  const [remoteModels, setRemoteModels] = useState<RemoteModel[]>([]);
  const [remoteProviderId, setRemoteProviderId] = useState<string | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [remoteModelSearch, setRemoteModelSearch] = useState("");
  const [modelSearch, setModelSearch] = useState("");
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const modelDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open && providerOpen) {
      fetchProviders().then(setProviders).catch(() => {});
    }
  }, [open, providerOpen]);

  useEffect(() => {
    if (!open) {
      setModelDropdownOpen(false);
      setModelSearch("");
    }
  }, [open]);

  if (!open) return null;

  const handleAddProvider = async () => {
    if (!addForm.name || !addForm.base_url || !addForm.api_key) return;
    try {
      await createProvider(addForm);
      setAddForm({ name: "", base_url: "", api_key: "" });
      setAddOpen(false);
      const updated = await fetchProviders();
      setProviders(updated);
    } catch (e) {
      alert(`添加失败: ${e}`);
    }
  };

  const handleDeleteProvider = async (id: string) => {
    if (!confirm("确定删除该 Provider 及其所有模型？")) return;
    try {
      await deleteProvider(id);
      setProviders((prev) => prev.filter((p) => p.id !== id));
      await refreshModels();
    } catch (e) {
      alert(`删除失败: ${e}`);
    }
  };

  const handleFetchModels = async (providerId: string) => {
    setFetchingModels(providerId);
    try {
      const models = await fetchRemoteModels(providerId);
      setRemoteModels(models);
      setRemoteProviderId(providerId);
      setRemoteModelSearch("");
    } catch (e) {
      alert(`获取模型失败: ${e}`);
    } finally {
      setFetchingModels(null);
    }
  };

  const handleToggleModel = async (remoteModel: RemoteModel) => {
    if (!remoteProviderId) return;
    const provider = providers.find((p) => p.id === remoteProviderId);
    const thinkingMode = provider?.thinking_mode ?? "none";
    const exists = modelOptions.find(
      (m) => m.id === remoteModel.id && m.provider_id === remoteProviderId
    );

    const stripMeta = ({ provider_name: _, thinking_mode: _tm, ...rest }: ModelOption) => rest;

    let next: Omit<ModelOption, "provider_name" | "thinking_mode">[];
    if (exists) {
      // Remove model — also remove its -thinking pair if in pair mode
      let removeIds = [remoteModel.id];
      if (thinkingMode === "pair") {
        const isThinking = remoteModel.id.endsWith("-thinking");
        const counterId = isThinking
          ? remoteModel.id.slice(0, -"-thinking".length)
          : remoteModel.id + "-thinking";
        removeIds.push(counterId);
      }
      next = modelOptions
        .filter((m) => !(removeIds.includes(m.id) && m.provider_id === remoteProviderId))
        .map(stripMeta);
    } else {
      const isThinking = remoteModel.id.endsWith("-thinking") || remoteModel.id.includes("reasoner");
      const newModel: Omit<ModelOption, "provider_name" | "thinking_mode"> = {
        id: remoteModel.id,
        display_name: remoteModel.id,
        provider_id: remoteProviderId,
        is_thinking: isThinking,
      };
      const additions: Omit<ModelOption, "provider_name" | "thinking_mode">[] = [newModel];

      // Auto-pair: for pair-mode providers, find and add the counterpart
      if (thinkingMode === "pair") {
        const counterpartId = isThinking
          ? remoteModel.id.slice(0, -"-thinking".length)
          : remoteModel.id + "-thinking";
        const counterpartExists = modelOptions.some(
          (m) => m.id === counterpartId && m.provider_id === remoteProviderId
        );
        if (!counterpartExists) {
          const counterpartRemote = remoteModels.find((rm) => rm.id === counterpartId);
          if (counterpartRemote) {
            additions.push({
              id: counterpartRemote.id,
              display_name: counterpartRemote.id,
              provider_id: remoteProviderId,
              is_thinking: counterpartRemote.id.endsWith("-thinking") || counterpartRemote.id.includes("reasoner"),
            });
          } else {
            alert(`提示：未在此 Provider 找到对应模型 "${counterpartId}"，Thinking 切换可能不可用。`);
          }
        }
      }

      next = [...modelOptions.map(stripMeta), ...additions];
    }
    try {
      await updateEnabledModels(next);
      await refreshModels();
    } catch (e) {
      alert(`更新失败: ${e}`);
    }
  };

  // Group models by provider
  const grouped = modelOptions.reduce<Record<string, ModelOption[]>>((acc, m) => {
    const key = m.provider_name || m.provider_id;
    (acc[key] ??= []).push(m);
    return acc;
  }, {});

  return (
    <div className="absolute inset-0 z-40 flex justify-end bg-black/40 backdrop-blur-sm">
      <div className="h-full w-full max-w-sm overflow-y-auto border-l border-app-border/30 bg-app-surface/95 p-6 shadow-[var(--app-shadow-teacher)]">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-lg font-semibold tracking-wide text-app-text">设置</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-app-border/30 p-2 text-app-muted transition-colors hover:border-app-info/40 hover:text-app-info"
          >
            <X size={16} />
          </button>
        </div>

        <div className="space-y-6">
          {/* ── Streaming ── */}
          <label className="flex items-center justify-between rounded-lg border border-app-border/25 bg-app-surface-alt/60 px-4 py-3">
            <span className="text-sm text-app-text">流式输出</span>
            <input
              type="checkbox"
              checked={settings.streaming}
              onChange={(e) => setStreaming(e.target.checked)}
              className="h-4 w-4 accent-[hsl(var(--app-info))]"
            />
          </label>

          {/* ── Model select (searchable) ── */}
          <div className="block space-y-2">
            <span className="text-sm text-app-text">模型选择</span>
            <div ref={modelDropdownRef} className="relative"
              onBlur={(e) => {
                if (!modelDropdownRef.current?.contains(e.relatedTarget as Node)) {
                  setModelDropdownOpen(false);
                  setModelSearch("");
                }
              }}
            >
              <div
                className="flex w-full items-center gap-2 rounded-lg border border-app-border/30 bg-app-surface-alt/70 px-3 py-2 text-sm text-app-text cursor-pointer"
                onClick={() => setModelDropdownOpen(!modelDropdownOpen)}
              >
                <span className="flex-1 truncate">
                  {modelOptions.find((m) => m.id === settings.model)?.display_name || settings.model || "（未配置模型）"}
                </span>
                <ChevronDown size={14} className={`text-app-muted transition-transform ${modelDropdownOpen ? "rotate-180" : ""}`} />
              </div>

              {modelDropdownOpen && (
                <div className="absolute left-0 right-0 top-full z-50 mt-1 rounded-lg border border-app-border/30 bg-app-surface shadow-lg">
                  <div className="flex items-center gap-2 border-b border-app-border/20 px-3 py-2">
                    <Search size={14} className="shrink-0 text-app-muted" />
                    <input
                      type="text"
                      autoFocus
                      placeholder="搜索模型..."
                      value={modelSearch}
                      onChange={(e) => setModelSearch(e.target.value)}
                      className="w-full bg-transparent text-xs text-app-text outline-none placeholder:text-app-muted/60"
                    />
                  </div>
                  <div className="max-h-56 overflow-y-auto p-1">
                    {(() => {
                      const keyword = modelSearch.toLowerCase();
                      const filteredGrouped = Object.entries(grouped).reduce<Record<string, ModelOption[]>>(
                        (acc, [providerName, models]) => {
                          const filtered = models.filter(
                            (m) => {
                              // Hide pair-mode thinking models — only reachable via toggle
                              if (m.thinking_mode === "pair" && m.is_thinking) return false;
                              return m.display_name.toLowerCase().includes(keyword) ||
                                m.id.toLowerCase().includes(keyword);
                            }
                          );
                          if (filtered.length > 0) acc[providerName] = filtered;
                          return acc;
                        },
                        {}
                      );
                      const entries = Object.entries(filteredGrouped);
                      if (entries.length === 0) {
                        return <div className="px-3 py-4 text-center text-xs text-app-muted">无匹配模型</div>;
                      }
                      return entries.map(([providerName, models]) => (
                        <div key={providerName}>
                          <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-app-muted/70">
                            {providerName}
                          </div>
                          {models.map((m) => (
                            <button
                              key={m.id}
                              type="button"
                              className={`flex w-full items-center rounded px-2 py-1.5 text-left text-xs transition-colors ${
                                m.id === settings.model
                                  ? "bg-app-info/15 text-app-info"
                                  : "text-app-text hover:bg-app-surface-alt/80"
                              }`}
                              onMouseDown={(e) => e.preventDefault()}
                              onClick={() => {
                                setModel(m.id);
                                setModelDropdownOpen(false);
                                setModelSearch("");
                              }}
                            >
                              <span className="truncate">{m.display_name}</span>
                            </button>
                          ))}
                        </div>
                      ));
                    })()}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* ── Thinking toggle ── */}
          <label className={`flex items-center justify-between rounded-lg border border-app-border/25 bg-app-surface-alt/60 px-4 py-3 ${!canToggleThinking ? "opacity-50" : ""}`}>
            <span className="text-sm text-app-text">思考模式{!canToggleThinking && " (不可用)"}</span>
            <input
              type="checkbox"
              checked={settings.thinking}
              onChange={(e) => setThinking(e.target.checked)}
              disabled={!canToggleThinking}
              className="h-4 w-4 accent-[hsl(var(--app-info))]"
            />
          </label>

          {/* ── Dev mode ── */}
          <label className="flex items-center justify-between rounded-lg border border-app-border/25 bg-app-surface-alt/60 px-4 py-3">
            <span className="text-sm text-app-text">开发者模式</span>
            <input
              type="checkbox"
              checked={settings.devMode}
              onChange={(e) => setDevMode(e.target.checked)}
              className="h-4 w-4 accent-[hsl(var(--app-accent))]"
            />
          </label>

          {/* ── Provider management ── */}
          <div className="rounded-lg border border-app-border/25 bg-app-surface-alt/60">
            <button
              type="button"
              onClick={() => setProviderOpen(!providerOpen)}
              className="flex w-full items-center justify-between px-4 py-3 text-sm text-app-text"
            >
              <span>Provider 管理</span>
              {providerOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>

            {providerOpen && (
              <div className="border-t border-app-border/20 px-4 pb-4 pt-3 space-y-3">
                {/* Provider list */}
                {providers.map((p) => (
                  <div key={p.id} className="flex items-center justify-between gap-2 rounded border border-app-border/20 px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-app-text">{p.name}</div>
                      <div className="truncate text-xs text-app-muted">{p.base_url}</div>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <button
                        type="button"
                        onClick={() => handleFetchModels(p.id)}
                        disabled={fetchingModels === p.id}
                        className="rounded p-1 text-app-muted hover:text-app-info disabled:opacity-50"
                        title="获取模型"
                      >
                        <RefreshCw size={14} className={fetchingModels === p.id ? "animate-spin" : ""} />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeleteProvider(p.id)}
                        className="rounded p-1 text-app-muted hover:text-red-400"
                        title="删除"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))}

                {/* Remote models checklist */}
                {remoteProviderId && remoteModels.length > 0 && (
                  <div className="rounded border border-app-border/20 space-y-1">
                    <div className="flex items-center gap-2 border-b border-app-border/20 px-2 py-1.5">
                      <Search size={12} className="shrink-0 text-app-muted" />
                      <input
                        type="text"
                        placeholder="搜索模型..."
                        value={remoteModelSearch}
                        onChange={(e) => setRemoteModelSearch(e.target.value)}
                        className="w-full bg-transparent text-xs text-app-text outline-none placeholder:text-app-muted/60"
                      />
                    </div>
                    <div className="max-h-48 overflow-y-auto p-2 pt-0 space-y-1">
                      <div className="mb-1 text-xs font-medium text-app-muted">
                        可用模型（勾选启用）
                      </div>
                      {(() => {
                        const keyword = remoteModelSearch.toLowerCase();
                        const filtered = remoteModels.filter((rm) =>
                          rm.id.toLowerCase().includes(keyword)
                        );
                        if (filtered.length === 0) {
                          return <div className="px-1 py-2 text-center text-xs text-app-muted">无匹配模型</div>;
                        }
                        return filtered.map((rm) => {
                          const enabled = modelOptions.some(
                            (m) => m.id === rm.id && m.provider_id === remoteProviderId
                          );
                          return (
                            <label key={rm.id} className="flex items-center gap-2 text-xs text-app-text cursor-pointer hover:bg-app-surface-alt/80 rounded px-1 py-0.5">
                              <input
                                type="checkbox"
                                checked={enabled}
                                onChange={() => handleToggleModel(rm)}
                                className="h-3 w-3 accent-[hsl(var(--app-info))]"
                              />
                              <span className="truncate">{rm.id}</span>
                            </label>
                          );
                        });
                      })()}
                    </div>
                  </div>
                )}

                {/* Add provider form */}
                {addOpen ? (
                  <div className="space-y-2 rounded border border-app-border/20 p-3">
                    <input
                      type="text"
                      placeholder="名称 (如 OpenRouter)"
                      value={addForm.name}
                      onChange={(e) => setAddForm((f) => ({ ...f, name: e.target.value }))}
                      className="w-full rounded border border-app-border/30 bg-app-surface/80 px-2 py-1.5 text-xs text-app-text outline-none focus:border-app-info/60"
                    />
                    <input
                      type="text"
                      placeholder="Base URL (如 https://api.openai.com/v1)"
                      value={addForm.base_url}
                      onChange={(e) => setAddForm((f) => ({ ...f, base_url: e.target.value }))}
                      className="w-full rounded border border-app-border/30 bg-app-surface/80 px-2 py-1.5 text-xs text-app-text outline-none focus:border-app-info/60"
                    />
                    <div className="relative">
                      <input
                        type={showKey ? "text" : "password"}
                        placeholder="API Key"
                        value={addForm.api_key}
                        onChange={(e) => setAddForm((f) => ({ ...f, api_key: e.target.value }))}
                        className="w-full rounded border border-app-border/30 bg-app-surface/80 px-2 py-1.5 pr-8 text-xs text-app-text outline-none focus:border-app-info/60"
                      />
                      <button
                        type="button"
                        onClick={() => setShowKey(!showKey)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-app-muted hover:text-app-text"
                      >
                        {showKey ? <EyeOff size={12} /> : <Eye size={12} />}
                      </button>
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={handleAddProvider}
                        className="rounded border border-app-info/40 px-3 py-1 text-xs text-app-info hover:bg-app-info/10"
                      >
                        保存
                      </button>
                      <button
                        type="button"
                        onClick={() => setAddOpen(false)}
                        className="rounded border border-app-border/30 px-3 py-1 text-xs text-app-muted hover:text-app-text"
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setAddOpen(true)}
                    className="flex w-full items-center justify-center gap-1 rounded border border-dashed border-app-border/30 py-2 text-xs text-app-muted hover:border-app-info/40 hover:text-app-info"
                  >
                    <Plus size={14} />
                    添加 Provider
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
