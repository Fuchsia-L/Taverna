import { useEffect, useMemo, useState } from "react";

interface AssessCardProps {
  tool: "assess" | "quiz";
  toolCallId: string;
  questions: Array<{
    question: string;
    purpose?: string;
    expected_concept?: string;
  }>;
  phaseId?: number;
  onSubmit: (toolCallId: string, answers: Array<{ question: string; answer: string }>) => void;
  submitted?: boolean;
  initialAnswers?: Array<{ question: string; answer: string }>;
}

export function AssessCard({
  tool,
  toolCallId,
  questions,
  phaseId,
  onSubmit,
  submitted = false,
  initialAnswers = [],
}: AssessCardProps) {
  const safeQuestions = Array.isArray(questions) ? questions : [];
  const [answers, setAnswers] = useState<Array<{ question: string; answer: string }>>(
    initialAnswers.length
      ? initialAnswers
      : safeQuestions.map((q) => ({ question: q.question, answer: "" }))
  );

  useEffect(() => {
    if (submitted) {
      return;
    }
    setAnswers((prev) => {
      if (prev.length === safeQuestions.length) {
        return prev;
      }
      return safeQuestions.map((q, idx) => ({
        question: q.question,
        answer: prev[idx]?.answer || "",
      }));
    });
  }, [safeQuestions, submitted]);

  const title = useMemo(() => {
    if (tool === "quiz") {
      return phaseId != null ? `📝 阶段${phaseId}检测` : "📝 阶段检测";
    }
    return "📋 诊断问题";
  }, [phaseId, tool]);

  const canSubmit = useMemo(() => {
    if (submitted || safeQuestions.length === 0) {
      return false;
    }
    return answers.length === safeQuestions.length && answers.every((item) => item.answer.trim().length > 0);
  }, [answers, safeQuestions.length, submitted]);

  return (
    <div className="mt-3 rounded-xl border border-app-info/35 bg-app-bg/35 p-3">
      <div className="mb-3 text-sm font-medium text-app-info">{title}</div>
      <div className="space-y-3">
        {safeQuestions.map((q, index) => (
          <div key={`${toolCallId}_${index}`} className="space-y-1">
            <div className="text-sm text-app-text">{index + 1}. {q.question}</div>
            <textarea
              value={answers[index]?.answer || ""}
              disabled={submitted}
              onChange={(event) =>
                setAnswers((prev) =>
                  prev.map((item, i) => (i === index ? { ...item, answer: event.target.value } : item))
                )
              }
              className="w-full min-h-20 resize-y rounded-md border border-app-border/35 bg-app-surface/60 px-2 py-1.5 text-sm text-app-text outline-none focus:border-app-info/60 disabled:opacity-65"
              placeholder="请输入你的答案..."
            />
          </div>
        ))}
      </div>
      <div className="mt-3 flex justify-end">
        <button
          type="button"
          disabled={!canSubmit}
          onClick={() => onSubmit(toolCallId, answers)}
          className="rounded border border-app-info/40 bg-app-info/15 px-3 py-1.5 text-xs text-app-info hover:bg-app-info/25 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitted ? "已提交" : "确认提交"}
        </button>
      </div>
    </div>
  );
}
