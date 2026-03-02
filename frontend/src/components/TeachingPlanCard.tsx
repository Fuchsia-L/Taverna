interface TeachingPlan {
  goal: string;
  current_phase: number | null;
  phases: {
    id: number;
    title: string;
    status: "pending" | "active" | "completed";
    summary?: string | null;
  }[];
}

interface TeachingPlanCardProps {
  open: boolean;
  plan: TeachingPlan | null;
  onClose: () => void;
}

function statusText(status: "pending" | "active" | "completed") {
  if (status === "completed") return "已完成";
  if (status === "active") return "进行中";
  return "未开始";
}

function statusClass(status: "pending" | "active" | "completed") {
  if (status === "completed") return "border-emerald-400/40 bg-emerald-500/10 text-emerald-300";
  if (status === "active") return "border-app-info/50 bg-app-info/10 text-app-info";
  return "border-app-border/35 bg-app-bg/35 text-app-muted";
}

export function TeachingPlanCard({ open, plan, onClose }: TeachingPlanCardProps) {
  if (!open || !plan) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[70] flex justify-end bg-black/45 backdrop-blur-[1px]">
      <button
        type="button"
        className="h-full flex-1 cursor-default"
        onClick={onClose}
        aria-label="close-overlay"
      />
      <aside className="h-full w-full max-w-md border-l border-app-border/40 bg-app-surface/95 p-5 shadow-2xl backdrop-blur-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold tracking-wide text-app-text">教学计划</h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-app-border/40 px-2 py-1 text-xs text-app-muted hover:text-app-text"
          >
            关闭
          </button>
        </div>

        <div className="mb-4 rounded-lg border border-app-border/35 bg-app-bg/35 p-3">
          <div className="mb-1 text-[11px] uppercase tracking-widest text-app-muted/75">目标</div>
          <div className="text-sm text-app-text">{plan.goal}</div>
        </div>

        <div className="space-y-3">
          {plan.phases.map((phase) => (
            <div key={phase.id} className={`rounded-lg border p-3 ${statusClass(phase.status)}`}>
              <div className="mb-1 flex items-center justify-between">
                <div className="text-sm font-medium">
                  阶段{phase.id} · {phase.title}
                </div>
                <div className="text-[11px]">{statusText(phase.status)}</div>
              </div>
              {phase.summary && <div className="mt-1 text-xs opacity-85">小结：{phase.summary}</div>}
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
