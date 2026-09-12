import { beforeEach, describe, expect, it, vi } from "vitest";

const openLiveConversation = vi.fn();

vi.mock("./liveVoice", () => ({
  openLiveConversation: (...args: unknown[]) => openLiveConversation(...args),
  narrateViaLive: vi.fn(),
  LiveVoiceUnavailable: class extends Error {},
}));
vi.mock("./speechPlayback", () => ({ claimSpeechChannel: vi.fn() }));
const noteCompanion = vi.fn(async (_sessionId: string, _kind: string, _text: string) => {});
vi.mock("./companionApi", () => ({
  noteCompanion: (sessionId: string, kind: string, text: string) =>
    noteCompanion(sessionId, kind, text),
}));

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
    expect(openLiveConversation.mock.calls[0]?.[0]).toBe("conv_a");
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

  it("records what was said, since nothing else does", async () => {
    const live = fakeConversation();
    let emit: ((u: { who: string; text: string }) => void) | undefined;
    openLiveConversation.mockImplementation(
      (_id: string, opts: { onUtterance?: (u: { who: string; text: string }) => void }) => {
        emit = opts.onUtterance;
        return Promise.resolve(live);
      },
    );

    await useLiveConversationStore.getState().start("conv_a");
    emit?.({ who: "reader", text: "does the waveform work in live mode?" });
    emit?.({ who: "voice", text: "it renders but never moves" });

    // The audio never touches our server, so if these are not written to the
    // ledger the conversation is gone the moment it ends.
    expect(noteCompanion).toHaveBeenCalledWith(
      "conv_a",
      "question",
      "does the waveform work in live mode?",
    );
    expect(noteCompanion).toHaveBeenCalledWith("conv_a", "answer", "it renders but never moves");
  });

  it("prices the session the way the API bills it", () => {
    // $0.05 a minute, by wall clock.
    expect(conversationCostUsd(60)).toBeCloseTo(0.05);
    expect(conversationCostUsd(3600)).toBeCloseTo(3);
  });
});

describe("a conversation nobody is having", () => {
  it("names an idle limit rather than billing until noticed", async () => {
    // The limit lives in liveVoice; this pins the contract the store relies
    // on: a session that goes quiet ends itself and reports it closed, so
    // the meter stops without the reader having to come back and press stop.
    const live = fakeConversation();
    openLiveConversation.mockResolvedValue(live);
    await useLiveConversationStore.getState().start("conv_a");
    expect(useLiveConversationStore.getState().sessionId).toBe("conv_a");

    live.end(); // what the idle timer does
    await vi.waitFor(() => {
      expect(useLiveConversationStore.getState().sessionId).toBeNull();
      expect(useLiveConversationStore.getState().elapsedS).toBe(0);
    });
  });
});
