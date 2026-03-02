import { X } from "lucide-react";
import { useSettings } from "@/stores/settingsStore";

interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
}

export function SettingsPanel({ open, onClose }: SettingsPanelProps) {
  const { settings, setStreaming, setModel, setDevMode, modelOptions } = useSettings();

  if (!open) {
    return null;
  }

  return (
    <div className="absolute inset-0 z-40 flex justify-end bg-black/40 backdrop-blur-sm">
      <div className="h-full w-full max-w-sm border-l border-app-border/30 bg-app-surface/95 p-6 shadow-[var(--app-shadow-teacher)]">
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
          <label className="flex items-center justify-between rounded-lg border border-app-border/25 bg-app-surface-alt/60 px-4 py-3">
            <span className="text-sm text-app-text">流式输出</span>
            <input
              type="checkbox"
              checked={settings.streaming}
              onChange={(event) => setStreaming(event.target.checked)}
              className="h-4 w-4 accent-[hsl(var(--app-info))]"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm text-app-text">模型选择</span>
            <select
              value={settings.model}
              onChange={(event) => setModel(event.target.value)}
              className="app-select w-full rounded-lg border border-app-border/30 bg-app-surface-alt/70 px-3 py-2 text-sm text-app-text outline-none focus:border-app-info/60"
            >
              {modelOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center justify-between rounded-lg border border-app-border/25 bg-app-surface-alt/60 px-4 py-3">
            <span className="text-sm text-app-text">开发者模式</span>
            <input
              type="checkbox"
              checked={settings.devMode}
              onChange={(event) => setDevMode(event.target.checked)}
              className="h-4 w-4 accent-[hsl(var(--app-accent))]"
            />
          </label>
        </div>
      </div>
    </div>
  );
}
