import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { fetchEnabledModels, ModelOption } from "@/services/providerApi";

const SETTINGS_STORAGE_KEY = "taverna_settings_v1";
const MODELS_CACHE_KEY = "taverna_models_cache";

export type { ModelOption };

export interface SettingsState {
  streaming: boolean;
  model: string;
  thinking: boolean;
  devMode: boolean;
  conversationId: string | null;
}

interface SettingsContextValue {
  settings: SettingsState;
  modelOptions: ModelOption[];
  setStreaming: (streaming: boolean) => void;
  setModel: (model: string) => void;
  setThinking: (thinking: boolean) => void;
  setDevMode: (devMode: boolean) => void;
  setConversationId: (conversationId: string | null) => void;
  refreshModels: () => Promise<void>;
  /** Whether the current model supports thinking toggle */
  canToggleThinking: boolean;
}

const DEFAULT_SETTINGS: SettingsState = {
  streaming: true,
  model: "",
  thinking: false,
  devMode: false,
  conversationId: null,
};

const SettingsContext = createContext<SettingsContextValue | null>(null);

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function normalizeConversationId(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed && UUID_RE.test(trimmed) ? trimmed : null;
}

/**
 * For pair-mode: find the thinking counterpart in the model list.
 * Priority: 1) explicit thinking_pair field  2) reverse lookup  3) -thinking suffix match.
 */
function findThinkingPair(modelId: string, models: ModelOption[]): string | null {
  const current = models.find((m) => m.id === modelId);
  if (!current) return null;
  const pid = current.provider_id;

  // 1) Explicit thinking_pair on this model
  if (current.thinking_pair) {
    const target = models.find((m) => m.id === current.thinking_pair && m.provider_id === pid);
    if (target) return target.id;
  }

  // 2) Reverse: another model's thinking_pair points to us
  const reverse = models.find((m) => m.thinking_pair === modelId && m.provider_id === pid);
  if (reverse) return reverse.id;

  // 3) Fallback: -thinking suffix auto-match
  const isThinking = modelId.endsWith("-thinking");
  const counterId = isThinking ? modelId.slice(0, -"-thinking".length) : modelId + "-thinking";
  const counter = models.find((m) => m.id === counterId && m.provider_id === pid);
  return counter ? counter.id : null;
}

function isThinkingActive(model: string, models: ModelOption[]): boolean {
  const found = models.find((m) => m.id === model);
  if (!found) return false;
  if (found.thinking_mode === "pair") {
    // A model is the "thinking" side if it has is_thinking flag,
    // or if another model's thinking_pair points to it.
    return !!found.is_thinking;
  }
  // For params mode, thinking state is independent of model ID
  // (preserved from settings). For none mode, always false.
  return false;
}

