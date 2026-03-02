import { useState } from "react";
import { DebugInfo } from "@/services/chatApi";

interface DebugPanelProps {
  info: DebugInfo | null;
  liveRaw?: unknown;
}

export function DebugPanel({ info, liveRaw }: DebugPanelProps) {
  const [open, setOpen] = useState(true);
  const latestRaw = info?.debug_turns?.[info.debug_turns.length - 1]?.response_raw;
  const rawToShow = liveRaw ?? latestRaw;
  const rawText =
    rawToShow == null
      ? "无"
      : typeof rawToShow === "string"
        ? rawToShow
        : JSON.stringify(rawToShow, null, 2);

  return (
    <div className="relative z-20 mx-auto mt-3 w-full max-w-4xl rounded-xl border border-app-border/30 bg-app-surface/85 p-3 backdrop-blur-md">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="w-full text-left font-mono text-xs tracking-widest text-app-info"
      >
        {open ? "隐藏开发面板" : "显示开发面板"}
      </button>
      {open && (
        <div className="mt-3 max-h-[36vh] space-y-3 overflow-y-auto pr-1 text-xs text-app-text">
          <div className="grid grid-cols-2 gap-2 font-mono text-app-muted">
            <div>Model: {info?.model || "N/A"}</div>
            <div>Tokens: {info?.token_estimate ?? 0}</div>
            <div>History: {info?.history_length ?? 0}</div>
            <div>Conversation: {info?.conversation_id || "N/A"}</div>
          </div>
          <div>
            <div className="mb-1 font-mono text-app-info">Summary</div>
            <pre className="max-h-28 overflow-auto rounded-md border border-app-border/25 bg-app-bg/50 p-2 text-[11px] text-app-text whitespace-pre-wrap">
              {info?.summary?.trim() ? info.summary : "无"}
            </pre>
          </div>
          <div>
            <div className="mb-1 font-mono text-app-info">System Prompt</div>
            <pre className="max-h-52 overflow-auto rounded-md border border-app-border/25 bg-app-bg/50 p-2 text-[11px] text-app-text whitespace-pre-wrap">
              {info?.system_prompt || "无"}
            </pre>
          </div>
          <div>
            <div className="mb-1 font-mono text-app-info">Latest RAW</div>
            <pre className="max-h-72 overflow-auto rounded-md border border-app-border/25 bg-app-bg/50 p-2 text-[11px] text-app-text whitespace-pre-wrap">
              {rawText}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

