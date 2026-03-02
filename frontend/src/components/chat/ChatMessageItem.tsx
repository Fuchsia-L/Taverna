import { FormEvent, useEffect, useMemo, useState } from "react";
import { Bot, ChevronDown, Lightbulb, Pencil, RefreshCw, User } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { AssessCard } from "@/components/cards/AssessCard";
import { cn } from "@/lib/utils";
import { ChatMessage } from "@/types/chat";
import { parseThinking } from "@/utils/parseThinking";
import { preprocessLaTeX } from "@/utils/mathPreprocess";

interface ChatMessageItemProps {
  message: ChatMessage;
  devMode: boolean;
  canRetry: boolean;
  onRetry: () => void;
  onEditSave: (value: string) => void;
  onPrevVersion: () => void;
  onNextVersion: () => void;
  onToolSubmit: (toolCallId: string, answers: Array<{ question: string; answer: string }>) => void;
}

function MessageContent({ content, className }: { content: string; className?: string }) {
  const normalized = useMemo(() => preprocessLaTeX(content), [content]);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath]}
      rehypePlugins={[[rehypeKatex, { throwOnError: false, errorColor: "#ef4444" }]]}
      components={{
        p: ({ children }) => <p className={cn("my-2", className)}>{children}</p>,
        ul: ({ children }) => <ul className="my-2 list-disc pl-5">{children}</ul>,
        ol: ({ children }) => <ol className="my-2 list-decimal pl-5">{children}</ol>,
        li: ({ children }) => <li className="my-1">{children}</li>,
        code: ({ className: codeClassName, children, ...props }) => {
          const isBlock = Boolean(codeClassName);
          if (isBlock) {
            return (
              <code className={cn("block overflow-x-auto rounded-md bg-app-bg/60 p-3 font-mono text-xs", codeClassName)} {...props}>
                {children}
              </code>
            );
          }
          return (
            <code className="rounded bg-app-bg/60 px-1.5 py-0.5 font-mono text-[0.9em]" {...props}>
              {children}
            </code>
          );
        },
        pre: ({ children }) => <pre className="my-2 overflow-x-auto">{children}</pre>,
      }}
    >
      {normalized}
    </ReactMarkdown>
  );
}