function loadCachedModels(): ModelOption[] {
  try {
    const raw = localStorage.getItem(MODELS_CACHE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function loadSettings(models: ModelOption[]): SettingsState {
  const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
  if (!raw) return DEFAULT_SETTINGS;

  try {
    const parsed = JSON.parse(raw) as Partial<SettingsState>;
    const modelValid = parsed.model && models.some((m) => m.id === parsed.model);
    const model = modelValid ? parsed.model! : DEFAULT_SETTINGS.model;
    return {
      streaming: parsed.streaming ?? DEFAULT_SETTINGS.streaming,
      model,
      thinking: parsed.thinking ?? isThinkingActive(model, models),
      devMode: parsed.devMode ?? DEFAULT_SETTINGS.devMode,
      conversationId: normalizeConversationId(parsed.conversationId) ?? DEFAULT_SETTINGS.conversationId,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [modelOptions, setModelOptions] = useState<ModelOption[]>(() => loadCachedModels());
  const [defaultModel, setDefaultModel] = useState<string | null>(null);
  const [settings, setSettings] = useState<SettingsState>(() => loadSettings(loadCachedModels()));

  const refreshModels = useCallback(async () => {
    try {
      const data = await fetchEnabledModels();
      setModelOptions(data.models);
      setDefaultModel(data.default_model);
      localStorage.setItem(MODELS_CACHE_KEY, JSON.stringify(data.models));
    } catch {
      // keep cached models
    }
  }, []);

  // Fetch models on mount
  useEffect(() => {
    refreshModels();
  }, [refreshModels]);

  // When models load and current model is empty or invalid, set to default
  useEffect(() => {
    if (modelOptions.length === 0) return;
    setSettings((prev) => {
      const currentValid = prev.model && modelOptions.some((m) => m.id === prev.model);
      if (currentValid) return prev;
      const fallback = defaultModel || modelOptions[0].id;
      return { ...prev, model: fallback, thinking: isThinkingActive(fallback, modelOptions) };
    });
  }, [modelOptions, defaultModel]);

  // Persist settings
  useEffect(() => {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  }, [settings]);

  // Stable setter refs
  const modelOptionsRef = useRef(modelOptions);
  modelOptionsRef.current = modelOptions;

  const setStreaming = useCallback(
    (streaming: boolean) =>
      setSettings((prev) => (prev.streaming === streaming ? prev : { ...prev, streaming })),
    []
  );

  const setModel = useCallback(
    (model: string) =>
      setSettings((prev) => {
        const valid = modelOptionsRef.current.some((m: ModelOption) => m.id === model);
        const nextModel = valid ? model : prev.model;
        if (prev.model === nextModel) return prev;
        return { ...prev, model: nextModel, thinking: isThinkingActive(nextModel, modelOptionsRef.current) };
      }),
    []
  );

  const setThinking = useCallback(
    (thinking: boolean) =>
      setSettings((prev) => {
        if (prev.thinking === thinking) return prev;
        const current = modelOptionsRef.current.find((m) => m.id === prev.model);
        if (!current) return prev;
        const mode = current.thinking_mode;

        if (mode === "pair") {
          // Switch model ID to/from -thinking counterpart
          const pair = findThinkingPair(prev.model, modelOptionsRef.current);
          if (!pair) return prev; // no counterpart, can't toggle
          return { ...prev, thinking, model: pair };
        }

        if (mode === "params") {
          // Same model ID, just flip the flag — backend will inject extra_body
          return { ...prev, thinking };
        }

        // mode === "none" — shouldn't reach here, but don't break
        return prev;
      }),
    []
  );

  const setDevMode = useCallback(
    (devMode: boolean) =>
      setSettings((prev) => (prev.devMode === devMode ? prev : { ...prev, devMode })),
    []
  );
  const setConversationId = useCallback(
    (conversationId: string | null) =>
      setSettings((prev) => {
        const next = normalizeConversationId(conversationId);
        return prev.conversationId === next ? prev : { ...prev, conversationId: next };
      }),
    []
  );

  // Whether current model can toggle thinking
  const canToggleThinking = useMemo(() => {
    const current = modelOptions.find((m) => m.id === settings.model);
    if (!current) return false;
    if (current.thinking_mode === "params") {
      // Can toggle if thinking_extra_params is configured
      return !!current.thinking_extra_params;
    }
    if (current.thinking_mode === "pair") {
      return !!findThinkingPair(settings.model, modelOptions);
    }
    return false;
  }, [modelOptions, settings.model]);

  const value = useMemo<SettingsContextValue>(
    () => ({
      settings,
      modelOptions,
      refreshModels,
      setStreaming,
      setModel,
      setThinking,
      setDevMode,
      setConversationId,
      canToggleThinking,
    }),
    [settings, modelOptions, refreshModels, setStreaming, setModel, setThinking, setDevMode, setConversationId, canToggleThinking]
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings() {
  const ctx = useContext(SettingsContext);
  if (!ctx) {
    throw new Error("useSettings must be used inside SettingsProvider");
  }
  return ctx;
}
