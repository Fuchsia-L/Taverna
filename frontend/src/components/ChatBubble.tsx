import { cn } from "@/lib/utils";
import { APP_CONFIG } from "@/config/app";

export type Role = "teacher" | "student";

interface ChatBubbleProps {
  role: Role;
  content: string;
}

export function ChatBubble({ role, content }: ChatBubbleProps) {
  const isTeacher = role === "teacher";

  return (
    <div className={cn("flex w-full", isTeacher ? "justify-start" : "justify-end")}>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl border px-4 py-3 text-sm leading-relaxed backdrop-blur-sm md:max-w-[78%]",
          isTeacher
            ? "border-app-border/45 bg-app-surface/95 text-app-text shadow-neon"
            : "border-app-accentAlt/55 bg-gradient-to-br from-app-userFrom to-app-userTo text-white shadow-neon"
        )}
      >
        <div className="mb-1 text-xs opacity-80">
          {isTeacher ? APP_CONFIG.roleLabel.teacher : APP_CONFIG.roleLabel.student}
        </div>
        <div>{content}</div>
      </div>
    </div>
  );
}