export function ChatMessageItem({
  message,
  devMode,
  canRetry,
  onRetry,
  onEditSave,
  onPrevVersion,
  onNextVersion,
  onToolSubmit
}: ChatMessageItemProps) {
  const [showThinking, setShowThinking] = useState(false);
  const [thinkingAutoCollapsed, setThinkingAutoCollapsed] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);
  const isTeacher = message.role === "teacher";
  const parsed = useMemo(() => parseThinking(message.content), [message.content]);
  const showParsedTeacher = isTeacher && !message.isStreaming;
  const streamingVisible = isTeacher && message.isStreaming
    ? message.content.replace(/<think>[\s\S]*?<\/think>/gi, "").trimStart()
    : message.content;
  const visibleContent = showParsedTeacher ? parsed.visible : streamingVisible;
  const hasThinkTag = /<think>[\s\S]*?<\/think>/i.test(message.content);
  const hasThinking = Boolean(parsed.thinking) || hasThinkTag;
  const streamingThinking = (message.streamingThinking || "").trim();
  const showStreamingThinking = isTeacher && Boolean(message.isStreaming && streamingThinking);
  const thinkingText = showStreamingThinking ? streamingThinking : parsed.thinking;
  const showThinkingPanel = isTeacher && Boolean(thinkingText);
  const canEdit = message.role === "student";
  const versionTotal = message.versions?.length ?? 1;
  const versionIndex = (message.currentVersion ?? 0) + 1;

  useEffect(() => {
    if (!editing) {
      setDraft(message.content);
    }
  }, [editing, message.content]);

  useEffect(() => {
    setThinkingAutoCollapsed(false);
    setShowThinking(Boolean(message.isStreaming));
  }, [message.id]);

  useEffect(() => {
    if (!isTeacher || !message.isStreaming) {
      return;
    }
    setShowThinking(true);
    setThinkingAutoCollapsed(false);
  }, [isTeacher, message.isStreaming]);

  useEffect(() => {
    if (!isTeacher || !message.isStreaming || !showStreamingThinking) {
      return;
    }
    if (!visibleContent.trim() || thinkingAutoCollapsed) {
      return;
    }
    setShowThinking(false);
    setThinkingAutoCollapsed(true);
  }, [
    isTeacher,
    message.isStreaming,
    showStreamingThinking,
    visibleContent,
    thinkingAutoCollapsed
  ]);

  const handleSave = (event: FormEvent) => {
    event.preventDefault();
    const value = draft.trim();
    if (!value) {
      return;
    }
    setEditing(false);
    onEditSave(value);
  };

  return (
    <div className={cn("mb-8 flex w-full", isTeacher ? "justify-start" : "justify-end")}>
      <div className={cn("group flex max-w-[85%] gap-4", !isTeacher && "flex-row-reverse")}>
        <div
          className={cn(
            "mt-1 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full border",
            isTeacher
              ? "border-app-info/40 bg-app-surface-alt/95 text-app-info shadow-[var(--app-shadow-avatar-teacher)]"
              : "border-app-accentAlt/50 bg-app-accent/20 text-app-accentAlt shadow-[var(--app-shadow-avatar-student)]"
          )}
        >
          {isTeacher ? <Bot size={18} /> : <User size={18} />}
        </div>

        <div className={cn("flex flex-col gap-1.5", !isTeacher && "items-end")}>
          <div className={cn("flex items-center gap-2 px-1", !isTeacher && "flex-row-reverse")}>
            <span className="font-mono text-xs tracking-wider text-app-muted/70">
              {isTeacher ? "TEACHER_AI" : "STUDENT"}
            </span>
            {versionTotal > 1 && (
              <div className="flex items-center gap-1 rounded border border-app-border/30 px-1.5 py-0.5 font-mono text-[10px] text-app-muted/80">
                <button
                  type="button"
                  onClick={onPrevVersion}
                  className="leading-none text-app-info/80 hover:text-app-info"
                  title="上一版本"
                >
                  {"<"}
                </button>
                <span>
                  {versionIndex}/{versionTotal}
                </span>
                <button
                  type="button"
                  onClick={onNextVersion}
                  className="leading-none text-app-info/80 hover:text-app-info"
                  title="下一版本"
                >
                  {">"}
                </button>
              </div>
            )}
            {canEdit && !editing && (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="rounded p-1 text-app-muted/70 opacity-0 transition-opacity hover:text-app-info group-hover:opacity-100"
                title="编辑消息"
              >
                <Pencil size={13} />
              </button>
            )}
            {isTeacher && (
              <button
                type="button"
                onClick={onRetry}
                disabled={!canRetry}
                className={cn(
                  "rounded p-1 transition-colors",
                  canRetry
                    ? "text-app-muted/80 hover:text-app-info"
                    : "cursor-not-allowed text-app-muted/35"
                )}
                title="重试回复"
              >
                <RefreshCw size={13} />
              </button>
            )}
          </div>

          <div
            className={cn(
              "relative rounded-2xl border px-6 py-4 text-sm leading-relaxed",
              isTeacher
                ? "rounded-tl-sm border-app-border/35 bg-app-surface/80 text-app-text shadow-[var(--app-shadow-teacher)] backdrop-blur-md"
                : "rounded-tr-sm border-app-accentAlt/30 bg-gradient-to-br from-app-userFrom/90 to-app-userTo/90 text-white shadow-[var(--app-shadow-student)] backdrop-blur-sm"
            )}
          >
            {isTeacher && (
              <div className="absolute bottom-3 left-0 top-3 w-[2px] rounded-l-full bg-gradient-to-b from-app-info/85 to-transparent" />
            )}

            {editing ? (
              <form onSubmit={handleSave} className="space-y-2">
                <textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  className="min-h-24 w-full resize-y rounded-md border border-app-border/30 bg-app-bg/60 p-2 text-sm text-white outline-none focus:border-app-info/70"
                />
                <div className="flex gap-2">
                  <button
                    type="submit"
                    className="rounded-md border border-app-info/40 bg-app-info/15 px-3 py-1 text-xs text-app-info hover:bg-app-info/25"
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setEditing(false);
                      setDraft(message.content);
                    }}
                    className="rounded-md border border-app-border/30 px-3 py-1 text-xs text-app-muted hover:border-app-border/50 hover:text-app-text"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <>
                {showThinkingPanel && (
                  <div className="mb-3">
                    <button
                      type="button"
                      onClick={() => setShowThinking((prev) => !prev)}
                      className="flex w-full items-center justify-between rounded-md border border-app-border/30 bg-app-bg/35 px-3 py-2 text-xs text-app-muted transition-colors hover:border-app-info/40 hover:text-app-info"
                    >
                      <span className="flex items-center gap-2 font-mono tracking-wider">
                        <Lightbulb size={14} className={showThinking ? "text-app-info" : "text-app-muted"} />
                        THINKING_PROCESS
                      </span>
                      <ChevronDown
                        size={14}
                        className={cn(
                          "transition-transform duration-300",
                          showThinking ? "rotate-180 text-app-info" : "rotate-0 text-app-muted"
                        )}
                      />
                    </button>
                    <div
                      className={cn(
                        "mt-2 grid transition-all duration-300 ease-in-out",
                        showThinking ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
                      )}
                    >
                      <div className="overflow-hidden">
                        <pre className="max-h-44 overflow-auto rounded-md border border-app-border/25 bg-app-bg/45 p-3 font-mono text-xs leading-loose tracking-wide text-app-muted whitespace-pre-wrap">
                          {thinkingText}
                        </pre>
                      </div>
                    </div>
                  </div>
                )}
                {!isTeacher && message.images && message.images.length > 0 && (
                  <div className="mb-3 flex flex-wrap gap-2">
                    {message.images.map((img, idx) => (
                      <img
                        key={`${img.name}_${idx}`}
                        src={img.dataUrl}
                        alt={img.name}
                        className="max-h-48 max-w-full rounded-md border border-white/30 object-contain"
                      />
                    ))}
                  </div>
                )}
                <div className="break-words">
                  <MessageContent content={visibleContent} className={isTeacher ? "text-app-text" : "text-white"} />
                </div>
                {isTeacher && message.toolCard && (
                  <AssessCard
                    tool={message.toolCard.tool}
                    toolCallId={message.toolCard.tool_call_id}
                    questions={message.toolCard.questions}
                    phaseId={message.toolCard.phase_id}
                    onSubmit={onToolSubmit}
                    submitted={message.toolCardSubmitted}
                    initialAnswers={message.toolAnswers}
                  />
                )}
                {devMode && (
                  <div className="mt-3">
                    <button
                      type="button"
                      onClick={() => setShowRaw((prev) => !prev)}
                      className="text-xs text-app-accentAlt/90 underline-offset-2 hover:underline"
                    >
                      {showRaw ? "隐藏 RAW" : isTeacher ? "查看 RAW" : "查看原文"}
                    </button>
                    {showRaw && (
                      <div className="mt-2 space-y-1">
                        <div className="font-mono text-[10px] uppercase tracking-widest text-app-accentAlt/80">
                          {isTeacher ? "RAW RESPONSE" : "RAW REQUEST PAYLOAD"}
                        </div>
                        <pre className="max-h-40 overflow-auto rounded-md border border-app-border/25 bg-app-bg/55 p-2 font-mono text-xs text-app-muted whitespace-pre-wrap">
                          {message.rawContent || message.content}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
