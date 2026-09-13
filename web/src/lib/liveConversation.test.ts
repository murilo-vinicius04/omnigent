import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const openLiveConversation = vi.fn();

vi.mock("./liveVoice", () => ({
  openLiveConversation: (...args: unknown[]) => openLiveConversation(...args),
  narrateViaLive: vi.fn(),
  LiveVoiceUnavailable: class extends Error {},
}));
vi.mock("./speechPlayback", () => ({ claimSpeechChannel: vi.fn() }));
const noteCompanion = vi.fn(async (_sessionId: string, _kind: string, _text: string) => {});
const routeSpoken = vi.fn(async (_sessionId: string, _text: string) => ({
  forward: false,
  english: null as string | null,
  answer: null as string | null,
}));
vi.mock("./companionApi", () => ({
  noteCompanion: (sessionId: string, kind: string, text: string) =>
    noteCompanion(sessionId, kind, text),
  routeSpoken: (sessionId: string, text: string) => routeSpoken(sessionId, text),
}));

const send = vi.fn(async (_text: string, _agentId: string) => {});
vi.mock("@/store/chatStore", () => ({
  useChatStore: { getState: () => ({ send }) },
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
  const announce = vi.fn(async (_instruction: string) => {});
  return {
    stream: new MediaStream(),
    closed,
    elapsedS: () => 12,
    stop,
    announce,
    end: () => settle(),
  };
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

describe("handing a spoken request to Claude", () => {
  /** Drive one conversation and return its utterance callback. */
  async function open(agentId: string | null = "agent_1") {
    const live = fakeConversation();
    let emit: ((u: { who: string; text: string }) => void) | undefined;
    let speaking: (() => void) | undefined;
    openLiveConversation.mockImplementation(
      (
        _id: string,
        opts: {
          onUtterance?: (u: { who: string; text: string }) => void;
          onReaderSpeaking?: () => void;
        },
      ) => {
        emit = opts.onUtterance;
        speaking = opts.onReaderSpeaking;
        return Promise.resolve(live);
      },
    );
    await useLiveConversationStore.getState().start("conv_a", agentId);
    // A leftover session from a previous test makes start() return early and
    // the failure then shows up as "emit is not a function", which points at
    // the wrong thing entirely.
    if (!emit || !speaking) throw new Error("start() never opened a conversation");
    return { live, emit, speaking };
  }

  it("waits while the reader is still talking, even past the settle window", async () => {
    const { emit, speaking } = await open();
    emit({ who: "reader", text: "hey, so now you can" });
    await vi.advanceTimersByTimeAsync(2000);
    speaking(); // the rest of the sentence has started arriving
    await vi.advanceTimersByTimeAsync(2000);
    expect(routeSpoken).not.toHaveBeenCalled();

    emit({ who: "reader", text: "talk to Claude" });
    await vi.advanceTimersByTimeAsync(3000);
    expect(routeSpoken).toHaveBeenCalledTimes(1);
    expect(routeSpoken.mock.calls[0]?.[1]).toBe("hey, so now you can talk to Claude");
  });

  beforeEach(() => {
    // A sibling describe, so the reset in the first one does not reach here.
    vi.clearAllMocks();
    routeSpoken.mockResolvedValue({ forward: false, english: null, answer: null });
    useLiveConversationStore.setState({
      sessionId: null,
      handedOff: null,
      connecting: false,
      elapsedS: 0,
      error: null,
    });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("joins fragments instead of sending half a thought", async () => {
    const { emit } = await open();
    emit({ who: "reader", text: "can you run" });
    await vi.advanceTimersByTimeAsync(1000); // a breath, not the end
    emit({ who: "reader", text: "the migration tests" });
    await vi.advanceTimersByTimeAsync(3000);

    expect(routeSpoken).toHaveBeenCalledTimes(1);
    expect(routeSpoken.mock.calls[0]?.[1]).toBe("can you run the migration tests");
  });

  it("sends to Claude and hangs up when it is work", async () => {
    routeSpoken.mockResolvedValueOnce({
      forward: true,
      english: "Run the migration tests.",
      answer: null,
    });
    const { live, emit } = await open();
    emit({ who: "reader", text: "run the migration tests" });
    await vi.advanceTimersByTimeAsync(3000);

    // Forced past routing: it was already routed as speech.
    expect(send).toHaveBeenCalledWith("Run the migration tests.", "agent_1", undefined, {
      forceClaude: true,
    });
    // The voice is told before the call ends; on its own it guessed and said no.
    expect(live.announce).toHaveBeenCalledTimes(1);
    expect(live.announce.mock.calls[0]?.[0]).toContain("sent to Claude");
    // The meter must not run through however long the turn takes.
    expect(live.stop).toHaveBeenCalled();
    expect(live.announce.mock.invocationCallOrder[0]).toBeLessThan(
      live.stop.mock.invocationCallOrder[0] ?? 0,
    );
  });

  it("tells the voice nothing when the conversation keeps what was said", async () => {
    const { live, emit } = await open();
    emit({ who: "reader", text: "can't you ask Claude?" });
    await vi.advanceTimersByTimeAsync(3000);
    expect(live.announce).not.toHaveBeenCalled();
  });

  it("tells a voice that stalled on something kept to answer it", async () => {
    // It said "one sec" to "what are the next steps", nothing followed, and the
    // idle timer hung up on a question it had the answer to.
    const { live, emit } = await open();
    emit({ who: "reader", text: "what were we planning as next steps?" });
    emit({ who: "voice", text: "Yeah! One sec." });
    await vi.advanceTimersByTimeAsync(3000);
    expect(live.announce).toHaveBeenCalledTimes(1);
    expect(live.announce.mock.calls[0]?.[0]).toContain("not sent to Claude");
    expect(live.stop).not.toHaveBeenCalled();
  });

  it("leaves a voice that actually answered alone", async () => {
    const { live, emit } = await open();
    emit({ who: "reader", text: "what are the next steps?" });
    emit({ who: "voice", text: "Making the A76 mesh watertight, then the per-link survey." });
    await vi.advanceTimersByTimeAsync(3000);
    expect(live.announce).not.toHaveBeenCalled();
  });

  it("keeps talking when the companion can answer", async () => {
    const { live, emit } = await open();
    emit({ who: "reader", text: "what did you change?" });
    await vi.advanceTimersByTimeAsync(3000);

    expect(send).not.toHaveBeenCalled();
    expect(live.stop).not.toHaveBeenCalled();
  });

  it("never hands off twice", async () => {
    routeSpoken.mockResolvedValue({ forward: true, english: null, answer: null });
    const { emit } = await open();
    emit({ who: "reader", text: "run the tests" });
    await vi.advanceTimersByTimeAsync(3000);
    emit({ who: "reader", text: "and the linter" });
    await vi.advanceTimersByTimeAsync(3000);

    expect(send).toHaveBeenCalledTimes(1);
  });

  it("stays open when there is no agent to send to", async () => {
    routeSpoken.mockResolvedValueOnce({ forward: true, english: null, answer: null });
    const { live, emit } = await open(null);
    emit({ who: "reader", text: "run the tests" });
    await vi.advanceTimersByTimeAsync(3000);

    // Hanging up without sending would lose the request entirely.
    expect(live.stop).not.toHaveBeenCalled();
  });

  it("ignores what the voice itself says", async () => {
    const { emit } = await open();
    emit({ who: "voice", text: "sure, let me look at that" });
    await vi.advanceTimersByTimeAsync(3000);
    expect(routeSpoken).not.toHaveBeenCalled();
  });
});
