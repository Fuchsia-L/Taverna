import { useCallback, useEffect, useRef, useState } from "react";
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
  fetchDebugInfo,
  getProjectConversations,
  listProjects,
  listStandaloneConversations,
  ProjectSummary,
  retryLastReply,
  retryLastReplyStream,
  rewindAndResendStream,
  rewindAndResend,
  StreamRawPayload,
  submitToolResponse,
  submitToolResponseStream,
  sendMessageWithOptions,
  sendMessageStream
} from "@/services/chatApi";
import { useSettings } from "@/stores/settingsStore";
import { APP_CONFIG } from "@/config/app";
import { AttachedImage, ChatMessage, ToolInputEvent } from "@/types/chat";

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
  const [showPlanNotice, setShowPlanNotice] = useState(false);
  const [planBuildState, setPlanBuildState] = useState<"idle" | "building" | "done">("idle");
  const [submittingToolAnswer, setSubmittingToolAnswer] = useState(false);
  const [pendingImages, setPendingImages] = useState<AttachedImage[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [standaloneConversations, setStandaloneConversations] = useState<ConversationMeta[]>([]);
  const [projectConversations, setProjectConversations] = useState<Record<string, ConversationMeta[]>>({});
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: buildId(),
      role: "teacher",
      content: APP_CONFIG.text.initialTeacherMessage
    }
  ]);
  const messageEndRef = useRef<HTMLDivElement>(null);
  const activeRequestRef = useRef<AbortController | null>(null);
  const handledToolSignalsRef = useRef<Set<string>>(new Set());
  const previousPlanExistsRef = useRef(false);

  const pushToolEvent = useCallback((name: string) => {
    const id = buildId();
    setToolEvents((prev) => [...prev, { id, text: `工具调用：${name}` }]);
    setPlanBuildState("building");
    setTimeout(() => {
      setToolEvents((prev) => prev.filter((item) => item.id !== id));
    }, 4500);
  }, []);

  const addToolCard = useCallback((event: ToolInputEvent) => {
    setMessages((prev) => [
      ...prev,
      {
        id: buildId(),
        role: "teacher",
        content: "",
        toolCard: event,
        toolCardSubmitted: false,
        versions: [""],
        rawVersions: [undefined],
        currentVersion: 0,
      },
    ]);
  }, []);

  // Keep scroll position stable during conversation; no auto-follow.

  useEffect(() => {
    document.documentElement.dataset.theme = APP_CONFIG.theme.defaultTheme;
  }, []);

  const refreshNavigation = useCallback(async () => {
    try {
      const projectItems = await listProjects();
      setProjects(projectItems);

      const conversationsByProject: Record<string, ConversationMeta[]> = {};
      await Promise.all(
        projectItems.map(async (project) => {
          conversationsByProject[project.id] = await getProjectConversations(project.id);
        })
      );
      setProjectConversations(conversationsByProject);

      const standalone = await listStandaloneConversations();
      setStandaloneConversations(standalone);
      if (!settings.conversationId && standalone.length > 0) {
        setConversationId(standalone[0].id);
      }
    } catch (error) {
      console.error(error);
    }
  }, [setConversationId, settings.conversationId]);

  useEffect(() => {
    void refreshNavigation();
  }, [refreshNavigation]);

  const refreshDebugInfo = useCallback(
    async (session: string | null) => {
      if (!settings.devMode) {
        return;
      }
      try {
        const info = await fetchDebugInfo(session);
        setDebugInfo(info);
        setTeachingPlan(info.teaching_plan || null);
        setMessages((prev) => applyDebugTurns(prev, info));
        if (info.conversation_id) {
          setConversationId(info.conversation_id);
        }
      } catch (error) {
        console.error(error);
      }
    },
    [settings.devMode, setConversationId]
  );

  const checkLatestTurnSignals = useCallback(
    async (session: string | null) => {
      if (!session) {
        return;
      }
      try {
        const info = await fetchDebugInfo(session);
        const hasPlanNow = Boolean(info.teaching_plan);
        const hadPlanBefore = previousPlanExistsRef.current;
        setTeachingPlan(info.teaching_plan || null);
        if (!hadPlanBefore && hasPlanNow) {
          setShowPlanNotice(true);
          setPlanBuildState("done");
        } else if (hasPlanNow && planBuildState === "building") {
          setPlanBuildState("done");
        }
        previousPlanExistsRef.current = hasPlanNow;

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
        if (toolRounds.length > 0 && planBuildState === "idle") {
          setPlanBuildState("building");
        }
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
    [planBuildState, pushToolEvent]
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
        const hasPlanNow = Boolean(info.teaching_plan);
        const hadPlanBefore = previousPlanExistsRef.current;
        setTeachingPlan(info.teaching_plan || null);
        if (!hadPlanBefore && hasPlanNow) {
          setShowPlanNotice(true);
          setPlanBuildState("done");
        } else if (hasPlanNow) {
          setPlanBuildState("done");
        }
        previousPlanExistsRef.current = hasPlanNow;
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
  }, [planBuildState, settings.conversationId]);

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
    const timer = window.setTimeout(() => setPlanBuildState("idle"), 2800);
    return () => window.clearTimeout(timer);
  }, [planBuildState]);

  const handleSend = useCallback(
    async (overrideText?: string) => {
      const text = (overrideText ?? input).trim();
      if ((!text && pendingImages.length === 0) || sending) {
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
              conversation_id: settings.conversationId
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
            ({ fullContent, thinkingContent, conversationId }) => {
              if (conversationId) {
                setConversationId(conversationId);
                latestSessionId = conversationId;
              }
              const finalThinking = (thinkingContent || streamThinking).trim();
              const withThinking =
                finalThinking && !/<think>[\s\S]*?<\/think>/i.test(fullContent)
                  ? `<think>${finalThinking}</think>\n${fullContent}`
                  : fullContent;
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
            (tool) => {
              pushToolEvent(tool.name);
            },
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
          setConversationId(result.conversationId);
        }
        setMessages((prev) => [
          ...prev,
          createVersionedMessage("teacher", result.data.reply, userMessage.turnIndex)
        ]);
        if (result.data.tool_input_required) {
          addToolCard(result.data.tool_input_required);
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
        setSending(false);
        if (activeRequestRef.current === controller) {
          activeRequestRef.current = null;
        }
        await refreshDebugInfo(latestSessionId);
        await checkLatestTurnSignals(latestSessionId);
      }
    },
    [checkLatestTurnSignals, input, pendingImages, pushToolEvent, refreshDebugInfo, sending, setConversationId, settings.model, settings.conversationId, settings.streaming]
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

    const lastTeacher = [...messages].reverse().find((msg) => msg.role === "teacher");
    if (!lastTeacher) {
      return;
    }

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
              ? { ...msg, isStreaming: true, streamingThinking: "" }
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
          ({ fullContent, thinkingContent, conversationId }) => {
            if (conversationId) {
              setConversationId(conversationId);
              latestSessionId = conversationId;
            }
            const finalThinking = (thinkingContent || streamThinking).trim();
            const withThinking =
              finalThinking && !/<think>[\s\S]*?<\/think>/i.test(fullContent)
                ? `<think>${finalThinking}</think>\n${fullContent}`
                : fullContent;
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
          (tool) => {
            pushToolEvent(tool.name);
          },
          (event) => {
            addToolCard(event);
          },
          { signal: controller.signal }
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
      const result = await retryLastReply(settings.conversationId, settings.model, { signal: controller.signal });
      const latestSessionId = result.conversationId || settings.conversationId;
      if (result.conversationId) {
        setConversationId(result.conversationId);
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
  }, [checkLatestTurnSignals, messages, pushToolEvent, refreshDebugInfo, sending, setConversationId, settings.model, settings.conversationId, settings.streaming]);

  const handleEditSave = useCallback(
    async (messageId: string, value: string) => {
      if (!settings.conversationId || sending) {
        return;
      }

      const targetTurn = getUserTurnByMessageId(messageId);
      if (targetTurn < 1) {
        return;
      }

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
              model: settings.model
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
            ({ fullContent, thinkingContent, conversationId }) => {
              if (conversationId) {
                setConversationId(conversationId);
                latestSessionId = conversationId;
              }
              const finalThinking = (thinkingContent || streamThinking).trim();
              const withThinking =
                finalThinking && !/<think>[\s\S]*?<\/think>/i.test(fullContent)
                  ? `<think>${finalThinking}</think>\n${fullContent}`
                  : fullContent;
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
          (tool) => {
            pushToolEvent(tool.name);
          },
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
          model: settings.model
        }, { signal: controller.signal });
        const latestSessionId = result.conversationId || settings.conversationId;

        if (result.conversationId) {
          setConversationId(result.conversationId);
        }

        setMessages((prev) => {
          let next = appendVersionToTurn(prev, "student", targetTurn, value);
          next = appendVersionToTurn(next, "teacher", targetTurn, result.data.reply);
          return next;
        });
        if (result.data.tool_input_required) {
          addToolCard(result.data.tool_input_required);
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
    [checkLatestTurnSignals, getUserTurnByMessageId, messages, pushToolEvent, refreshDebugInfo, sending, setConversationId, settings.model, settings.conversationId, settings.streaming]
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
            ({ fullContent, thinkingContent, conversationId }) => {
              if (conversationId) {
                setConversationId(conversationId);
                latestSessionId = conversationId;
              }
              const finalThinking = (thinkingContent || streamThinking).trim();
              const withThinking =
                finalThinking && !/<think>[\s\S]*?<\/think>/i.test(fullContent)
                  ? `<think>${finalThinking}</think>\n${fullContent}`
                  : fullContent;
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
            (tool) => {
              pushToolEvent(tool.name);
            },
            { signal: controller.signal }
          );
          await refreshDebugInfo(latestSessionId);
          await checkLatestTurnSignals(latestSessionId);
        } else {
          const result = await submitToolResponse(
            toolCallId,
            answers,
            settings.model,
            settings.conversationId,
            { signal: controller.signal }
          );
          if (result.conversationId) {
            setConversationId(result.conversationId);
          }
          if (result.data.tool_input_required) {
            addToolCard(result.data.tool_input_required);
          } else {
            setMessages((prev) => [...prev, createVersionedMessage("teacher", result.data.reply)]);
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
      addToolCard,
      checkLatestTurnSignals,
      pushToolEvent,
      refreshDebugInfo,
      setConversationId,
      settings.model,
      settings.conversationId,
      settings.streaming,
      submittingToolAnswer,
    ]
  );

  const handleCreateProject = useCallback(async () => {
    const title = window.prompt("请输入项目标题");
    if (!title || !title.trim()) {
      return;
    }
    try {
      const created = await createProject({ title: title.trim() });
      setConversationId(created.planning_conversation.id);
      await refreshNavigation();
      setMessages([
        {
          id: buildId(),
          role: "teacher",
          content: APP_CONFIG.text.initialTeacherMessage
        }
      ]);
    } catch (error) {
      console.error(error);
    }
  }, [refreshNavigation, setConversationId]);

  const handleCreateStandaloneConversation = useCallback(async () => {
    try {
      const created = await createStandaloneConversation({ title: "独立会话" });
      setConversationId(created.id);
      await refreshNavigation();
      setMessages([
        {
          id: buildId(),
          role: "teacher",
          content: APP_CONFIG.text.initialTeacherMessage
        }
      ]);
    } catch (error) {
      console.error(error);
    }
  }, [refreshNavigation, setConversationId]);

  const handleSelectConversation = useCallback(
    async (conversationId: string) => {
      if (settings.conversationId === conversationId) {
        return;
      }
      setConversationId(conversationId);
      setMessages([
        {
          id: buildId(),
          role: "teacher",
          content: APP_CONFIG.text.initialTeacherMessage
        }
      ]);
      setDebugInfo(null);
      await refreshDebugInfo(conversationId);
    },
    [refreshDebugInfo, setConversationId, settings.conversationId]
  );

  const lastAssistantId = [...messages].reverse().find((msg) => msg.role === "teacher")?.id ?? null;
  const handlePrevVersion = useCallback((messageId: string) => {
    setMessages((prev) => cycleMessageVersion(prev, messageId, -1));
  }, []);
  const handleNextVersion = useCallback((messageId: string) => {
    setMessages((prev) => cycleMessageVersion(prev, messageId, 1));
  }, []);
  const handleAbort = useCallback(() => {
    activeRequestRef.current?.abort();
    setMessages((prev) => prev.map((msg) => finalizeStreamingMessage(msg)));
    setSending(false);
    setSubmittingToolAnswer(false);
    setLiveRaw(null);
  }, []);

  return (
    <main className="group relative flex h-screen w-full overflow-hidden bg-app-bg text-app-text selection:bg-app-accentAlt selection:text-white">
      <div className="pointer-events-none absolute inset-0 bg-app-vignette" />

      <ChatSidebar
        onOpenSettings={() => setShowSettings(true)}
        projects={projects}
        standaloneConversations={standaloneConversations}
        activeConversationId={settings.conversationId}
        projectConversations={projectConversations}
        onSelectConversation={handleSelectConversation}
        onCreateProject={handleCreateProject}
        onCreateConversation={handleCreateStandaloneConversation}
      />

      <section className="relative z-10 flex flex-1 flex-col overflow-hidden">
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
    </main>
  );
}

