import { beforeEach, describe, expect, it, vi } from "vitest";

const openLiveConversation = vi.fn();

vi.mock("./liveVoice", () => ({
  openLiveConversation: (...args: unknown[]) => openLiveConversation(...args),
  narrateViaLive: vi.fn(),
  LiveVoiceUnavailable: class extends Error {},
}));
const setConversationSpeaking = vi.fn();
const claimSpeechChannel = vi.fn();
vi.mock("./speechPlayback", () => ({
  claimSpeechChannel: (...args: unknown[]) => claimSpeechChannel(...args),
  setConversationSpeaking: (...args: unknown[]) => setConversationSpeaking(...args),
}));
const noteCompanion = vi.fn(async (_sessionId: string, _kind: string, _text: string) => {});
const delegateSpoken = vi.fn(async (_sessionId: string, _text: string) => ({
  forward: false,
  english: null as string | null,
  answer: null as string | null,
}));
const prewarmCompanion = vi.fn(async (_sessionId: string) => ({}));
vi.mock("./companionApi", () => ({
  noteCompanion: (sessionId: string, kind: string, text: string) =>
    noteCompanion(sessionId, kind, text),
  delegateSpoken: (sessionId: string, text: string) => delegateSpoken(sessionId, text),
  prewarmCompanion: (sessionId: string) => prewarmCompanion(sessionId),
}));

const send = vi.fn(async (_text: string, _agentId: string) => {});
vi.mock("@/store/chatStore", () => ({
  useChatStore: { getState: () => ({ send }) },
}));

import { useLiveConversationStore, conversationCostUsd, composeHandoff } from "./liveConversation";

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
  return {
    stream: new MediaStream(),
    closed,
    elapsedS: () => 12,
    stop,
    untilQuiet: vi.fn(async (_expectSpeech?: boolean) => {}),
    commentary: vi.fn((_delegationId: string, _content: string) => {}),
    thinking: vi.fn((_content: string) => {}),
    end: () => settle(),
  };
}

