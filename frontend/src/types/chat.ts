export type ChatRole = "teacher" | "student";

export interface AttachedImage {
  dataUrl: string;
  mimeType: string;
  name: string;
  sizeBytes: number;
}

export interface ToolInputEvent {
  tool: "assess" | "quiz";
  tool_call_id: string;
  questions: Array<{
    question: string;
    purpose?: string;
    expected_concept?: string;
  }>;
  phase_id?: number;
}

export interface PlanCardNode {
  id: string;
  title: string;
  objective: string;
  status?: "pending" | "active" | "completed";
}

export interface PlanCardData {
  nodes: PlanCardNode[];
  projectId: string;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  images?: AttachedImage[];
  isStreaming?: boolean;
  streamingThinking?: string;
  turnIndex?: number;
  rawContent?: string;
  versions?: string[];
  rawVersions?: (string | undefined)[];
  currentVersion?: number;
  toolCard?: ToolInputEvent;
  toolCardSubmitted?: boolean;
  toolAnswers?: Array<{ question: string; answer: string }>;
  planCard?: PlanCardData;
  planCardConfirmed?: boolean;
}
