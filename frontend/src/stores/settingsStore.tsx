import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";

const SETTINGS_STORAGE_KEY = "taverna_settings_v1";

export const MODEL_OPTIONS = [
  "gpt-4o-mini",
  "gpt-4o",
  "deepseek-chat",
  "deepseek-reasoner",
  "gemini-3-pro-preview-thinking",
  "claude-sonnet-4-5-20250929",
  "claude-sonnet-4-5-20250929-thinking",
  "claude-opus-4-6-thinking"
] as const;

export interface SettingsState {
  streaming: boolean;
  model: string;
  devMode: boolean;
  conversationId: string | null;
}

interface SettingsContextValue {
  settings: SettingsState;
  modelOptions: readonly string[];
  setStreaming: (streaming: boolean) => void;
  setModel: (model: string) => void;
  setDevMode: (devMode: boolean) => void;
  setConversationId: (conversationId: string | null) => void;
}

const DEFAULT_SETTINGS: SettingsState = {
  streaming: true,
  model: "gpt-4o-mini",
  devMode: false,
  conversationId: null
};

const SettingsContext = createContext<SettingsContextValue | null>(null);

function loadSettings(): SettingsState {
  const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
  if (!raw) {
    return DEFAULT_SETTINGS;
  }

  try {
    const parsed = JSON.parse(raw) as Partial<SettingsState>;
    const model = parsed.model && MODEL_OPTIONS.includes(parsed.model as (typeof MODEL_OPTIONS)[number])
      ? parsed.model
      : DEFAULT_SETTINGS.model;
    return {
      streaming: parsed.streaming ?? DEFAULT_SETTINGS.streaming,
      model,
      devMode: parsed.devMode ?? DEFAULT_SETTINGS.devMode,
      conversationId: parsed.conversationId ?? DEFAULT_SETTINGS.conversationId
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<SettingsState>(() => loadSettings());

  useEffect(() => {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  }, [settings]);

  const value = useMemo<SettingsContextValue>(
    () => ({
      settings,
      modelOptions: MODEL_OPTIONS,
      setStreaming: (streaming) =>
        setSettings((prev) => (prev.streaming === streaming ? prev : { ...prev, streaming })),
      setModel: (model) =>
        setSettings((prev) => {
          const nextModel = MODEL_OPTIONS.includes(model as (typeof MODEL_OPTIONS)[number]) ? model : prev.model;
          return prev.model === nextModel ? prev : { ...prev, model: nextModel };
        }),
      setDevMode: (devMode) => setSettings((prev) => (prev.devMode === devMode ? prev : { ...prev, devMode })),
      setConversationId: (conversationId) => setSettings((prev) => (prev.conversationId === conversationId ? prev : { ...prev, conversationId }))
    }),
    [settings]
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