/** Give pending promise callbacks a moment to run. */
function settleCallbacks(): Promise<void> {
  return new Promise<void>((resolve) => {
    setTimeout(resolve, 20);
  });
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

  it("warms the companion up as the call opens, before anything is delegated", async () => {
    openLiveConversation.mockResolvedValue(fakeConversation());
    await useLiveConversationStore.getState().start("conv_a");
    expect(prewarmCompanion).toHaveBeenCalledWith("conv_a");
  });

  it("never opens a second one, which would be two voices and two meters", async () => {
    openLiveConversation.mockResolvedValue(fakeConversation());
    await useLiveConversationStore.getState().start("conv_a");
    await useLiveConversationStore.getState().start("conv_b");
    expect(openLiveConversation).toHaveBeenCalledTimes(1);
  });

  it("registers conversation speaking on start and unregisters on stop", async () => {
    const live = fakeConversation();
    openLiveConversation.mockResolvedValue(live);
    await useLiveConversationStore.getState().start("conv_a");
    expect(setConversationSpeaking).toHaveBeenCalledWith(true, expect.any(Function));
    expect(claimSpeechChannel).toHaveBeenCalledWith(expect.anything(), "conv_a");

    useLiveConversationStore.getState().stop();
    expect(setConversationSpeaking).toHaveBeenCalledWith(false);
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

interface ConversationOptions {
  onUtterance?: (u: { who: string; text: string }) => void;
  onDelegation?: (delegationId: string, asked: string) => void;
}

describe("answering what the voice delegates", () => {
  /** Open one conversation and return its callbacks. */
  async function open(agentId: string | null = "agent_1") {
    const live = fakeConversation();
    let opts: ConversationOptions = {};
    openLiveConversation.mockImplementation((_id: string, given: ConversationOptions) => {
      opts = given;
      return Promise.resolve(live);
    });
    await useLiveConversationStore.getState().start("conv_a", agentId);
    // A leftover session from a previous test makes start() return early, and
    // the failure then points at the wrong thing entirely.
    const { onUtterance, onDelegation } = opts;
    if (!onUtterance || !onDelegation) throw new Error("start() never opened a conversation");
    return { live, say: onUtterance, delegate: onDelegation };
  }

  beforeEach(() => {
    // A sibling describe, so the reset in the first one does not reach here.
    vi.clearAllMocks();
    delegateSpoken.mockResolvedValue({ forward: false, english: null, answer: null });
    useLiveConversationStore.setState({
      sessionId: null,
      handedOff: null,
      connecting: false,
      elapsedS: 0,
      error: null,
    });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  });

  it("has the voice say the companion's answer when it knows", async () => {
    delegateSpoken.mockResolvedValueOnce({
      forward: false,
      english: null,
      answer: "The painted stencils run four to one.",
    });
    const { live, delegate } = await open();
    delegate("item_d1", "did we already identify which link is which");

    await vi.waitFor(() => {
      expect(live.commentary).toHaveBeenCalledWith(
        "item_d1",
        "The painted stencils run four to one.",
      );
    });
    expect(delegateSpoken).toHaveBeenCalledWith(
      "conv_a",
      "did we already identify which link is which",
    );
    // Claude is not spent on what the companion already knows.
    expect(send).not.toHaveBeenCalled();
    expect(live.stop).not.toHaveBeenCalled();
  });

  it("sends it to Claude and ends the call when the companion cannot answer", async () => {
    delegateSpoken.mockResolvedValueOnce({
      forward: true,
      english: "Which caliper reading belongs to link three?",
      answer: null,
    });
    const { live, delegate } = await open();
    delegate("item_d2", "which caliper reading is link three");

    await vi.waitFor(() => {
      expect(live.stop).toHaveBeenCalled();
    });
    expect(send).toHaveBeenCalledWith(
      "Which caliper reading belongs to link three?",
      "agent_1",
      undefined,
      { forceClaude: true },
    );
    // The voice says so through the delegation, then the meter stops.
    expect(live.commentary.mock.calls[0]?.[0]).toBe("item_d2");
    expect(live.commentary.mock.calls[0]?.[1]).toContain("sent that to Claude");
    expect(live.untilQuiet.mock.invocationCallOrder[0]).toBeLessThan(
      live.stop.mock.invocationCallOrder[0] ?? 0,
    );
  });

  it("uses the reader's last sentence when the transcript has not caught up", async () => {
    const { say, delegate } = await open();
    say({ who: "reader", text: "what is the pitch of link three" });
    say({ who: "voice", text: "Let me find out." });
    delegate("item_d3", "");

    await vi.waitFor(() => {
      expect(delegateSpoken).toHaveBeenCalledWith("conv_a", "what is the pitch of link three");
    });
  });

  it("does nothing with a delegation that has nothing to go on", async () => {
    const { live, delegate } = await open();
    delegate("item_d4", "");
    await settleCallbacks();
    expect(delegateSpoken).not.toHaveBeenCalled();
    expect(live.commentary).not.toHaveBeenCalled();
  });

  it("tells the reader when there is nobody to send it to", async () => {
    delegateSpoken.mockResolvedValueOnce({ forward: true, english: null, answer: null });
    const { live, delegate } = await open(null);
    delegate("item_d5", "run the tests");

    await vi.waitFor(() => {
      expect(live.commentary).toHaveBeenCalledWith(
        "item_d5",
        expect.stringContaining("couldn't send"),
      );
    });
    // Hanging up without sending would lose the request entirely.
    expect(live.stop).not.toHaveBeenCalled();
  });

  it("never hands off twice", async () => {
    delegateSpoken.mockResolvedValue({ forward: true, english: null, answer: null });
    const { delegate } = await open();
    delegate("item_d6", "run the tests");
    delegate("item_d7", "and the linter");

    await vi.waitFor(() => {
      expect(send).toHaveBeenCalledTimes(1);
    });
    await settleCallbacks();
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("sends not-ready tool response on delegation timeout (15s) and does NOT send to Claude", async () => {
    vi.useFakeTimers();
    try {
      delegateSpoken.mockImplementation(() => new Promise(() => {}));
      const { live, delegate } = await open();
      delegate("item_d_timeout", "what is the status of the deployment");

      await vi.advanceTimersByTimeAsync(15_000);

      expect(live.commentary).toHaveBeenCalledWith(
        "item_d_timeout",
        expect.stringContaining("answer is not ready yet"),
      );
      expect(send).not.toHaveBeenCalled();
      expect(live.stop).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("composeHandoff", () => {
  it("carries the discussion, so a handoff is not stranded on its last phrase", () => {
    // The reported failure: three minutes of discussion, then "I want this",
    // and Claude received only that sentence.
    const out = composeHandoff("Go ahead with the occlusion fix.", [
      { who: "reader", text: "The D2 measurements look wrong." },
      { who: "voice", text: "Which ones — the occluded markers?" },
      { who: "reader", text: "No, forget occlusion. The good ones." },
      { who: "voice", text: "So you want the unoccluded set re-checked?" },
      { who: "reader", text: "I want this." },
    ]);
    expect(out).toContain("Go ahead with the occlusion fix.");
    expect(out).toContain("Me: The D2 measurements look wrong.");
    expect(out).toContain("Voice: Which ones — the occluded markers?");
    // Oldest first, so Claude reads the discussion in the order it happened.
    expect(out.indexOf("The D2 measurements")).toBeLessThan(out.indexOf("I want this."));
  });

  it("sends the bare question when there is nothing to add", () => {
    expect(composeHandoff("Run the tests.", [])).toBe("Run the tests.");
    expect(composeHandoff("Run the tests.", [{ who: "reader", text: "   " }])).toBe(
      "Run the tests.",
    );
  });

  it("drops the oldest lines rather than the request when the call is long", () => {
    const transcript = Array.from({ length: 400 }, (_, i) => ({
      who: (i % 2 === 0 ? "reader" : "voice") as "reader" | "voice",
      text: `line ${i} ${"x".repeat(40)}`,
    }));
    const out = composeHandoff("Do the thing.", transcript);
    expect(out.length).toBeLessThan(7000);
    expect(out).toContain("Do the thing.");
    expect(out).toContain("line 399");
    expect(out).not.toContain("line 0 ");
  });
});
