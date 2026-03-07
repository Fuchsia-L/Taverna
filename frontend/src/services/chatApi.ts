import { APP_CONFIG } from "@/config/app";
import { ToolInputEvent } from "@/types/chat";

export interface ChatRequestPayload {
  message: string;
  images?: string[];
  model?: string;
  conversation_id?: string | null;
  thinking?: boolean;
}

export interface ChatResponsePayload {
  reply: string;
  tool_input_required?: ToolInputEvent;
  plan_card?: { nodes: Array<{ id: string; title: string; objective: string; status?: "pending" | "active" | "completed" }>; mode: string };
}

export interface ChatApiResult {
  data: ChatResponsePayload;
  conversationId: string | null;
}

export interface DebugInfo {
  system_prompt: string;
  summary: string;
  history_length: number;
  token_estimate: number;
  model: string;
  conversation_id: string;
  teaching_plan?: {
    goal: string;
    current_phase: number | null;
    phases: {
      id: number;
      title: string;
      status: "pending" | "active" | "completed";
      summary?: string | null;
    }[];
  } | null;
  debug_turns: {
    turn_index: number;
    version_index: number;
    model: string;
    request_messages: { role: string; content: unknown }[];
    response_raw: unknown;
  }[];
}

export interface StreamDonePayload {
  fullContent: string;
  thinkingContent?: string;
  conversationId: string | null;
  paused?: boolean;
}

export interface StreamToolPayload {
  name: string;
  arguments?: string;
  result?: string;
}

export interface StreamRawPayload {
  kind?: string;
  data: unknown;
}

export interface ProjectNode {
  id: string;
  title: string;
  objective?: string;
  status?: "pending" | "active" | "completed";
  conversation_id?: string;
}

export interface ProjectSummary {
  id: string;
  title: string;
  description?: string | null;
  status: "planning" | "active" | "completed" | "paused";
  learning_path: { nodes: ProjectNode[] };
  conversation_count?: number;
}

export interface ConversationMeta {
  id: string;
  type: "planning" | "learning" | "review" | "standalone";
  title?: string | null;
  objective?: string | null;
  node_id?: string | null;
  status: "active" | "completed" | "abandoned";
}

export interface HistoryMessagePayload {
  role: "user" | "assistant" | "system";
  content: unknown;
}

export interface ConversationHistoryResponse {
  conversation_id: string;
  messages: HistoryMessagePayload[];
  tool_input_required?: ToolInputEvent;
}

const api = APP_CONFIG.apiBaseUrl;

function normalizeSession(payload: ChatRequestPayload): ChatRequestPayload {
  if (!payload.conversation_id) {
    return { ...payload, conversation_id: undefined };
  }
  return payload;
}

export async function sendMessage(payload: ChatRequestPayload): Promise<ChatApiResult> {
  return sendMessageWithOptions(payload);
}

