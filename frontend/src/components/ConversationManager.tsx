import { useState } from "react";
import { BookOpen, FolderKanban } from "lucide-react";
import type { ConversationMeta, ProjectSummary } from "@/services/chatApi";

interface ConversationManagerProps {
  open: boolean;
  onClose: () => void;
  projects: ProjectSummary[];
  projectConversations: Record<string, ConversationMeta[]>;
  standaloneConversations: ConversationMeta[];
  activeConversationId: string | null;
  onDeleteConversation: (id: string) => Promise<void>;
  onDeleteProject: (id: string) => Promise<void>;
}

export function ConversationManager({
  open,
  onClose,
  projects,
  projectConversations,
  standaloneConversations,
  activeConversationId,
  onDeleteConversation,
  onDeleteProject,
}: ConversationManagerProps) {
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  if (!open) return null;

  const handleDelete = async (type: "conversation" | "project", id: string) => {
    setDeleting(true);
    try {
      if (type === "project") {
        await onDeleteProject(id);
      } else {
        await onDeleteConversation(id);
      }
    } catch (error) {
      console.error("Delete failed:", error);
    } finally {
      setConfirmingId(null);
      setDeleting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex justify-end bg-black/45 backdrop-blur-[1px]">
      <button
        type="button"
        className="h-full flex-1 cursor-default"
        onClick={onClose}
        aria-label="close-overlay"
      />

      <aside className="flex h-full w-full max-w-md flex-col border-l border-app-border/40 bg-app-surface/95 p-5 shadow-2xl backdrop-blur-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold tracking-wide text-app-text">
            会话管理
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-app-border/40 px-2 py-1 text-xs text-app-muted hover:text-app-text"
          >
            关闭
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto">
          {projects.map((project) => {
            const convs = projectConversations[project.id] || [];
            const isConfirmingProject = confirmingId === `project-${project.id}`;

            return (
              <div
                key={project.id}
                className="rounded-lg border border-app-border/35 bg-app-bg/25 p-3"
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-app-text">
                    <FolderKanban size={14} className="shrink-0 text-app-info" />
                    <span className="truncate">{project.title}</span>
                  </div>
                  {isConfirmingProject ? (
                    <span className="flex shrink-0 items-center gap-1 text-xs">
                      <span className="text-red-400">确定？</span>
                      <button
                        type="button"
                        disabled={deleting}
                        onClick={() => handleDelete("project", project.id)}
                        className="rounded border border-red-500/50 px-1.5 py-0.5 text-red-400 hover:bg-red-500/20 disabled:opacity-50"
                      >
                        确认
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmingId(null)}
                        className="rounded border border-app-border/40 px-1.5 py-0.5 text-app-muted hover:text-app-text"
                      >
                        取消
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmingId(`project-${project.id}`)}
                      className="shrink-0 rounded border border-red-500/30 px-1.5 py-0.5 text-xs text-red-400/70 hover:border-red-500/50 hover:text-red-400"
                    >
                      删除项目
                    </button>
                  )}
                </div>

                <div className="space-y-1 pl-3">
                  {convs.map((conv) => (
                    <ConversationRow
                      key={conv.id}
                      conv={conv}
                      isActive={conv.id === activeConversationId}
                      isConfirming={confirmingId === `conv-${conv.id}`}
                      deleting={deleting}
                      onRequestConfirm={() => setConfirmingId(`conv-${conv.id}`)}
                      onCancelConfirm={() => setConfirmingId(null)}
                      onConfirmDelete={() => handleDelete("conversation", conv.id)}
                    />
                  ))}
                  {convs.length === 0 && (
                    <div className="py-1 text-xs text-app-muted/50">无会话</div>
                  )}
                </div>
              </div>
            );
          })}

          {standaloneConversations.length > 0 && (
            <div className="rounded-lg border border-app-border/35 bg-app-bg/25 p-3">
              <div className="mb-2 text-[11px] uppercase tracking-wider text-app-muted/70">
                独立会话
              </div>
              <div className="space-y-1">
                {standaloneConversations.map((conv) => (
                  <ConversationRow
                    key={conv.id}
                    conv={conv}
                    isActive={conv.id === activeConversationId}
                    isConfirming={confirmingId === `conv-${conv.id}`}
                    deleting={deleting}
                    onRequestConfirm={() => setConfirmingId(`conv-${conv.id}`)}
                    onCancelConfirm={() => setConfirmingId(null)}
                    onConfirmDelete={() => handleDelete("conversation", conv.id)}
                  />
                ))}
              </div>
            </div>
          )}

          {projects.length === 0 && standaloneConversations.length === 0 && (
            <div className="py-8 text-center text-sm text-app-muted/60">
              暂无会话或项目
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

function ConversationRow({
  conv,
  isActive,
  isConfirming,
  deleting,
  onRequestConfirm,
  onCancelConfirm,
  onConfirmDelete,
}: {
  conv: ConversationMeta;
  isActive: boolean;
  isConfirming: boolean;
  deleting: boolean;
  onRequestConfirm: () => void;
  onCancelConfirm: () => void;
  onConfirmDelete: () => void;
}) {
  return (
    <div
      className={`flex items-center justify-between rounded px-2 py-1 text-xs ${
        isActive ? "bg-app-info/10 text-app-info" : "text-app-muted"
      }`}
    >
      <span className="flex min-w-0 items-center gap-1.5">
        <BookOpen size={11} className="shrink-0" />
        <span className="truncate">{conv.title || conv.type}</span>
        {isActive && <span className="shrink-0 text-[10px]">(当前)</span>}
      </span>
      {isConfirming ? (
        <span className="flex shrink-0 items-center gap-1 text-[11px]">
          <button
            type="button"
            disabled={deleting}
            onClick={onConfirmDelete}
            className="text-red-400 hover:underline disabled:opacity-50"
          >
            确认
          </button>
          <button
            type="button"
            onClick={onCancelConfirm}
            className="text-app-muted hover:text-app-text"
          >
            取消
          </button>
        </span>
      ) : (
        <button
          type="button"
          onClick={onRequestConfirm}
          className="shrink-0 text-[11px] text-red-400/50 hover:text-red-400"
        >
          删除
        </button>
      )}
    </div>
  );
}
