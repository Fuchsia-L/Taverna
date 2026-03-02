import { BookOpen, ChevronDown, ChevronRight, FolderKanban, Plus, Settings, Sparkles, Terminal } from "lucide-react";
import { useState } from "react";
import { APP_CONFIG } from "@/config/app";
import { ConversationMeta, ProjectSummary } from "@/services/chatApi";

interface ChatSidebarProps {
  onOpenSettings: () => void;
  projects: ProjectSummary[];
  standaloneConversations: ConversationMeta[];
  activeConversationId: string | null;
  projectConversations: Record<string, ConversationMeta[]>;
  onSelectConversation: (conversationId: string) => void;
  onCreateProject: () => void;
  onCreateConversation: () => void;
}

export function ChatSidebar({
  onOpenSettings,
  projects,
  standaloneConversations,
  activeConversationId,
  projectConversations,
  onSelectConversation,
  onCreateProject,
  onCreateConversation,
}: ChatSidebarProps) {
  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>({});

  return (
    <aside className="relative z-20 hidden w-64 flex-shrink-0 flex-col border-r border-app-border/20 bg-app-surface/80 backdrop-blur-2xl md:flex">
      <div className="border-b border-app-border/20 p-6">
        <h1 className="flex items-center gap-2 bg-gradient-to-r from-app-accent to-app-info bg-clip-text text-2xl font-bold tracking-wider text-transparent">
          <Terminal size={24} className="text-app-info" />
          {APP_CONFIG.projectTitle}
        </h1>
        <p className="mt-2 flex items-center gap-1 font-mono text-[10px] uppercase tracking-widest text-app-muted/60">
          <Sparkles size={10} />
          {APP_CONFIG.text.sidebarSubtitle}
        </p>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        <div className="space-y-2">
          {projects.map((project) => {
            const collapsed = Boolean(collapsedProjects[project.id]);
            const conversations = projectConversations[project.id] || [];
            return (
              <div key={project.id} className="rounded-lg border border-app-border/20 bg-app-bg/25">
                <button
                  type="button"
                  onClick={() =>
                    setCollapsedProjects((prev) => ({ ...prev, [project.id]: !prev[project.id] }))
                  }
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-app-text"
                >
                  <span className="flex items-center gap-2 text-sm">
                    <FolderKanban size={14} className="text-app-info" />
                    {project.title}
                  </span>
                  {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                </button>
                {!collapsed && (
                  <div className="space-y-1 px-2 pb-2">
                    {conversations.map((conv) => (
                      <button
                        key={conv.id}
                        type="button"
                        onClick={() => onSelectConversation(conv.id)}
                        className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs ${
                          conv.id === activeConversationId
                            ? "bg-app-info/20 text-app-info"
                            : "text-app-muted hover:bg-app-border/10 hover:text-app-text"
                        }`}
                      >
                        <BookOpen size={12} />
                        <span>{conv.title || conv.objective || conv.type}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="rounded-lg border border-app-border/20 bg-app-bg/25 p-2">
          <div className="mb-1 px-1 text-[11px] uppercase tracking-wider text-app-muted/70">独立会话</div>
          <div className="space-y-1">
            {standaloneConversations.map((conv) => (
              <button
                key={conv.id}
                type="button"
                onClick={() => onSelectConversation(conv.id)}
                className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs ${
                  conv.id === activeConversationId
                    ? "bg-app-info/20 text-app-info"
                    : "text-app-muted hover:bg-app-border/10 hover:text-app-text"
                }`}
              >
                <BookOpen size={12} />
                <span>{conv.title || conv.objective || "未命名会话"}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={onCreateProject}
            className="flex flex-1 items-center justify-center gap-1 rounded border border-app-border/30 px-2 py-2 text-xs text-app-muted hover:text-app-info"
          >
            <Plus size={12} />
            新建项目
          </button>
          <button
            type="button"
            onClick={onCreateConversation}
            className="flex flex-1 items-center justify-center gap-1 rounded border border-app-border/30 px-2 py-2 text-xs text-app-muted hover:text-app-info"
          >
            <Plus size={12} />
            新建会话
          </button>
        </div>

        <button
          type="button"
          onClick={onOpenSettings}
          className="flex w-full items-center justify-between rounded-lg border border-transparent px-4 py-3 text-left text-app-muted transition-colors hover:border-app-border/20 hover:bg-app-border/10"
        >
          <span className="flex items-center gap-3">
            <Settings size={16} />
            <span className="text-sm font-medium">设置</span>
          </span>
          <span className="font-mono text-[10px] text-app-info">OPEN</span>
        </button>
      </div>
    </aside>
  );
}