export async function sendMessageWithOptions(
  payload: ChatRequestPayload,
  options?: { signal?: AbortSignal }
): Promise<ChatApiResult> {
  const response = await fetch(`${api}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(normalizeSession(payload)),
    signal: options?.signal
  });

  if (!response.ok) {
    throw new Error(`Chat API failed with status ${response.status}`);
  }

  return {
    data: (await response.json()) as ChatResponsePayload,
    conversationId: response.headers.get("X-Conversation-Id")
  };
}

export async function sendMessageStream(
  payload: ChatRequestPayload,
  onToken: (token: string) => void,
  onDone: (done: StreamDonePayload) => void,
  onError: (error: string) => void,
  onThinking?: (thinking: string) => void,
  onTool?: (tool: StreamToolPayload) => void,
  onToolInputRequired?: (event: ToolInputEvent) => void,
  onRaw?: (raw: StreamRawPayload) => void,
  options?: { signal?: AbortSignal }
): Promise<void> {
  const response = await fetch(`${api}/api/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(normalizeSession(payload)),
    signal: options?.signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`Stream API failed with status ${response.status}`);
  }

  const headerSessionId = response.headers.get("X-Conversation-Id");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullContent = "";
  let fullThinking = "";
  let doneReceived = false;
  let errorReceived = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";

    for (const event of events) {
      const line = event
        .split("\n")
        .find((item) => item.startsWith("data: "));

      if (!line) {
        continue;
      }

      const parsed = JSON.parse(line.slice(6)) as
        | { type: "token"; content: string }
        | { type: "thinking"; content: string }
        | { type: "tool"; name?: string; arguments?: string; result?: string }
        | { type: "raw"; kind?: string; data: unknown }
        | ({ type: "tool_input_required" } & ToolInputEvent)
        | { type: "done"; full_content: string; thinking_content?: string; conversation_id?: string }
        | { type: "error"; message: string; conversation_id?: string };

      if (parsed.type === "token") {
        fullContent += parsed.content;
        onToken(parsed.content);
      } else if (parsed.type === "thinking") {
        fullThinking += parsed.content;
        onThinking?.(parsed.content);
      } else if (parsed.type === "tool") {
        onTool?.({
          name: parsed.name || "unknown",
          arguments: parsed.arguments,
          result: parsed.result
        });
      } else if (parsed.type === "raw") {
        onRaw?.({
          kind: parsed.kind,
          data: parsed.data
        });
      } else if (parsed.type === "tool_input_required") {
        onToolInputRequired?.({
          tool: parsed.tool,
          tool_call_id: parsed.tool_call_id,
          questions: parsed.questions,
          phase_id: parsed.phase_id
        });
      } else if (parsed.type === "done") {
        doneReceived = true;
        onDone({
          fullContent: parsed.full_content,
          thinkingContent: parsed.thinking_content || fullThinking,
          conversationId: parsed.conversation_id || headerSessionId,
          paused: (parsed as { paused?: boolean }).paused
        });
      } else if (parsed.type === "error") {
        errorReceived = true;
        onError(parsed.message);
      }
    }
  }

  // Some providers/endpoints may close stream without an explicit "done" event.
  // Finalize with accumulated content so UI does not stay in uncertain streaming state.
  if (!doneReceived && !errorReceived) {
    onDone({
      fullContent,
      thinkingContent: fullThinking,
      conversationId: headerSessionId
    });
  }
}

export async function fetchDebugInfo(conversationId?: string | null): Promise<DebugInfo> {
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
  const response = await fetch(`${api}/api/chat/debug-info${query}`);
  if (!response.ok) {
    throw new Error(`Debug API failed with status ${response.status}`);
  }
  return (await response.json()) as DebugInfo;
}

export async function fetchConversationHistory(
  conversationId?: string | null
): Promise<ConversationHistoryResponse> {
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
  const response = await fetch(`${api}/api/chat/history${query}`);
  if (!response.ok) {
    throw new Error(`History API failed with status ${response.status}`);
  }
  return (await response.json()) as ConversationHistoryResponse;
}

export async function createProject(payload: {
  title?: string;
  description?: string;
}): Promise<{
  project: ProjectSummary;
  planning_conversation: ConversationMeta;
}> {
  const response = await fetch(`${api}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Create project failed with status ${response.status}`);
  }
  return (await response.json()) as {
    project: ProjectSummary;
    planning_conversation: ConversationMeta;
  };
}

export async function listProjects(): Promise<ProjectSummary[]> {
  const response = await fetch(`${api}/api/projects`);
  if (!response.ok) {
    throw new Error(`List projects failed with status ${response.status}`);
  }
  const data = (await response.json()) as { items: ProjectSummary[] };
  return data.items || [];
}

export async function getProjectConversations(projectId: string): Promise<ConversationMeta[]> {
  const response = await fetch(`${api}/api/projects/${encodeURIComponent(projectId)}/conversations`);
  if (!response.ok) {
    throw new Error(`List conversations failed with status ${response.status}`);
  }
  const data = (await response.json()) as { items: ConversationMeta[] };
  return data.items || [];
}

