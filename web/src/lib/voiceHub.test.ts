import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  opts: null as null | {
    endpoint?: string;
    onEvent?: (ev: unknown) => void;
    onStateChange?: (st: unknown) => void;
  },
  stop: vi.fn(),
  chatStop: vi.fn(),
}));

vi.mock("@/lib/geminiLive", () => ({
  startGeminiLive: vi.fn(async (opts: typeof h.opts) => {
    h.opts = opts;
    return { stop: h.stop, sendToolResponse: vi.fn() };
  }),
}));
vi.mock("@/lib/liveConversation", () => ({
  useLiveConversationStore: { getState: () => ({ stop: h.chatStop }) },
}));

import { describeHubTool, HUB_ENDPOINT, useVoiceHubStore } from "@/lib/voiceHub";

describe("voice hub", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useVoiceHubStore.setState({ active: false, connecting: false, notice: null, activity: null });
    h.stop.mockClear();
    h.chatStop.mockClear();
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
    expect(describeHubTool("session_status", "all")).toBe("Looking at every session");
    expect(describeHubTool("session_status", "amarras")).toBe("Looking at amarras");
    expect(describeHubTool("check_machine", "Brasa")).toBe("Checking on Brasa (read-only)");
    expect(describeHubTool("ask_claude", "")).toBe("Sending to a session's Claude");
  });
});
