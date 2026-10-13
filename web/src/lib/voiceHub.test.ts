import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  opts: null as null | {
    endpoint?: string;
    onEvent?: (ev: unknown) => void;
    onStateChange?: (st: unknown) => void;
  },
  stop: vi.fn(),
  chatStop: vi.fn(),
  chatStart: vi.fn(),
  engine: "gemini" as "gpt" | "gemini" | "unmute",
  live: {
    sessionId: null as string | null,
    error: null as string | null,
    notice: null as string | null,
  },
  listeners: [] as ((state: unknown) => void)[],
}));

vi.mock("@/lib/geminiLive", () => ({
  startGeminiLive: vi.fn(async (opts: typeof h.opts) => {
    h.opts = opts;
    return { stop: h.stop, sendToolResponse: vi.fn(), untilPlaybackDrained: async () => {} };
  }),
}));
vi.mock("@/lib/liveConversation", () => ({
  useLiveConversationStore: {
    getState: () => ({ ...h.live, noticeTone: "info", stop: h.chatStop, start: h.chatStart }),
    subscribe: (listener: (state: unknown) => void) => {
      h.listeners.push(listener);
      return () => {
        h.listeners = h.listeners.filter((l) => l !== listener);
      };
    },
  },
}));
vi.mock("@/lib/liveVoiceEngine", () => ({
  getLiveVoiceEngine: () => h.engine,
  unmuteEndpoint: () => "/v1/live/unmute/ws?voice=ex02",
}));
vi.mock("@/lib/hubApi", () => ({ openHub: vi.fn(async () => "conv_hub") }));

import { describeHubTool, HUB_ENDPOINT, hubEndpoint, useVoiceHubStore } from "@/lib/voiceHub";

