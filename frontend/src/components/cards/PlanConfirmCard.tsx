import { useCallback, useMemo, useState } from "react";
import type { PlanCardNode } from "@/types/chat";

interface PlanConfirmCardProps {
  nodes: PlanCardNode[];
  projectId: string;
  onConfirm: (projectId: string, nodes: PlanCardNode[]) => void;
  confirmed?: boolean;
}

export function PlanConfirmCard({ nodes, projectId, onConfirm, confirmed = false }: PlanConfirmCardProps) {
  const [items, setItems] = useState<PlanCardNode[]>(() =>
    nodes.map((n) => ({ ...n, objective: n.objective || "" }))
  );
  const [submitting, setSubmitting] = useState(false);
  const disabled = confirmed || submitting;

  const canConfirm = useMemo(
    () => !disabled && items.length > 0 && items.every((n) => n.title.trim()),
    [disabled, items]
  );

  const updateField = useCallback(
    (index: number, field: "title" | "objective", value: string) => {
      setItems((prev) => prev.map((n, i) => (i === index ? { ...n, [field]: value } : n)));
    },
    []
  );

  const moveItem = useCallback((index: number, direction: -1 | 1) => {
    setItems((prev) => {
      const target = index + direction;
      if (target < 0 || target >= prev.length) return prev;
      const next = [...prev];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }, []);

  const removeItem = useCallback((index: number) => {
    setItems((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== index)));
  }, []);

  const handleConfirm = useCallback(async () => {
    if (!canConfirm) return;
    setSubmitting(true);
    try {
      await onConfirm(projectId, items);
    } finally {
      setSubmitting(false);
    }
  }, [canConfirm, onConfirm, projectId, items]);

  return (
    <div className="mt-3 rounded-xl border border-app-info/35 bg-app-bg/35 p-3">
      <div className="mb-3 text-sm font-medium text-app-info">
        {confirmed ? "学习路径（已确认）" : "学习路径草案"}
      </div>
      <div className="space-y-2">
        {items.map((node, index) => (
          <div
            key={node.id}
            className="flex items-start gap-2 rounded-lg border border-app-border/25 bg-app-surface/40 p-2"
          >
            <span className="mt-1.5 shrink-0 text-xs font-mono text-app-muted">{index + 1}</span>
            <div className="min-w-0 flex-1 space-y-1">
              <input
                type="text"
                value={node.title}
                disabled={disabled}
                onChange={(e) => updateField(index, "title", e.target.value)}
                className="w-full rounded border border-app-border/35 bg-app-surface/60 px-2 py-1 text-sm text-app-text outline-none focus:border-app-info/60 disabled:opacity-65"
                placeholder="阶段标题"
              />
              <input
                type="text"
                value={node.objective}
                disabled={disabled}
                onChange={(e) => updateField(index, "objective", e.target.value)}
                className="w-full rounded border border-app-border/25 bg-app-surface/40 px-2 py-1 text-xs text-app-muted outline-none focus:border-app-info/40 disabled:opacity-65"
                placeholder="学习目标（可选）"
              />
            </div>
            {!disabled && (
              <div className="flex shrink-0 flex-col gap-0.5">
                <button
                  type="button"
                  disabled={index === 0}
                  onClick={() => moveItem(index, -1)}
                  className="rounded px-1 text-xs text-app-muted hover:text-app-text disabled:opacity-30"
                  title="上移"
                >
                  ▲
                </button>
                <button
                  type="button"
                  disabled={index === items.length - 1}
                  onClick={() => moveItem(index, 1)}
                  className="rounded px-1 text-xs text-app-muted hover:text-app-text disabled:opacity-30"
                  title="下移"
                >
                  ▼
                </button>
                <button
                  type="button"
                  disabled={items.length <= 1}
                  onClick={() => removeItem(index)}
                  className="rounded px-1 text-xs text-red-400 hover:text-red-300 disabled:opacity-30"
                  title="删除"
                >
                  ✕
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="mt-3 flex justify-end">
        <button
          type="button"
          disabled={!canConfirm}
          onClick={handleConfirm}
          className="rounded border border-app-info/40 bg-app-info/15 px-3 py-1.5 text-xs text-app-info hover:bg-app-info/25 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {confirmed ? "已确认" : submitting ? "确认中..." : "确认学习路径"}
        </button>
      </div>
    </div>
  );
}
