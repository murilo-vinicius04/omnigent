import { beforeEach, describe, expect, it, vi } from "vitest";

const openLiveConversation = vi.fn();

vi.mock("./liveVoice", () => ({
  openLiveConversation: (...args: unknown[]) => openLiveConversation(...args),
  narrateViaLive: vi.fn(),
  LiveVoiceUnavailable: class extends Error {},
}));
vi.mock("./speechPlayback", () => ({ claimSpeechChannel: vi.fn() }));

import { useLiveConversationStore, conversationCostUsd } from "./liveConversation";

if (typeof globalThis.MediaStream === "undefined") {
  (globalThis as unknown as { MediaStream: unknown }).MediaStream = function MediaStream() {};
}

function fakeConversation() {
  let settle: () => void = () => {};
  const closed = new Promise<void>((resolve) => {
    settle = resolve;
  });
  const stop = vi.fn(() => {
    settle();
  });
  return { stream: new MediaStream(), closed, elapsedS: () => 12, stop, end: () => settle() };
}

describe("the open spoken conversation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useLiveConversationStore.setState({
      sessionId: null,
      connecting: false,
      elapsedS: 0,
      error: null,
    });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  });

  it("opens one for the session and reports it", async () => {
    openLiveConversation.mockResolvedValue(fakeConversation());
    await useLiveConversationStore.getState().start("conv_a");
    expect(openLiveConversation).toHaveBeenCalledWith("conv_a");
    expect(useLiveConversationStore.getState().sessionId).toBe("conv_a");
  });

  it("never opens a second one, which would be two voices and two meters", async () => {
    openLiveConversation.mockResolvedValue(fakeConversation());
    await useLiveConversationStore.getState().start("conv_a");
    await useLiveConversationStore.getState().start("conv_b");
    expect(openLiveConversation).toHaveBeenCalledTimes(1);
  });

  it("hangs up on stop, so the meter stops with it", async () => {
    const live = fakeConversation();
    openLiveConversation.mockResolvedValue(live);
    await useLiveConversationStore.getState().start("conv_a");
    useLiveConversationStore.getState().stop();
    expect(live.stop).toHaveBeenCalled();
    expect(useLiveConversationStore.getState().sessionId).toBeNull();
  });

  it("clears itself when the session ends on its own", async () => {
    const live = fakeConversation();
    openLiveConversation.mockResolvedValue(live);
    await useLiveConversationStore.getState().start("conv_a");
    live.end();
    await vi.waitFor(() => {
      expect(useLiveConversationStore.getState().sessionId).toBeNull();
    });
  });

  it("reports a refused microphone instead of looking broken", async () => {
    openLiveConversation.mockRejectedValue(new Error("microphone unavailable"));
    await useLiveConversationStore.getState().start("conv_a");
    expect(useLiveConversationStore.getState().sessionId).toBeNull();
    expect(useLiveConversationStore.getState().connecting).toBe(false);
    expect(useLiveConversationStore.getState().error).toContain("microphone");
  });

  it("prices the session the way the API bills it", () => {
    // $0.05 a minute, by wall clock.
    expect(conversationCostUsd(60)).toBeCloseTo(0.05);
    expect(conversationCostUsd(3600)).toBeCloseTo(3);
  });
});