export async function listStandaloneConversations(): Promise<ConversationMeta[]> {
  const response = await fetch(`${api}/api/conversations`);
  if (!response.ok) {
    throw new Error(`List standalone conversations failed with status ${response.status}`);
  }
  const data = (await response.json()) as { items: ConversationMeta[] };
  return data.items || [];
}

export interface NavigationData {
  projects: ProjectSummary[];
  project_conversations: Record<string, ConversationMeta[]>;
  standalone_conversations: ConversationMeta[];
}

export async function fetchNavigation(): Promise<NavigationData> {
  const response = await fetch(`${api}/api/navigation`);
  if (!response.ok) {
    throw new Error(`Navigation API failed with status ${response.status}`);
  }
  return (await response.json()) as NavigationData;
}

export async function confirmProjectPlan(
  projectId: string,
  nodes: ProjectNode[]
): Promise<{ project_id: string; learning_path: { nodes: ProjectNode[] } }> {
  const response = await fetch(`${api}/api/projects/${encodeURIComponent(projectId)}/confirm-plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nodes }),
  });
  if (!response.ok) {
    throw new Error(`Confirm plan failed with status ${response.status}`);
  }
  return (await response.json()) as { project_id: string; learning_path: { nodes: ProjectNode[] } };
}

export async function createStandaloneConversation(payload?: {
  title?: string;
  objective?: string;
}): Promise<ConversationMeta> {
  const response = await fetch(`${api}/api/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  if (!response.ok) {
    throw new Error(`Create conversation failed with status ${response.status}`);
  }
  return (await response.json()) as ConversationMeta;
}

export async function endConversation(conversationId: string): Promise<void> {
  const response = await fetch(`${api}/api/conversations/${encodeURIComponent(conversationId)}/end`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`End conversation failed with status ${response.status}`);
  }
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await fetch(`${api}/api/conversations/${encodeURIComponent(conversationId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`Delete conversation failed with status ${response.status}`);
  }
}

export async function deleteProject(projectId: string): Promise<void> {
  const response = await fetch(`${api}/api/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`Delete project failed with status ${response.status}`);
  }
}

export async function retryLastReply(
  conversationId: string,
  model?: string,
  options?: { signal?: AbortSignal; thinking?: boolean }
): Promise<ChatApiResult> {
  const response = await fetch(`${api}/api/chat/retry`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      conversation_id: conversationId,
      model,
      thinking: options?.thinking ?? false,
    }),
    signal: options?.signal
  });

  if (!response.ok) {
    throw new Error(`Retry API failed with status ${response.status}`);
  }

  return {
    data: (await response.json()) as ChatResponsePayload,
    conversationId: response.headers.get("X-Conversation-Id")
  };
}

