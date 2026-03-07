import { useCallback, useEffect, useRef, useState } from "react";
import { ConversationManager } from "@/components/ConversationManager";
import { DebugPanel } from "@/components/DebugPanel";
import { SettingsPanel } from "@/components/SettingsPanel";
import { TeachingPlanCard } from "@/components/TeachingPlanCard";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ChatMessageItem } from "@/components/chat/ChatMessageItem";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import {
  ConversationMeta,
  createProject,
  createStandaloneConversation,
  DebugInfo,
  fetchConversationHistory,
  fetchDebugInfo,
  fetchNavigation,
  ProjectSummary,
  retryLastReply,
  retryLastReplyStream,
  rewindAndResendStream,
  rewindAndResend,
  StreamRawPayload,
  submitToolResponse,
  submitToolResponseStream,
  sendMessageWithOptions,
  sendMessageStream,
  confirmProjectPlan,
  deleteConversation,
  deleteProject
} from "@/services/chatApi";
import { useSettings } from "@/stores/settingsStore";
import { APP_CONFIG } from "@/config/app";
import { AttachedImage, ChatMessage, PlanCardNode, ToolInputEvent } from "@/types/chat";

let messageIdCounter = 0;

function buildId() {
  messageIdCounter += 1;
  return `msg_${messageIdCounter}_${Date.now()}`;
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

type StreamThinkState = {
  inThink: boolean;
  pending: string;
};

type LiveRawState = {
  initial?: unknown;
  chunks: unknown[];
  toolRounds: unknown[];
  final?: unknown;
};

const PLAN_NOTICE_FINGERPRINT_KEY = "taverna_plan_notice_fingerprint_v1";

function applyLiveRawEvent(prev: LiveRawState | null, event: StreamRawPayload): LiveRawState {
  const next: LiveRawState = prev || { chunks: [], toolRounds: [] };
  const kind = event.kind || "misc";
  if (kind === "initial") {
    return { ...next, initial: event.data };
  }
  if (kind === "chunk") {
    return { ...next, chunks: [...next.chunks, event.data] };
  }
  if (kind === "tool_round") {
    return { ...next, toolRounds: [...next.toolRounds, event.data] };
  }
  if (kind === "final") {
    return { ...next, final: event.data };
  }
  return {
    ...next,
    chunks: [...next.chunks, { kind, data: event.data }],
  };
}

function buildPlanFingerprint(conversationId: string | null, plan: DebugInfo["teaching_plan"] | null): string | null {
  if (!conversationId || !plan) {
    return null;
  }
  const phaseCount = Array.isArray(plan.phases) ? plan.phases.length : 0;
  return `${conversationId}::${phaseCount}::${plan.current_phase ?? "none"}`;
}

function parseMimeFromDataUrl(dataUrl: string): string {
  const match = /^data:([^;]+);base64,/i.exec(dataUrl);
  return match?.[1] || "image/*";
}

function extractTextAndImages(content: unknown): { text: string; images?: AttachedImage[] } {
  if (typeof content === "string") {
    return { text: content };
  }
  if (!Array.isArray(content)) {
    return { text: String(content ?? "") };
  }

  const texts: string[] = [];
  const images: AttachedImage[] = [];
  let imageIndex = 1;
  for (const part of content) {
    if (!part || typeof part !== "object") {
      continue;
    }
    const item = part as Record<string, unknown>;
    const type = String(item.type || "");
    if (type === "text" || type === "input_text") {
      const text = typeof item.text === "string" ? item.text : "";
      if (text) {
        texts.push(text);
      }
      continue;
    }
    if (type === "image_url") {
      const image = item.image_url as { url?: string } | undefined;
      const dataUrl = typeof image?.url === "string" ? image.url : "";
      if (!dataUrl) {
        continue;
      }
      images.push({
        dataUrl,
        mimeType: parseMimeFromDataUrl(dataUrl),
        name: `image-${imageIndex}`,
        sizeBytes: dataUrl.length,
      });
      imageIndex += 1;
    }
  }
  return {
    text: texts.join("\n").trim(),
    images: images.length > 0 ? images : undefined,
  };
}

function buildMessagesFromHistory(
  history: Array<{ role: "user" | "assistant" | "system"; content: unknown }>
): ChatMessage[] {
  const items: ChatMessage[] = [];
  let turn = 0;

  for (const msg of history) {
    if (msg.role === "system") {
      continue;
    }
    if (msg.role === "user") {
      turn += 1;
      const parsed = extractTextAndImages(msg.content);
      items.push({
        id: buildId(),
        role: "student",
        content: parsed.text,
        images: parsed.images,
        turnIndex: turn,
        versions: [parsed.text],
        rawVersions: [undefined],
        currentVersion: 0,
      });
      continue;
    }
    const text = typeof msg.content === "string" ? msg.content : String(msg.content ?? "");
    items.push({
      id: buildId(),
      role: "teacher",
      content: text,
      turnIndex: turn > 0 ? turn : undefined,
      versions: [text],
      rawVersions: [undefined],
      currentVersion: 0,
    });
  }

  return items;
}

function normalizeToolInputEvent(event: ToolInputEvent): ToolInputEvent {
  const parseQuestionsText = (text: string): unknown => {
    const trimmed = text.trim();
    if (!trimmed) {
      return [];
    }

    let probe: unknown = trimmed;
    for (let i = 0; i < 3; i += 1) {
      if (typeof probe !== "string") {
        break;
      }
      try {
        probe = JSON.parse(probe.trim());
      } catch {
        break;
      }
    }
    if (typeof probe !== "string") {
      return probe;
    }

    const listMatch = probe.match(/\[[\s\S]*\]/);
    if (listMatch) {
      try {
        return JSON.parse(listMatch[0]);
      } catch {
        return [];
      }
    }
    return [];
  };

  const parseQuestions = (raw: unknown): Array<Record<string, unknown>> => {
    if (Array.isArray(raw)) {
      return raw.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object");
    }
    if (typeof raw === "string") {
      return parseQuestions(parseQuestionsText(raw));
    }
    if (raw && typeof raw === "object") {
      return [raw as Record<string, unknown>];
    }
    return [];
  };

  const rawQuestions = parseQuestions((event as { questions?: unknown }).questions);
  return {
    ...event,
    questions: rawQuestions.map((q, index) => {
      const item = q;
      return {
        question: String(item?.question ?? `问题${index + 1}`),
        purpose: typeof item?.purpose === "string" ? item.purpose : undefined,
        expected_concept: typeof item?.expected_concept === "string" ? item.expected_concept : undefined,
      };
    }),
  };
}

function pendingTagPrefixLength(text: string, tag: string): number {
  const max = Math.min(text.length, tag.length - 1);
  for (let size = max; size > 0; size -= 1) {
    if (text.endsWith(tag.slice(0, size))) {
      return size;
    }
  }
  return 0;
}

function consumeStreamChunk(
  state: StreamThinkState,
  chunk: string
): { next: StreamThinkState; visibleDelta: string; thinkingDelta: string } {
  const openTag = "<think>";
  const closeTag = "</think>";
  const text = `${state.pending}${chunk}`;
  let index = 0;
  let visibleDelta = "";
  let thinkingDelta = "";
  let inThink = state.inThink;

  while (index < text.length) {
    if (inThink) {
      const closeAt = text.indexOf(closeTag, index);
      if (closeAt === -1) {
        break;
      }
      thinkingDelta += text.slice(index, closeAt);
      index = closeAt + closeTag.length;
      inThink = false;
      continue;
    }

    const openAt = text.indexOf(openTag, index);
    if (openAt === -1) {
      break;
    }
    visibleDelta += text.slice(index, openAt);
    index = openAt + openTag.length;
    inThink = true;
  }

  const rest = text.slice(index);
  const tagToTrack = inThink ? closeTag : openTag;
  const keepSize = pendingTagPrefixLength(rest, tagToTrack);
  const flush = rest.slice(0, rest.length - keepSize);
  const pending = rest.slice(rest.length - keepSize);

  if (inThink) {
    thinkingDelta += flush;
  } else {
    visibleDelta += flush;
  }

  return {
    next: { inThink, pending },
    visibleDelta,
    thinkingDelta,
  };
}

function finalizeStreamingMessage(msg: ChatMessage): ChatMessage {
  if (!msg.isStreaming) {
    return msg;
  }
  const thinking = (msg.streamingThinking || "").trim();
  const hasThinkTag = /<think>[\s\S]*?<\/think>/i.test(msg.content);
  const mergedContent = thinking && !hasThinkTag ? `<think>${thinking}</think>\n${msg.content}` : msg.content;
  // Preserve previous versions instead of resetting to a single-element array
  const prevVersions = msg.versions && msg.versions.length > 1 ? msg.versions : [];
  const versions = prevVersions.length > 0
    ? [...prevVersions.slice(0, -1), mergedContent]
    : [mergedContent];
  return {
    ...msg,
    content: mergedContent,
    isStreaming: false,
    streamingThinking: undefined,
    versions,
    currentVersion: versions.length - 1
  };
}

function countUserTurns(items: ChatMessage[]) {
  return items.filter((msg) => msg.role === "student").length;
}

function createVersionedMessage(
  role: ChatMessage["role"],
  content: string,
  turnIndex?: number,
  rawContent?: string,
  isStreaming?: boolean
): ChatMessage {
  return {
    id: buildId(),
    role,
    content,
    turnIndex,
    rawContent,
    isStreaming,
    versions: [content],
    rawVersions: [rawContent],
    currentVersion: 0
  };
}

function appendVersionToTurn(
  items: ChatMessage[],
  role: ChatMessage["role"],
  turnIndex: number,
  content: string,
  rawContent?: string
): ChatMessage[] {
  let matched = false;
  const updated = items.map((msg) => {
    if (msg.role !== role || msg.turnIndex !== turnIndex) {
      return msg;
    }
    matched = true;
    const versions = [...(msg.versions || [msg.content]), content];
    const rawVersions = [...(msg.rawVersions || [msg.rawContent]), rawContent];
    const currentVersion = versions.length - 1;
    return {
      ...msg,
      versions,
      rawVersions,
      currentVersion,
      content: versions[currentVersion],
      rawContent: rawVersions[currentVersion]
    };
  });
  if (!matched) {
    return [...updated, createVersionedMessage(role, content, turnIndex, rawContent)];
  }
  return updated;
}

function cycleMessageVersion(items: ChatMessage[], messageId: string, direction: -1 | 1): ChatMessage[] {
  return items.map((msg) => {
    if (msg.id !== messageId) {
      return msg;
    }
    const versions = msg.versions || [msg.content];
    const rawVersions = msg.rawVersions || [msg.rawContent];
    if (versions.length <= 1) {
      return msg;
    }
    const current = msg.currentVersion ?? versions.length - 1;
    const next = (current + direction + versions.length) % versions.length;
    return {
      ...msg,
      currentVersion: next,
      content: versions[next],
      rawContent: rawVersions[next]
    };
  });
}

function applyDebugTurns(items: ChatMessage[], info: DebugInfo): ChatMessage[] {
  const byTurn = new Map<number, DebugInfo["debug_turns"]>();
  for (const turn of info.debug_turns || []) {
    const arr = byTurn.get(turn.turn_index) || [];
    arr.push(turn);
    byTurn.set(turn.turn_index, arr);
  }
  for (const turns of byTurn.values()) {
    turns.sort((a, b) => a.version_index - b.version_index);
  }

  return items.map((msg) => {
    if (!msg.turnIndex) {
      return msg;
    }
    const turns = byTurn.get(msg.turnIndex);
    if (!turns || turns.length === 0) {
      return msg;
    }

    const versions = msg.versions || [msg.content];
    const rawVersions = msg.rawVersions || new Array(versions.length).fill(undefined);
    const currentVersion = msg.currentVersion ?? versions.length - 1;
    for (let i = 0; i < versions.length; i += 1) {
      const turn = turns[i] || turns[turns.length - 1];
      if (!turn) {
        continue;
      }
      if (msg.role === "student") {
        rawVersions[i] = JSON.stringify(
          {
            model: turn.model,
            messages: turn.request_messages
          },
          null,
          2
        );
      } else {
        rawVersions[i] =
          turn.response_raw && typeof turn.response_raw === "string"
            ? turn.response_raw
            : turn.response_raw
              ? JSON.stringify(turn.response_raw, null, 2)
              : rawVersions[i] || msg.content;
      }
    }

    const nextRawContent = rawVersions[currentVersion] || msg.rawContent;

    if (msg.role === "student") {
      return {
        ...msg,
        rawContent: nextRawContent,
        rawVersions
      };
    }

    return {
      ...msg,
      rawContent: nextRawContent,
      rawVersions
    };
  });
}

export function ChatPage() {
  const { settings, setConversationId } = useSettings();
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [debugInfo, setDebugInfo] = useState<DebugInfo | null>(null);
  const [liveRaw, setLiveRaw] = useState<LiveRawState | null>(null);
  const [toolEvents, setToolEvents] = useState<Array<{ id: string; text: string }>>([]);
  const [teachingPlan, setTeachingPlan] = useState<DebugInfo["teaching_plan"]>(null);
  const [showPlanPanel, setShowPlanPanel] = useState(false);
  const [showConversationManager, setShowConversationManager] = useState(false);
  const [showPlanNotice, setShowPlanNotice] = useState(false);
  const [planBuildState, setPlanBuildState] = useState<"idle" | "building" | "done">("idle");
  const [submittingToolAnswer, setSubmittingToolAnswer] = useState(false);
  const [pendingImages, setPendingImages] = useState<AttachedImage[]>([]);
  const [bootstrapLoading, setBootstrapLoading] = useState(true);
  const [bootstrapProgress, setBootstrapProgress] = useState(4);
  const [bootstrapStatus, setBootstrapStatus] = useState("正在连接后端...");
  const [bootstrapHint, setBootstrapHint] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [standaloneConversations, setStandaloneConversations] = useState<ConversationMeta[]>([]);
  const [projectConversations, setProjectConversations] = useState<Record<string, ConversationMeta[]>>({});
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const messageEndRef = useRef<HTMLDivElement>(null);
  const activeRequestRef = useRef<AbortController | null>(null);
  const handledToolSignalsRef = useRef<Set<string>>(new Set());
  const previousPlanExistsRef = useRef(false);
  const planBuildStateRef = useRef<"idle" | "building" | "done">("idle");
  const loadConversationRequestRef = useRef(0);
  const sendingRef = useRef(false);
  const latestConversationIdRef = useRef<string | null>(settings.conversationId);
  const bootstrapCompletedRef = useRef(false);
  const bootstrapRetryAttemptRef = useRef(0);
  const commitConversationId = useCallback(
    (conversationId: string | null) => {
      latestConversationIdRef.current = conversationId;
      setConversationId(conversationId);
    },
    [setConversationId]
  );

  const clearPlanNoticeFingerprint = useCallback(() => {
    try {
      sessionStorage.removeItem(PLAN_NOTICE_FINGERPRINT_KEY);
    } catch {
      // ignore storage errors
    }
  }, []);

  const maybeShowPlanNotice = useCallback((conversationId: string | null, plan: DebugInfo["teaching_plan"] | null) => {
    const fingerprint = buildPlanFingerprint(conversationId, plan);
    if (!fingerprint) {
      return;
    }
    try {
      const shownFingerprint = sessionStorage.getItem(PLAN_NOTICE_FINGERPRINT_KEY);
      if (shownFingerprint === fingerprint) {
        return;
      }
      sessionStorage.setItem(PLAN_NOTICE_FINGERPRINT_KEY, fingerprint);
    } catch {
      // ignore storage errors; still show notice
    }
    setShowPlanNotice(true);
  }, []);

  const pushToolEvent = useCallback((name: string) => {
    const id = buildId();
    setToolEvents((prev) => [...prev, { id, text: `工具调用：${name}` }]);
    if (name === "plan") {
      planBuildStateRef.current = "building";
      setPlanBuildState("building");
    }
    setTimeout(() => {
      setToolEvents((prev) => prev.filter((item) => item.id !== id));
    }, 4500);
  }, []);

  const addToolCard = useCallback((event: ToolInputEvent) => {
    const normalized = normalizeToolInputEvent(event);
    setMessages((prev) => [
      ...prev,
      {
        id: buildId(),
        role: "teacher",
        content: "",
        toolCard: normalized,
        toolCardSubmitted: false,
        versions: [""],
        rawVersions: [undefined],
        currentVersion: 0,
      },
    ]);
  }, []);

  const addPlanCard = useCallback((nodes: PlanCardNode[], projectId: string) => {
    setMessages((prev) => [
      ...prev,
      {
        id: buildId(),
        role: "teacher" as const,
        content: "",
        planCard: { nodes, projectId },
        planCardConfirmed: false,
      },
    ]);
  }, []);

  // Keep scroll position stable during conversation; no auto-follow.

  useEffect(() => {
    document.documentElement.dataset.theme = APP_CONFIG.theme.defaultTheme;
  }, []);

  const loadConversationMessages = useCallback(
    async (
      conversationId: string | null,
      options?: { throwOnError?: boolean }
    ) => {
      const requestId = ++loadConversationRequestRef.current;
      if (!conversationId) {
        if (requestId !== loadConversationRequestRef.current) {
          return;
        }
        setMessages([]);
        return;
      }
      try {
        const history = await fetchConversationHistory(conversationId);
        if (requestId !== loadConversationRequestRef.current) {
          return;
        }
        const baseMessages = buildMessagesFromHistory(history.messages || []);
        if (history.tool_input_required) {
          const normalized = normalizeToolInputEvent(history.tool_input_required);
          baseMessages.push({
            id: buildId(),
            role: "teacher",
            content: "",
            toolCard: normalized,
            toolCardSubmitted: false,
            versions: [""],
            rawVersions: [undefined],
            currentVersion: 0,
          });
        }
        setMessages(baseMessages);
      } catch (error) {
        console.error(error);
        if (options?.throwOnError) {
          throw error;
        }
      }
    },
    []
  );

  useEffect(() => {
    sendingRef.current = sending;
  }, [sending]);

  useEffect(() => {
    latestConversationIdRef.current = settings.conversationId;
  }, [settings.conversationId]);

  useEffect(() => {
    planBuildStateRef.current = planBuildState;
  }, [planBuildState]);

  const refreshNavigation = useCallback(async (reason: "bootstrap" | "manual" = "manual") => {
    const isBootstrap = reason === "bootstrap" && !bootstrapCompletedRef.current;
    const startConversationId = latestConversationIdRef.current;
    try {
      if (isBootstrap) {
        setBootstrapLoading(true);
        setBootstrapHint(null);
        setBootstrapStatus("正在加载项目...");
        setBootstrapProgress(12);
      }
      const nav = await fetchNavigation();
      setProjects(nav.projects);
      setProjectConversations(nav.project_conversations);
      setStandaloneConversations(nav.standalone_conversations);
      if (isBootstrap) {
        setBootstrapStatus("正在加载历史消息...");
        setBootstrapProgress(72);
      }

      const standalone = nav.standalone_conversations;
      const currentConversationId = latestConversationIdRef.current;
      let targetConversationId = currentConversationId;
      if (!startConversationId && !currentConversationId && standalone.length > 0) {
        commitConversationId(standalone[0].id);
        targetConversationId = standalone[0].id;
      }
      if (isBootstrap && targetConversationId) {
        setBootstrapStatus("正在加载历史消息...");
        setBootstrapProgress(84);
        prevLoadedConversationRef.current = targetConversationId;
        await loadConversationMessages(targetConversationId, { throwOnError: true });
      }
      if (isBootstrap) {
        setBootstrapProgress(100);
        setBootstrapStatus("加载完成");
        bootstrapCompletedRef.current = true;
        bootstrapRetryAttemptRef.current = 0;
        setBootstrapLoading(false);
      }
      return true;
    } catch (error) {
      console.error(error);
      if (isBootstrap) {
        setBootstrapLoading(true);
        setBootstrapStatus("等待服务就绪...");
        setBootstrapHint("后端尚未完成启动，正在自动重试");
        setBootstrapProgress((prev) => Math.max(8, Math.min(prev, 24)));
      }
      return false;
    }
  }, [commitConversationId, loadConversationMessages]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;

    const bootstrap = async () => {
      if (cancelled || bootstrapCompletedRef.current) {
        return;
      }
      const ok = await refreshNavigation("bootstrap");
      if (cancelled || bootstrapCompletedRef.current || ok) {
        return;
      }
      const attempt = bootstrapRetryAttemptRef.current;
      const delay = Math.min(6000, 1000 * Math.pow(2, attempt));
      bootstrapRetryAttemptRef.current = attempt + 1;
      timer = window.setTimeout(() => {
        void bootstrap();
      }, delay);
    };

    void bootstrap();

    return () => {
      cancelled = true;
      if (timer !== null) {
        window.clearTimeout(timer);
      }
    };
  }, [refreshNavigation]);

  const applyPlanTransition = useCallback(
    async (session: string | null, info: DebugInfo) => {
      const nextPlan = info.teaching_plan || null;
      const hasPlanNow = Boolean(nextPlan);
      const hadPlanBefore = previousPlanExistsRef.current;
      const conversationId = info.conversation_id || session;
      const shouldFinalizePlan =
        hasPlanNow && (!hadPlanBefore || planBuildStateRef.current === "building");

      setTeachingPlan(nextPlan);

      if (shouldFinalizePlan) {
        maybeShowPlanNotice(conversationId, nextPlan);
        planBuildStateRef.current = "done";
        setPlanBuildState("done");
        await refreshNavigation();
      }

      previousPlanExistsRef.current = hasPlanNow;
    },
    [maybeShowPlanNotice, refreshNavigation]
  );

  const prevLoadedConversationRef = useRef<string | null>(null);
  useEffect(() => {
    if (!settings.conversationId) {
      return;
    }
    // Skip if bootstrap already loaded this conversation
    if (prevLoadedConversationRef.current === settings.conversationId) {
      return;
    }
    prevLoadedConversationRef.current = settings.conversationId;
    setShowPlanNotice(false);
    void loadConversationMessages(settings.conversationId);
  }, [loadConversationMessages, settings.conversationId]);

  const refreshDebugInfo = useCallback(
    async (session: string | null) => {
      if (!settings.devMode) {
        return;
      }
      try {
        const info = await fetchDebugInfo(session);
        setDebugInfo(info);
        setTeachingPlan(info.teaching_plan || null);
        previousPlanExistsRef.current = Boolean(info.teaching_plan);
        setMessages((prev) => applyDebugTurns(prev, info));
      } catch (error) {
        console.error(error);
      }
    },
    [settings.devMode]
  );

  const checkLatestTurnSignals = useCallback(
    async (session: string | null) => {
      if (!session) {
        return;
      }
      try {
        const info = await fetchDebugInfo(session);
        await applyPlanTransition(session, info);

        const turns = info.debug_turns || [];
        if (!turns.length) {
          return;
        }
        const latest = turns[turns.length - 1];
        const signalKey = `${info.conversation_id}:${latest.turn_index}:${latest.version_index}`;
        if (handledToolSignalsRef.current.has(signalKey)) {
          return;
        }
        handledToolSignalsRef.current.add(signalKey);

        const raw = latest.response_raw as Record<string, unknown> | undefined;
        const toolRounds = Array.isArray(raw?.tool_rounds) ? raw.tool_rounds : [];
        for (const round of toolRounds as Array<Record<string, unknown>>) {
          const calls = Array.isArray(round.tool_calls) ? round.tool_calls : [];
          for (const call of calls as Array<Record<string, unknown>>) {
            const name = String(call.name || "unknown");
            pushToolEvent(name);
          }
        }
      } catch {
        // ignore signal fetch errors
      }
    },
    [applyPlanTransition, pushToolEvent]
  );

  useEffect(() => {
    if (planBuildState !== "building" || !settings.conversationId) {
      return;
    }

    let cancelled = false;
    const poll = async () => {
      try {
        const info = await fetchDebugInfo(settings.conversationId);
        if (cancelled) {
          return;
        }
        await applyPlanTransition(settings.conversationId, info);
      } catch {
        // ignore polling errors
      }
    };

    void poll();
    const timer = window.setInterval(() => {
      void poll();
    }, 700);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [applyPlanTransition, planBuildState, settings.conversationId]);

  useEffect(() => {
    if (!settings.devMode) {
      return;
    }
    void refreshDebugInfo(settings.conversationId);
  }, [refreshDebugInfo, settings.devMode, settings.conversationId]);

  useEffect(() => {
    if (planBuildState !== "done") {
      return;
    }
    const timer = window.setTimeout(() => {
      planBuildStateRef.current = "idle";
      setPlanBuildState("idle");
    }, 2800);
    return () => window.clearTimeout(timer);
  }, [planBuildState]);

  useEffect(() => {
    if (!showPlanNotice) {
      return;
    }
    const timer = window.setTimeout(() => setShowPlanNotice(false), 8000);
    return () => window.clearTimeout(timer);
  }, [showPlanNotice]);

  const findProjectIdByConversation = useCallback(
    (conversationId: string | null): string | null => {
      if (!conversationId) return null;
      for (const [projectId, convs] of Object.entries(projectConversations)) {
        if (convs.some((c) => c.id === conversationId)) return projectId;
      }
      return null;
    },
    [projectConversations]
  );

  const handleToolEvent = useCallback(
    (tool: { name: string; arguments?: string; result?: string }) => {
      pushToolEvent(tool.name);
      if (tool.name === "plan" && tool.result) {
        try {
          const parsed = JSON.parse(tool.result);
          if (parsed.mode === "project" && Array.isArray(parsed.learning_path_draft)) {
            const projectId = findProjectIdByConversation(
              latestConversationIdRef.current || settings.conversationId
            );
            if (projectId) {
              addPlanCard(parsed.learning_path_draft, projectId);
            }
          }
        } catch { /* ignore parse errors */ }
      }
    },
    [pushToolEvent, findProjectIdByConversation, settings.conversationId, addPlanCard]
  );

  const handlePlanConfirm = useCallback(
    async (projectId: string, nodes: PlanCardNode[]) => {
      await confirmProjectPlan(projectId, nodes);
      setMessages((prev) =>
        prev.map((msg) =>
          msg.planCard?.projectId === projectId
            ? { ...msg, planCardConfirmed: true }
            : msg
        )
      );
      await refreshNavigation();
    },
    [refreshNavigation]
  );

  const handleSend = useCallback(
    async (overrideText?: string) => {
      const text = (overrideText ?? input).trim();
      if ((!text && pendingImages.length === 0) || sendingRef.current) {
        return;
      }
      setLiveRaw(null);
      const imagesToSend = pendingImages;

      const userMessage: ChatMessage = {
        ...createVersionedMessage("student", text, countUserTurns(messages) + 1),
        images: imagesToSend.length > 0 ? imagesToSend : undefined,
      };

      if (!overrideText) {
        setInput("");
      }
      setPendingImages([]);

      sendingRef.current = true;
      setSending(true);
      const controller = new AbortController();
      activeRequestRef.current = controller;

      if (settings.streaming) {
        const assistantMessage = createVersionedMessage("teacher", "", userMessage.turnIndex, undefined, true);
        const assistantId = assistantMessage.id;
        let latestSessionId = settings.conversationId;
        let streamThinking = "";
        let streamThinkState: StreamThinkState = { inThink: false, pending: "" };
        setMessages((prev) => [
          ...prev,
          userMessage,
          assistantMessage
        ]);
        try {
          await sendMessageStream(
            {
              message: text,
              images: imagesToSend.map((item) => item.dataUrl),
              model: settings.model,
              conversation_id: settings.conversationId,
              thinking: settings.thinking
            },
            (token) => {
              const consumed = consumeStreamChunk(streamThinkState, token);
              streamThinkState = consumed.next;
              if (consumed.thinkingDelta) {
                streamThinking += consumed.thinkingDelta;
              }
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId && msg.role === "teacher"
                    ? {
                        ...msg,
                        content: `${msg.content}${consumed.visibleDelta}`,
                        streamingThinking: consumed.thinkingDelta
                          ? `${msg.streamingThinking || ""}${consumed.thinkingDelta}`
                          : msg.streamingThinking
                      }
                    : msg
                )
              );
            },
            ({ fullContent, thinkingContent, conversationId, paused }) => {
              if (conversationId) {
                commitConversationId(conversationId);
                latestSessionId = conversationId;
              }
              const finalThinking = (thinkingContent || streamThinking).trim();
              const withThinking =
                finalThinking && !/<think>[\s\S]*?<\/think>/i.test(fullContent)
                  ? `<think>${finalThinking}</think>\n${fullContent}`
                  : fullContent;
              // Remove empty placeholder when stream paused for a tool card
              if (paused && !withThinking.trim()) {
                setMessages((prev) => prev.filter((msg) => msg.id !== assistantId));
                return;
              }
              setMessages((prev) =>
                prev.map((msg) => {
                  if (msg.id !== assistantId) {
                    return msg;
                  }
                  return {
                    ...msg,
                    content: withThinking,
                    isStreaming: false,
                    streamingThinking: undefined,
                    versions: [withThinking],
                    currentVersion: 0
                  };
                })
              );
            },
            (error) => {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId && msg.role === "teacher"
                    ? {
                        ...msg,
                        content: error || msg.content || APP_CONFIG.text.requestFailed,
                        isStreaming: false,
                        streamingThinking: undefined,
                        versions: [error || msg.content || APP_CONFIG.text.requestFailed],
                        currentVersion: 0
                      }
                    : msg
                )
              );
            },
            (thinking) => {
              streamThinking += thinking;
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId && msg.role === "teacher"
                    ? { ...msg, streamingThinking: `${msg.streamingThinking || ""}${thinking}` }
                    : msg
                )
              );
            },
            handleToolEvent,
            (event) => {
              addToolCard(event);
            },
            (rawEvent) => {
              setLiveRaw((prev) => applyLiveRawEvent(prev, rawEvent));
            },
            { signal: controller.signal }
          );
        } catch (error) {
          if (isAbortError(error)) {
            setMessages((prev) => prev.map((msg) => finalizeStreamingMessage(msg)));
          } else {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId && msg.role === "teacher"
                  ? { ...msg, isStreaming: false, streamingThinking: undefined }
                  : msg
              )
            );
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId && msg.role === "teacher"
                  ? {
                      ...msg,
                      content: msg.content || APP_CONFIG.text.requestFailed,
                      versions: [msg.content || APP_CONFIG.text.requestFailed],
                      currentVersion: 0
                    }
                    : msg
              )
            );
            console.error(error);
          }
        } finally {
          sendingRef.current = false;
          setSending(false);
          if (activeRequestRef.current === controller) {
            activeRequestRef.current = null;
          }
          await refreshDebugInfo(latestSessionId);
          await checkLatestTurnSignals(latestSessionId);
        }
        return;
      }

      setMessages((prev) => [...prev, userMessage]);
      let latestSessionId = settings.conversationId;
      try {
        const result = await sendMessageWithOptions(
          {
            message: text,
            images: imagesToSend.map((item) => item.dataUrl),
            model: settings.model,
            conversation_id: settings.conversationId
          },
          { signal: controller.signal }
        );
        latestSessionId = result.conversationId || settings.conversationId;
        if (result.conversationId) {
          commitConversationId(result.conversationId);
        }
        setMessages((prev) => [
          ...prev,
          createVersionedMessage("teacher", result.data.reply, userMessage.turnIndex)
        ]);
        if (result.data.tool_input_required) {
          addToolCard(result.data.tool_input_required);
        }
        if (result.data.plan_card?.mode === "project" && result.data.plan_card.nodes) {
          const pid = findProjectIdByConversation(latestSessionId);
          if (pid) addPlanCard(result.data.plan_card.nodes, pid);
        }
      } catch (error) {
        if (!isAbortError(error)) {
          setMessages((prev) => [
            ...prev,
            {
              id: buildId(),
              role: "teacher",
              content: APP_CONFIG.text.requestFailed
            }
          ]);
          console.error(error);
        }
      } finally {
        sendingRef.current = false;
        setSending(false);
        if (activeRequestRef.current === controller) {
          activeRequestRef.current = null;
        }
        await refreshDebugInfo(latestSessionId);
        await checkLatestTurnSignals(latestSessionId);
      }
    },
    [addPlanCard, checkLatestTurnSignals, commitConversationId, findProjectIdByConversation, input, pendingImages, handleToolEvent, refreshDebugInfo, settings.model, settings.conversationId, settings.streaming]
  );

  const getUserTurnByMessageId = useCallback(
    (messageId: string) => {
      let turn = 0;
      for (const msg of messages) {
        if (msg.role === "student") {
          turn += 1;
        }
        if (msg.id === messageId) {
          return msg.role === "student" ? turn : -1;
        }
      }
      return -1;
    },
    [messages]
  );

  const handleRetry = useCallback(async () => {
    if (!settings.conversationId || sending) {
      return;
    }

    const lastTeacher = [...messages]
      .reverse()
      .find((msg) => msg.role === "teacher" && !msg.toolCard);
    if (!lastTeacher) {
      return;
    }

    clearPlanNoticeFingerprint();
    setSending(true);
    const controller = new AbortController();
    activeRequestRef.current = controller;

    if (settings.streaming) {
        let latestSessionId = settings.conversationId;
        let streamThinking = "";
        let streamThinkState: StreamThinkState = { inThink: false, pending: "" };
        let startedVisibleOutput = false;
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === lastTeacher.id && msg.role === "teacher"
              ? { ...msg, isStreaming: true, streamingThinking: "", content: "", versions: msg.versions }
              : msg
          )
      );

      try {
        await retryLastReplyStream(
          settings.conversationId,
          settings.model,
          (token) => {
            const consumed = consumeStreamChunk(streamThinkState, token);
            streamThinkState = consumed.next;
            if (consumed.thinkingDelta) {
              streamThinking += consumed.thinkingDelta;
            }
            const hasVisible = consumed.visibleDelta.length > 0;
            const hadVisible = startedVisibleOutput;
            if (hasVisible) {
              startedVisibleOutput = true;
            }
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === lastTeacher.id && msg.role === "teacher"
                  ? {
                      ...msg,
                      content: hasVisible
                        ? (hadVisible ? `${msg.content}${consumed.visibleDelta}` : consumed.visibleDelta)
                        : msg.content,
                      streamingThinking: consumed.thinkingDelta
                        ? `${msg.streamingThinking || ""}${consumed.thinkingDelta}`
                        : msg.streamingThinking
                    }
                  : msg
              )
            );
          },
          ({ fullContent, thinkingContent, conversationId, paused }) => {
            if (conversationId) {
              commitConversationId(conversationId);
              latestSessionId = conversationId;
            }
            const finalThinking = (thinkingContent || streamThinking).trim();
            const withThinking =
              finalThinking && !/<think>[\s\S]*?<\/think>/i.test(fullContent)
                ? `<think>${finalThinking}</think>\n${fullContent}`
                : fullContent;
            if (paused && !withThinking.trim()) {
              // Restore the message from its previous version instead of removing it
              setMessages((prev) => prev.map((msg) => {
                if (msg.id !== lastTeacher.id || msg.role !== "teacher") return msg;
                const versions = msg.versions || [];
                const idx = msg.currentVersion ?? (versions.length - 1);
                return { ...msg, isStreaming: false, streamingThinking: undefined, content: versions[idx] ?? "" };
              }));
              return;
            }
            setMessages((prev) => {
              const target = prev.find((msg) => msg.id === lastTeacher.id);
              const cleared = prev.map((msg) =>
                msg.id === lastTeacher.id && msg.role === "teacher"
                  ? { ...msg, isStreaming: false, streamingThinking: undefined }
                  : msg
              );
              if (!target || target.turnIndex == null) {
                return cleared.map((msg) =>
                  msg.id === lastTeacher.id && msg.role === "teacher"
                    ? { ...msg, content: withThinking }
                    : msg
                );
              }
              return appendVersionToTurn(cleared, "teacher", target.turnIndex, withThinking);
            });
          },
          (error) => {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === lastTeacher.id
                  && msg.role === "teacher"
                  ? {
                      ...msg,
                      isStreaming: false,
                      streamingThinking: undefined,
                      content: error || msg.content || APP_CONFIG.text.requestFailed
                    }
                  : msg
              )
            );
          },
          (thinking) => {
            streamThinking += thinking;
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === lastTeacher.id
                  && msg.role === "teacher"
                  ? { ...msg, streamingThinking: `${msg.streamingThinking || ""}${thinking}` }
                  : msg
              )
            );
          },
          handleToolEvent,
          (event) => {
            addToolCard(event);
          },
          { signal: controller.signal, thinking: settings.thinking }
        );
      } catch (error) {
        if (isAbortError(error)) {
          setMessages((prev) => prev.map((msg) => finalizeStreamingMessage(msg)));
          return;
        }
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === lastTeacher.id
              && msg.role === "teacher"
              ? { ...msg, isStreaming: false, streamingThinking: undefined, content: APP_CONFIG.text.requestFailed }
              : msg
          )
        );
        console.error(error);
      } finally {
        setSending(false);
        if (activeRequestRef.current === controller) {
          activeRequestRef.current = null;
        }
        await refreshDebugInfo(latestSessionId);
        await checkLatestTurnSignals(latestSessionId);
      }
      return;
    }

    try {
      const result = await retryLastReply(settings.conversationId, settings.model, { signal: controller.signal, thinking: settings.thinking });
      const latestSessionId = result.conversationId || settings.conversationId;
      if (result.conversationId) {
        commitConversationId(result.conversationId);
      }
      setMessages((prev) => {
        const target = prev.find((msg) => msg.id === lastTeacher.id);
        if (!target || target.turnIndex == null) {
          return prev.map((msg) =>
            msg.id === lastTeacher.id && msg.role === "teacher"
              ? { ...msg, content: result.data.reply }
              : msg
          );
        }
        return appendVersionToTurn(prev, "teacher", target.turnIndex, result.data.reply);
      });
      if (result.data.tool_input_required) {
        addToolCard(result.data.tool_input_required);
      }
      if (result.data.plan_card?.mode === "project" && result.data.plan_card.nodes) {
        const pid = findProjectIdByConversation(latestSessionId);
        if (pid) addPlanCard(result.data.plan_card.nodes, pid);
      }
      await refreshDebugInfo(latestSessionId);
      await checkLatestTurnSignals(latestSessionId);
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      setMessages((prev) => {
        const target = prev.find((msg) => msg.id === lastTeacher.id);
        if (!target || target.turnIndex == null) {
          return prev.map((msg) =>
            msg.id === lastTeacher.id && msg.role === "teacher"
              ? { ...msg, content: APP_CONFIG.text.requestFailed }
              : msg
          );
        }
        return appendVersionToTurn(prev, "teacher", target.turnIndex, APP_CONFIG.text.requestFailed);
      });
      console.error(error);
    } finally {
      setSending(false);
      if (activeRequestRef.current === controller) {
        activeRequestRef.current = null;
      }
    }
  }, [addPlanCard, checkLatestTurnSignals, clearPlanNoticeFingerprint, commitConversationId, findProjectIdByConversation, messages, handleToolEvent, refreshDebugInfo, sending, settings.model, settings.conversationId, settings.streaming]);

  const handleEditSave = useCallback(
    async (messageId: string, value: string) => {
      if (!settings.conversationId || sending) {
        return;
      }

      const targetTurn = getUserTurnByMessageId(messageId);
      if (targetTurn < 1) {
        return;
      }

      clearPlanNoticeFingerprint();

      const originalMsg = messages.find((msg) => msg.id === messageId);
      const originalImageUrls = originalMsg?.images?.map((img) => img.dataUrl) ?? [];

      setSending(true);
      const controller = new AbortController();
      activeRequestRef.current = controller;
      setMessages((prev) => prev.filter((msg) => (msg.turnIndex ?? Number.MAX_SAFE_INTEGER) <= targetTurn));

      if (settings.streaming) {
        let latestSessionId = settings.conversationId;
        let streamThinking = "";
        let streamThinkState: StreamThinkState = { inThink: false, pending: "" };
        let targetTeacherHadVisible = false;
        let targetTeacherId: string | null = null;
        setMessages((prev) =>
          {
            const withUserVersion = appendVersionToTurn(prev, "student", targetTurn, value);
            const existingTeacher = withUserVersion.find(
              (msg) => msg.role === "teacher" && msg.turnIndex === targetTurn
            );
            if (existingTeacher) {
              targetTeacherId = existingTeacher.id;
              return withUserVersion.map((msg) =>
                msg.id === existingTeacher.id
                  ? { ...msg, isStreaming: true, streamingThinking: "" }
                  : msg
              );
            }
            const placeholder = createVersionedMessage("teacher", "", targetTurn, undefined, true);
            targetTeacherId = placeholder.id;
            return [...withUserVersion, { ...placeholder, streamingThinking: "" }];
          }
        );

        try {
          await rewindAndResendStream(
            {
              conversation_id: settings.conversationId,
              target_user_turn: targetTurn,
              replacement_message: value,
              images: originalImageUrls.length > 0 ? originalImageUrls : undefined,
              model: settings.model,
              thinking: settings.thinking,
            },
            (token) => {
              const consumed = consumeStreamChunk(streamThinkState, token);
              streamThinkState = consumed.next;
              if (consumed.thinkingDelta) {
                streamThinking += consumed.thinkingDelta;
              }
              const hasVisible = consumed.visibleDelta.length > 0;
              const hadVisible = targetTeacherHadVisible;
              if (hasVisible) {
                targetTeacherHadVisible = true;
              }
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === targetTeacherId && msg.role === "teacher"
                    ? {
                        ...msg,
                        content: hasVisible
                          ? (hadVisible ? `${msg.content}${consumed.visibleDelta}` : consumed.visibleDelta)
                          : msg.content,
                        isStreaming: true,
                        streamingThinking: consumed.thinkingDelta
                          ? `${msg.streamingThinking || ""}${consumed.thinkingDelta}`
                          : msg.streamingThinking
                      }
                    : msg
                )
              );
            },
            ({ fullContent, thinkingContent, conversationId, paused }) => {
              if (conversationId) {
                commitConversationId(conversationId);
                latestSessionId = conversationId;
              }
              const finalThinking = (thinkingContent || streamThinking).trim();
              const withThinking =
                finalThinking && !/<think>[\s\S]*?<\/think>/i.test(fullContent)
                  ? `<think>${finalThinking}</think>\n${fullContent}`
                  : fullContent;
              if (paused && !withThinking.trim()) {
                setMessages((prev) => prev.filter((msg) => msg.id !== targetTeacherId));
                return;
              }
              setMessages((prev) =>
                appendVersionToTurn(prev, "teacher", targetTurn, withThinking).map((msg) =>
                  msg.id === targetTeacherId && msg.role === "teacher"
                    ? { ...msg, isStreaming: false, streamingThinking: undefined }
                    : msg
                )
              );
            },
            (error) => {
              setMessages((prev) =>
                appendVersionToTurn(prev, "teacher", targetTurn, error || APP_CONFIG.text.requestFailed).map((msg) =>
                  msg.id === targetTeacherId && msg.role === "teacher"
                    ? { ...msg, isStreaming: false, streamingThinking: undefined }
                    : msg
                )
              );
            },
            (thinking) => {
              streamThinking += thinking;
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === targetTeacherId && msg.role === "teacher"
                    ? { ...msg, streamingThinking: `${msg.streamingThinking || ""}${thinking}` }
                    : msg
                )
              );
            },
          handleToolEvent,
          (event) => {
            addToolCard(event);
          },
          { signal: controller.signal }
          );
          await refreshDebugInfo(latestSessionId);
          await checkLatestTurnSignals(latestSessionId);
        } catch (error) {
          if (isAbortError(error)) {
            setMessages((prev) => prev.map((msg) => finalizeStreamingMessage(msg)));
          } else {
            setMessages((prev) => appendVersionToTurn(prev, "teacher", targetTurn, APP_CONFIG.text.requestFailed));
            console.error(error);
          }
        } finally {
          setSending(false);
          if (activeRequestRef.current === controller) {
            activeRequestRef.current = null;
          }
        }
        return;
      }

      try {
        const result = await rewindAndResend({
          conversation_id: settings.conversationId,
          target_user_turn: targetTurn,
          replacement_message: value,
          images: originalImageUrls.length > 0 ? originalImageUrls : undefined,
          model: settings.model,
          thinking: settings.thinking,
        }, { signal: controller.signal });
        const latestSessionId = result.conversationId || settings.conversationId;

        if (result.conversationId) {
          commitConversationId(result.conversationId);
        }

        setMessages((prev) => {
          let next = appendVersionToTurn(prev, "student", targetTurn, value);
          next = appendVersionToTurn(next, "teacher", targetTurn, result.data.reply);
          return next;
        });
        if (result.data.tool_input_required) {
          addToolCard(result.data.tool_input_required);
        }
        if (result.data.plan_card?.mode === "project" && result.data.plan_card.nodes) {
          const pid = findProjectIdByConversation(latestSessionId);
          if (pid) addPlanCard(result.data.plan_card.nodes, pid);
        }
        await refreshDebugInfo(latestSessionId);
        await checkLatestTurnSignals(latestSessionId);
      } catch (error) {
        if (isAbortError(error)) {
          return;
        }
        setMessages((prev) => appendVersionToTurn(prev, "teacher", targetTurn, APP_CONFIG.text.requestFailed));
        console.error(error);
      } finally {
        setSending(false);
        if (activeRequestRef.current === controller) {
          activeRequestRef.current = null;
        }
      }
    },
    [addPlanCard, checkLatestTurnSignals, clearPlanNoticeFingerprint, commitConversationId, findProjectIdByConversation, getUserTurnByMessageId, messages, handleToolEvent, refreshDebugInfo, sending, settings.model, settings.conversationId, settings.streaming]
  );

  const handleToolSubmit = useCallback(
    async (toolCallId: string, answers: Array<{ question: string; answer: string }>) => {
      if (!settings.conversationId || submittingToolAnswer) {
        return;
      }
      setSubmittingToolAnswer(true);
      const controller = new AbortController();
      activeRequestRef.current = controller;
      setMessages((prev) =>
        prev.map((msg) =>
          msg.toolCard?.tool_call_id === toolCallId
            ? { ...msg, toolCardSubmitted: true, toolAnswers: answers }
            : msg
        )
      );

      try {
        if (settings.streaming) {
          const assistantMessage = createVersionedMessage("teacher", "", undefined, undefined, true);
          const assistantId = assistantMessage.id;
          let latestSessionId = settings.conversationId;
          let streamThinking = "";
          let streamThinkState: StreamThinkState = { inThink: false, pending: "" };
          setMessages((prev) => [
            ...prev,
            assistantMessage,
          ]);
          await submitToolResponseStream(
            toolCallId,
            answers,
            settings.model,
            settings.conversationId,
            (token) => {
              const consumed = consumeStreamChunk(streamThinkState, token);
              streamThinkState = consumed.next;
              if (consumed.thinkingDelta) {
                streamThinking += consumed.thinkingDelta;
              }
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: `${m.content}${consumed.visibleDelta}`,
                        streamingThinking: consumed.thinkingDelta
                          ? `${m.streamingThinking || ""}${consumed.thinkingDelta}`
                          : m.streamingThinking
                      }
                    : m
                )
              );
            },
            ({ fullContent, thinkingContent, conversationId, paused }) => {
              if (conversationId) {
                commitConversationId(conversationId);
                latestSessionId = conversationId;
              }
              const finalThinking = (thinkingContent || streamThinking).trim();
              const withThinking =
                finalThinking && !/<think>[\s\S]*?<\/think>/i.test(fullContent)
                  ? `<think>${finalThinking}</think>\n${fullContent}`
                  : fullContent;
              if (paused && !withThinking.trim()) {
                setMessages((prev) => prev.filter((m) => m.id !== assistantId));
                return;
              }
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: withThinking, isStreaming: false, streamingThinking: undefined, versions: [withThinking], currentVersion: 0 }
                    : m
                )
              );
            },
            (event) => {
              addToolCard(event);
            },
            (error) => {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: error || APP_CONFIG.text.requestFailed,
                        isStreaming: false,
                        streamingThinking: undefined,
                        versions: [error || APP_CONFIG.text.requestFailed],
                        currentVersion: 0
                      }
                    : m
                )
              );
            },
            (thinking) => {
              streamThinking += thinking;
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, streamingThinking: `${m.streamingThinking || ""}${thinking}` } : m
                )
              );
            },
            handleToolEvent,
            { signal: controller.signal, thinking: settings.thinking }
          );
          await refreshDebugInfo(latestSessionId);
          await checkLatestTurnSignals(latestSessionId);
        } else {
          const result = await submitToolResponse(
            toolCallId,
            answers,
            settings.model,
            settings.conversationId,
            { signal: controller.signal, thinking: settings.thinking }
          );
          if (result.conversationId) {
            commitConversationId(result.conversationId);
          }
          if (result.data.tool_input_required) {
            addToolCard(result.data.tool_input_required);
          } else {
            setMessages((prev) => [...prev, createVersionedMessage("teacher", result.data.reply)]);
          }
          if (result.data.plan_card?.mode === "project" && result.data.plan_card.nodes) {
            const pid = findProjectIdByConversation(result.conversationId || settings.conversationId);
            if (pid) addPlanCard(result.data.plan_card.nodes, pid);
          }
          await refreshDebugInfo(result.conversationId || settings.conversationId);
          await checkLatestTurnSignals(result.conversationId || settings.conversationId);
        }
      } catch (error) {
        if (isAbortError(error)) {
          setMessages((prev) => prev.map((msg) => finalizeStreamingMessage(msg)));
          return;
        }
        console.error(error);
      } finally {
        setSubmittingToolAnswer(false);
        if (activeRequestRef.current === controller) {
          activeRequestRef.current = null;
        }
      }
    },
    [
      addPlanCard,
      addToolCard,
      checkLatestTurnSignals,
      findProjectIdByConversation,
      handleToolEvent,
      refreshDebugInfo,
      commitConversationId,
      settings.model,
      settings.conversationId,
      settings.streaming,
      submittingToolAnswer,
    ]
  );

  const [creating, setCreating] = useState(false);
  const creatingRef = useRef(false);

  const handleCreateProject = useCallback(async () => {
    if (creatingRef.current) return;
    creatingRef.current = true;
    setCreating(true);
    try {
      const created = await createProject({});
      prevLoadedConversationRef.current = created.planning_conversation.id;
      commitConversationId(created.planning_conversation.id);
      setMessages([]);
      setDebugInfo(null);
      setTeachingPlan(null);
      await refreshNavigation();
    } catch (error) {
      console.error(error);
    } finally {
      creatingRef.current = false;
      setCreating(false);
    }
  }, [commitConversationId, refreshNavigation]);

  const handleCreateStandaloneConversation = useCallback(async () => {
    if (creatingRef.current) return;
    creatingRef.current = true;
    setCreating(true);
    try {
      const created = await createStandaloneConversation({ title: "独立会话" });
      prevLoadedConversationRef.current = created.id;
      commitConversationId(created.id);
      setMessages([]);
      setDebugInfo(null);
      setTeachingPlan(null);
      await refreshNavigation();
    } catch (error) {
      console.error(error);
    } finally {
      creatingRef.current = false;
      setCreating(false);
    }
  }, [commitConversationId, refreshNavigation]);

  const handleDeleteConversation = useCallback(async (conversationId: string) => {
    await deleteConversation(conversationId);
    if (settings.conversationId === conversationId) {
      prevLoadedConversationRef.current = null;
      commitConversationId(null);
      setMessages([]);
      setDebugInfo(null);
      setTeachingPlan(null);
    }
    await refreshNavigation();
  }, [settings.conversationId, commitConversationId, refreshNavigation]);

  const handleDeleteProject = useCallback(async (projectId: string) => {
    const projectConvIds = (projectConversations[projectId] || []).map((c) => c.id);
    await deleteProject(projectId);
    if (settings.conversationId && projectConvIds.includes(settings.conversationId)) {
      prevLoadedConversationRef.current = null;
      commitConversationId(null);
      setMessages([]);
      setDebugInfo(null);
      setTeachingPlan(null);
    }
    await refreshNavigation();
  }, [settings.conversationId, projectConversations, commitConversationId, refreshNavigation]);

  const handleSelectConversation = useCallback(
    async (conversationId: string) => {
      if (settings.conversationId === conversationId) {
        return;
      }
      prevLoadedConversationRef.current = conversationId;
      commitConversationId(conversationId);
      await loadConversationMessages(conversationId);
      setDebugInfo(null);
      await refreshDebugInfo(conversationId);
    },
    [commitConversationId, loadConversationMessages, refreshDebugInfo, settings.conversationId]
  );

  const lastAssistantId = [...messages]
    .reverse()
    .find((msg) => msg.role === "teacher" && !msg.toolCard)?.id ?? null;
  const handlePrevVersion = useCallback((messageId: string) => {
    setMessages((prev) => cycleMessageVersion(prev, messageId, -1));
  }, []);
  const handleNextVersion = useCallback((messageId: string) => {
    setMessages((prev) => cycleMessageVersion(prev, messageId, 1));
  }, []);
  const handleAbort = useCallback(() => {
    activeRequestRef.current?.abort();
    setMessages((prev) => prev.map((msg) => finalizeStreamingMessage(msg)));
    sendingRef.current = false;
    setSending(false);
    setSubmittingToolAnswer(false);
    setLiveRaw(null);
  }, []);

  return (
    <main className="group relative flex h-screen w-full overflow-hidden bg-app-bg text-app-text selection:bg-app-accentAlt selection:text-white">
      <div className="pointer-events-none absolute inset-0 bg-app-vignette" />

      <ChatSidebar
        onOpenSettings={() => setShowSettings(true)}
        onOpenConversationManager={() => setShowConversationManager(true)}
        projects={projects}
        standaloneConversations={standaloneConversations}
        activeConversationId={settings.conversationId}
        projectConversations={projectConversations}
        onSelectConversation={handleSelectConversation}
        onCreateProject={handleCreateProject}
        onCreateConversation={handleCreateStandaloneConversation}
        creating={creating}
      />

      <section className="relative z-10 flex flex-1 flex-col overflow-hidden">
        {bootstrapLoading && (
          <div className="pointer-events-none absolute inset-x-0 top-0 z-[60]">
            <div className="h-1 w-full bg-app-border/30">
              <div
                className="h-full bg-app-accent transition-[width] duration-300 ease-out"
                style={{ width: `${Math.max(6, Math.min(100, bootstrapProgress))}%` }}
              />
            </div>
            <div className="border-b border-app-border/25 bg-app-surface/80 px-3 py-1 text-[11px] text-app-muted backdrop-blur-sm md:px-6">
              {bootstrapStatus}
              {bootstrapHint ? ` · ${bootstrapHint}` : ""}
            </div>
          </div>
        )}
        {planBuildState !== "idle" && (
          <div className="absolute left-1/2 top-4 z-40 -translate-x-1/2">
            <div className="rounded-full border border-app-info/35 bg-app-surface/90 px-4 py-1.5 text-xs text-app-info shadow-[var(--app-shadow-teacher)] backdrop-blur-md">
              {planBuildState === "building" ? "计划定制中..." : "计划已完成 ✓"}
            </div>
          </div>
        )}
        {toolEvents.length > 0 && (
          <div className="pointer-events-none absolute right-6 top-4 z-40 flex max-w-xs flex-col gap-2">
            {toolEvents.map((event) => (
              <div
                key={event.id}
                className="rounded-lg border border-app-info/35 bg-app-surface/90 px-3 py-2 text-xs text-app-info shadow-[var(--app-shadow-teacher)] backdrop-blur-md"
              >
                {event.text}
              </div>
            ))}
          </div>
        )}
        {showPlanNotice && teachingPlan && (
          <div className="pointer-events-auto absolute left-8 right-8 top-14 z-50 md:left-24 md:right-24">
            <div className="flex items-center justify-between rounded-lg border border-app-info/35 bg-app-surface/90 px-4 py-2 text-sm text-app-text shadow-[var(--app-shadow-teacher)] backdrop-blur-md">
              <span>📋 教学计划已制定</span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setShowPlanPanel(true)}
                  className="rounded border border-app-info/40 px-2 py-1 text-xs text-app-info hover:bg-app-info/10"
                >
                  查看计划
                </button>
                <button
                  type="button"
                  onClick={() => setShowPlanNotice(false)}
                  className="rounded border border-app-border/35 px-2 py-1 text-xs text-app-muted hover:text-app-text"
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        )}
        <div className="pointer-events-none absolute inset-0 cyber-bg opacity-70" />
        <div className="pointer-events-none absolute inset-0 cyber-focus-radial" />

        <div className="pointer-events-none absolute top-0 z-20 h-12 w-full flex-shrink-0 bg-gradient-to-b from-app-bg to-transparent" />

        <div className="px-4 pb-2 md:hidden">
          <div className="rounded-lg border border-app-border/30 bg-app-surface/70 px-4 py-2">
            <div className="text-lg font-semibold text-app-accent">{APP_CONFIG.projectTitle}</div>
          </div>
        </div>

        <div className="relative z-20 flex items-center justify-end gap-2 px-8 pb-2 pt-2 md:px-24">
          <button
            type="button"
            disabled={!teachingPlan}
            onClick={() => setShowPlanPanel(true)}
            className={
              teachingPlan
                ? "rounded border border-app-info/40 bg-app-info/10 px-3 py-1.5 text-xs text-app-info transition-colors hover:bg-app-info/20"
                : "cursor-not-allowed rounded border border-app-border/35 bg-app-surface/60 px-3 py-1.5 text-xs text-app-muted/70"
            }
          >
            {teachingPlan ? "查看学习计划" : "暂无学习计划"}
          </button>
        </div>

        <div className="relative z-10 flex-1 overflow-y-auto space-y-2 px-8 pb-56 pt-16 md:px-24">
          <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
            {messages.map((message) => (
              <ChatMessageItem
                key={message.id}
                message={message}
                devMode={settings.devMode}
                canRetry={message.id === lastAssistantId && message.role === "teacher" && !sending && !submittingToolAnswer}
                onRetry={handleRetry}
                onEditSave={(value) => handleEditSave(message.id, value)}
                onPrevVersion={() => handlePrevVersion(message.id)}
                onNextVersion={() => handleNextVersion(message.id)}
                onToolSubmit={handleToolSubmit}
                onPlanConfirm={handlePlanConfirm}
              />
            ))}
            <div ref={messageEndRef} />
          </div>
        </div>

        <div className="absolute bottom-0 left-0 right-0 z-30">
          {settings.devMode && <DebugPanel info={debugInfo} liveRaw={liveRaw} />}
          <ChatComposer
            value={input}
            pendingImages={pendingImages}
            disabled={sending || submittingToolAnswer}
            isResponding={sending || submittingToolAnswer}
            onChange={setInput}
            onImagesChange={setPendingImages}
            onSubmit={() => handleSend()}
            onAbort={handleAbort}
          />
        </div>
      </section>

      <SettingsPanel open={showSettings} onClose={() => setShowSettings(false)} />
      <TeachingPlanCard open={showPlanPanel} plan={teachingPlan || null} onClose={() => setShowPlanPanel(false)} />
      <ConversationManager
        open={showConversationManager}
        onClose={() => setShowConversationManager(false)}
        projects={projects}
        projectConversations={projectConversations}
        standaloneConversations={standaloneConversations}
        activeConversationId={settings.conversationId}
        onDeleteConversation={handleDeleteConversation}
        onDeleteProject={handleDeleteProject}
      />
    </main>
  );
}