describe("voice hub", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useVoiceHubStore.setState({ active: false, connecting: false, notice: null, activity: null });
    h.stop.mockClear();
    h.chatStop.mockClear();
    h.chatStart.mockReset();
    h.engine = "gemini";
    h.live = { sessionId: null, error: null, notice: null };
    h.listeners = [];
  });
  afterEach(() => {
    useVoiceHubStore.getState().stop();
    vi.useRealTimers();
  });

  it("opens the hub socket, not a chat's, and closes a chat call first", async () => {
    await useVoiceHubStore.getState().start();
    expect(h.opts?.endpoint).toBe(HUB_ENDPOINT);
    expect(h.chatStop).toHaveBeenCalled();
    expect(useVoiceHubStore.getState().active).toBe(true);
  });

  it("shows which session a tool is working on, then its result for a while", async () => {
    await useVoiceHubStore.getState().start();
    h.opts?.onEvent?.({
      type: "toolCall",
      calls: [{ id: "t1", name: "ask_claude", args: { session: "amarras" } }],
    });
    expect(useVoiceHubStore.getState().activity).toEqual({
      tool: "ask_claude",
      project: "amarras",
      state: "running",
      result: "",
    });
    h.opts?.onEvent?.({
      type: "toolDone",
      id: "t1",
      name: "ask_claude",
      project: "Amarras",
      output: "Sent to Amarras's Claude.",
    });
    expect(useVoiceHubStore.getState().activity?.state).toBe("done");
    await vi.advanceTimersByTimeAsync(6_500);
    expect(useVoiceHubStore.getState().activity).toBeNull();
  });

  it("ends the call after the goodbye that follows a send, not after a look", async () => {
    await useVoiceHubStore.getState().start();
    const done = { type: "toolDone", id: "t9", project: "RAL", output: "ok" };
    h.opts?.onEvent?.({ ...done, name: "session_status", sent: false });
    h.opts?.onEvent?.({ type: "turnComplete" });
    await vi.advanceTimersByTimeAsync(0);
    expect(h.stop).not.toHaveBeenCalled();

    h.opts?.onEvent?.({ ...done, name: "ask_claude", output: "Sent to RAL.", sent: true });
    expect(h.stop).not.toHaveBeenCalled(); // the voice still has to say where it went
    h.opts?.onEvent?.({ type: "turnComplete" });
    await vi.advanceTimersByTimeAsync(0);
    expect(h.stop).toHaveBeenCalledWith("page hung up");
    expect(useVoiceHubStore.getState().active).toBe(false);
  });

  it("says when Gemini stopped answering and ends the hub", async () => {
    await useVoiceHubStore.getState().start();
    h.opts?.onStateChange?.({
      state: "closed",
      kind: "error",
      code: 4000,
      reason: "gemini stopped responding",
    });
    const state = useVoiceHubStore.getState();
    expect(state.active).toBe(false);
    expect(state.notice).toBe("Gemini stopped answering — start the hub again");
  });

  it("stopping hangs up the call", async () => {
    await useVoiceHubStore.getState().start();
    useVoiceHubStore.getState().stop();
    expect(h.stop).toHaveBeenCalledWith("page hung up");
    expect(useVoiceHubStore.getState().active).toBe(false);
  });

  it("words each tool the way the pill says it", () => {
    expect(describeHubTool("session_status", "all")).toBe("Looking at every project");
    expect(describeHubTool("session_status", "amarras")).toBe("Looking at amarras");
    expect(describeHubTool("check_machine", "Brasa")).toBe("Checking on Brasa (read-only)");
    expect(describeHubTool("ask_claude", "")).toBe("Sending to a project's Claude");
  });

  it("on Unmute, opens the Unmute relay as the hub", async () => {
    h.engine = "unmute";
    expect(hubEndpoint("unmute")).toBe("/v1/live/unmute/ws?voice=ex02&mode=hub");
    await useVoiceHubStore.getState().start();
    expect(h.opts?.endpoint).toBe("/v1/live/unmute/ws?voice=ex02&mode=hub");
    expect(useVoiceHubStore.getState().active).toBe(true);
  });

  it("on GPT Live, rides the hub conversation's own call and ends with it", async () => {
    h.engine = "gpt";
    h.opts = null;
    h.chatStart.mockImplementation(async () => {
      h.live = { sessionId: "conv_hub", error: null, notice: null };
    });
    await useVoiceHubStore.getState().start();
    expect(h.chatStart).toHaveBeenCalledWith("conv_hub", null, { fromVoiceHub: true });
    expect(h.opts).toBeNull(); // no relay socket: GPT-Live talks to OpenAI itself
    expect(useVoiceHubStore.getState().active).toBe(true);
    // The call's notices show on the hub's pill.
    h.live = { sessionId: "conv_hub", error: null, notice: "Working on what you asked…" };
    h.listeners.forEach((l) => l({ ...h.live, noticeTone: "info" }));
    expect(useVoiceHubStore.getState().notice).toBe("Working on what you asked…");
    // Hanging up the call ends the hub.
    h.live = { sessionId: null, error: null, notice: null };
    h.listeners.forEach((l) => l({ ...h.live, noticeTone: "info" }));
    expect(useVoiceHubStore.getState().active).toBe(false);
  });

  it("on GPT Live, stopping the hub hangs up its call", async () => {
    h.engine = "gpt";
    h.chatStart.mockImplementation(async () => {
      h.live = { sessionId: "conv_hub", error: null, notice: null };
    });
    await useVoiceHubStore.getState().start();
    h.chatStop.mockClear();
    useVoiceHubStore.getState().stop();
    expect(h.chatStop).toHaveBeenCalled();
    expect(useVoiceHubStore.getState().active).toBe(false);
  });

  it("on GPT Live, says so when the call could not open", async () => {
    h.engine = "gpt";
    h.chatStart.mockImplementation(async () => {
      h.live = { sessionId: null, error: "microphone unavailable", notice: null };
    });
    await useVoiceHubStore.getState().start();
    const state = useVoiceHubStore.getState();
    expect(state.active).toBe(false);
    expect(state.notice).toBe("Hub could not start: microphone unavailable");
  });
});