export async function retryLastReplyStream(
  conversationId: string,
  model: string | undefined,
  onToken: (token: string) => void,
  onDone: (done: StreamDonePayload) => void,
  onError: (error: string) => void,
  onThinking?: (thinking: string) => void,
  onTool?: (tool: StreamToolPayload) => void,
  onToolInputRequired?: (event: ToolInputEvent) => void,
  options?: { signal?: AbortSignal; thinking?: boolean }
): Promise<void> {
  const response = await fetch(`${api}/api/chat/retry/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      conversation_id: conversationId,
      model,
      thinking: options?.thinking ?? false,
    }),
    signal: options?.signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`Retry stream API failed with status ${response.status}`);
  }

  const headerSessionId = response.headers.get("X-Conversation-Id");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullContent = "";
  let fullThinking = "";
  let doneReceived = false;
  let errorReceived = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";

    for (const event of events) {
      const line = event.split("\n").find((item) => item.startsWith("data: "));
      if (!line) {
        continue;
      }

      const parsed = JSON.parse(line.slice(6)) as
        | { type: "token"; content: string }
        | { type: "thinking"; content: string }
        | { type: "tool"; name?: string; arguments?: string; result?: string }
        | ({ type: "tool_input_required" } & ToolInputEvent)
        | { type: "done"; full_content: string; thinking_content?: string; conversation_id?: string }
        | { type: "error"; message: string; conversation_id?: string };

      if (parsed.type === "token") {
        fullContent += parsed.content;
        onToken(parsed.content);
      } else if (parsed.type === "thinking") {
        fullThinking += parsed.content;
        onThinking?.(parsed.content);
      } else if (parsed.type === "tool") {
        onTool?.({
          name: parsed.name || "unknown",
          arguments: parsed.arguments,
          result: parsed.result
        });
      } else if (parsed.type === "tool_input_required") {
        onToolInputRequired?.({
          tool: parsed.tool,
          tool_call_id: parsed.tool_call_id,
          questions: parsed.questions,
          phase_id: parsed.phase_id
        });
      } else if (parsed.type === "done") {
        doneReceived = true;
        onDone({
          fullContent: parsed.full_content,
          thinkingContent: parsed.thinking_content || fullThinking,
          conversationId: parsed.conversation_id || headerSessionId,
          paused: (parsed as { paused?: boolean }).paused
        });
      } else if (parsed.type === "error") {
        errorReceived = true;
        onError(parsed.message);
      }
    }
  }

  if (!doneReceived && !errorReceived) {
    onDone({
      fullContent,
      thinkingContent: fullThinking,
      conversationId: headerSessionId
    });
  }
}

export interface RewindPayload {
  conversation_id: string;
  target_user_turn: number;
  replacement_message: string;
  images?: string[];
  model?: string;
  thinking?: boolean;
}

export async function rewindAndResend(
  payload: RewindPayload,
  options?: { signal?: AbortSignal }
): Promise<ChatApiResult> {
  const response = await fetch(`${api}/api/chat/rewind`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload),
    signal: options?.signal
  });

  if (!response.ok) {
    throw new Error(`Rewind API failed with status ${response.status}`);
  }

  return {
    data: (await response.json()) as ChatResponsePayload,
    conversationId: response.headers.get("X-Conversation-Id")
  };
}

export async function rewindAndResendStream(
  payload: RewindPayload,
  onToken: (token: string) => void,
  onDone: (done: StreamDonePayload) => void,
  onError: (error: string) => void,
  onThinking?: (thinking: string) => void,
  onTool?: (tool: StreamToolPayload) => void,
  onToolInputRequired?: (event: ToolInputEvent) => void,
  options?: { signal?: AbortSignal }
): Promise<void> {
  const response = await fetch(`${api}/api/chat/rewind/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload),
    signal: options?.signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`Rewind stream API failed with status ${response.status}`);
  }

  const headerSessionId = response.headers.get("X-Conversation-Id");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullContent = "";
  let fullThinking = "";
  let doneReceived = false;
  let errorReceived = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";

    for (const event of events) {
      const line = event.split("\n").find((item) => item.startsWith("data: "));
      if (!line) {
        continue;
      }

      const parsed = JSON.parse(line.slice(6)) as
        | { type: "token"; content: string }
        | { type: "thinking"; content: string }
        | { type: "tool"; name?: string; arguments?: string; result?: string }
        | ({ type: "tool_input_required" } & ToolInputEvent)
        | { type: "done"; full_content: string; thinking_content?: string; conversation_id?: string }
        | { type: "error"; message: string; conversation_id?: string };

      if (parsed.type === "token") {
        fullContent += parsed.content;
        onToken(parsed.content);
      } else if (parsed.type === "thinking") {
        fullThinking += parsed.content;
        onThinking?.(parsed.content);
      } else if (parsed.type === "tool") {
        onTool?.({
          name: parsed.name || "unknown",
          arguments: parsed.arguments,
          result: parsed.result
        });
      } else if (parsed.type === "tool_input_required") {
        onToolInputRequired?.({
          tool: parsed.tool,
          tool_call_id: parsed.tool_call_id,
          questions: parsed.questions,
          phase_id: parsed.phase_id
        });
      } else if (parsed.type === "done") {
        doneReceived = true;
        onDone({
          fullContent: parsed.full_content,
          thinkingContent: parsed.thinking_content || fullThinking,
          conversationId: parsed.conversation_id || headerSessionId,
          paused: (parsed as { paused?: boolean }).paused
        });
      } else if (parsed.type === "error") {
        errorReceived = true;
        onError(parsed.message);
      }
    }
  }

  if (!doneReceived && !errorReceived) {
    onDone({
      fullContent,
      thinkingContent: fullThinking,
      conversationId: headerSessionId
    });
  }
}

export async function postChatMessage(payload: ChatRequestPayload): Promise<ChatResponsePayload> {
  const result = await sendMessage(payload);
  return result.data;
}

export async function submitToolResponse(
  toolCallId: string,
  answers: Array<{ question: string; answer: string }>,
  model: string | undefined,
  conversationId: string,
  options?: { signal?: AbortSignal; thinking?: boolean }
): Promise<ChatApiResult> {
  const response = await fetch(`${api}/api/chat/tool-response`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tool_call_id: toolCallId,
      answers,
      model,
      conversation_id: conversationId,
      thinking: options?.thinking ?? false,
    }),
    signal: options?.signal,
  });
  if (!response.ok) {
    throw new Error(`Tool response API failed with status ${response.status}`);
  }
  return {
    data: (await response.json()) as ChatResponsePayload,
    conversationId: response.headers.get("X-Conversation-Id"),
  };
}

export async function submitToolResponseStream(
  toolCallId: string,
  answers: Array<{ question: string; answer: string }>,
  model: string | undefined,
  conversationId: string,
  onToken: (token: string) => void,
  onDone: (done: StreamDonePayload) => void,
  onToolInput: (event: ToolInputEvent) => void,
  onError: (error: string) => void,
  onThinking?: (thinking: string) => void,
  onTool?: (tool: StreamToolPayload) => void,
  options?: { signal?: AbortSignal; thinking?: boolean }
): Promise<void> {
  const response = await fetch(`${api}/api/chat/tool-response/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tool_call_id: toolCallId,
      answers,
      model,
      conversation_id: conversationId,
      thinking: options?.thinking ?? false,
    }),
    signal: options?.signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`Tool response stream API failed with status ${response.status}`);
  }
  const headerSessionId = response.headers.get("X-Conversation-Id");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullContent = "";
  let fullThinking = "";
  let doneReceived = false;
  let errorReceived = false;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const event of events) {
      const line = event.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      const parsed = JSON.parse(line.slice(6)) as
        | { type: "token"; content: string }
        | { type: "thinking"; content: string }
        | { type: "tool"; name?: string; arguments?: string; result?: string }
        | ({ type: "tool_input_required" } & ToolInputEvent)
        | { type: "done"; full_content: string; thinking_content?: string; conversation_id?: string; paused?: boolean }
        | { type: "error"; message: string; conversation_id?: string };
      if (parsed.type === "token") {
        fullContent += parsed.content;
        onToken(parsed.content);
      } else if (parsed.type === "thinking") {
        fullThinking += parsed.content;
        onThinking?.(parsed.content);
      } else if (parsed.type === "tool") {
        onTool?.({
          name: parsed.name || "unknown",
          arguments: parsed.arguments,
          result: parsed.result,
        });
      } else if (parsed.type === "tool_input_required") {
        onToolInput({
          tool: parsed.tool,
          tool_call_id: parsed.tool_call_id,
          questions: parsed.questions,
          phase_id: parsed.phase_id,
        });
      } else if (parsed.type === "done") {
        doneReceived = true;
        onDone({
          fullContent: parsed.full_content,
          thinkingContent: parsed.thinking_content || fullThinking,
          conversationId: parsed.conversation_id || headerSessionId,
          paused: parsed.paused,
        });
      } else if (parsed.type === "error") {
        errorReceived = true;
        onError(parsed.message);
      }
    }
  }
  if (!doneReceived && !errorReceived) {
    onDone({
      fullContent,
      thinkingContent: fullThinking,
      conversationId: headerSessionId,
    });
  }
}

