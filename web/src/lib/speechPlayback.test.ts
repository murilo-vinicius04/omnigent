import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const narrateViaLive = vi.fn();
const narrateViaGeminiLive = vi.fn();

vi.mock("./liveVoice", () => ({
  narrateViaLive: (...args: unknown[]) => narrateViaLive(...args),
  LiveVoiceUnavailable: class extends Error {},
}));

vi.mock("./geminiNarrator", () => ({
  narrateViaGeminiLive: (...args: unknown[]) => narrateViaGeminiLive(...args),
}));

const { liveConversationState, registryEntry } = vi.hoisted(() => ({
  liveConversationState: {
    sessionId: null as string | null,
    connecting: false,
    start: vi.fn(),
  },
  registryEntry: {
    boundAgentId: null as string | null,
  },
}));

vi.mock("./liveConversation", () => ({
  useLiveConversationStore: { getState: () => liveConversationState },
}));

vi.mock("../store/conversationRegistry", () => ({
  conversationRegistry: {
    peek: () => ({ getState: () => ({ boundAgentId: registryEntry.boundAgentId }) }),
  },
}));

import {
  claimSpeechChannel,
  clearSpeechQueue,
  isMessageSpoken,
  readLiveAutoOpen,
  resetSpokenMessageTracking,
  setConversationSpeaking,
  useSpeechPlaybackStore,
  writeLiveAutoOpen,
} from "./speechPlayback";
import { setLiveVoiceEngine } from "./liveVoiceEngine";
import { useVoiceBackendStore } from "./sessionVoiceBackend";

class FakeMediaStream {
  getTracks() {
    return [];
  }
}

if (typeof globalThis.MediaStream === "undefined") {
  (globalThis as unknown as { MediaStream: unknown }).MediaStream = FakeMediaStream;
}

describe("speechPlayback call coordination and queueing", () => {
  const playedAudios: string[] = [];
  let createdAudioElements: HTMLAudioElement[] = [];

  beforeEach(() => {
    vi.clearAllMocks();
    playedAudios.length = 0;
    createdAudioElements = [];
    resetSpokenMessageTracking();
    clearSpeechQueue();
    setConversationSpeaking(false);
    useSpeechPlaybackStore.setState({ isSpeaking: false, speakingItemId: null });

    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});

    const RealAudio = window.Audio;
    vi.stubGlobal(
      "Audio",
      class extends RealAudio {
        constructor(src?: string) {
          super(src);
          if (src) playedAudios.push(src);
          createdAudioElements.push(this);
        }
      },
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    setConversationSpeaking(false);
    clearSpeechQueue();
  });

  it("queues summaries arriving during an open call and does not pause GPT audio or start playback", () => {
    const hangUp = vi.fn();
    setConversationSpeaking(true, hangUp);

    // GPT call audio element
    const gptAudio = document.createElement("audio");
    gptAudio.dataset.summaryAudio = "conversation";
    Object.defineProperty(gptAudio, "paused", { value: false, writable: true });
    document.body.appendChild(gptAudio);
    const gptPauseSpy = vi.spyOn(gptAudio, "pause");

    const handled = useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("msg_gpt", "GPT summary", "en", "/audio/gpt.mp3", "conv_1");

    expect(handled).toBe(true);
    expect(playedAudios).toHaveLength(0);
    expect(gptPauseSpy).not.toHaveBeenCalled();

    document.body.removeChild(gptAudio);
  });

  it("queues summaries arriving during an open Gemini call without starting playback", () => {
    const hangUp = vi.fn();
    setConversationSpeaking(true, hangUp);

    const handled = useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("msg_gemini", "Gemini summary", "en", "/audio/gemini.mp3", "conv_2");

    expect(handled).toBe(true);
    expect(playedAudios).toHaveLength(0);
    expect(narrateViaLive).not.toHaveBeenCalled();
  });

  it("drops a hub message read during a call instead of saving it for after", () => {
    // A hub call says the hub's news itself; reading it again after the call repeats it.
    setConversationSpeaking(true, vi.fn());

    const handled = useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("msg_hub", "Hub news", "pt-BR", "/audio/hub.mp3", "conv_hub", {
        dropDuringCall: true,
      });
    setConversationSpeaking(false);

    expect(handled).toBe(true);
    expect(isMessageSpoken("msg_hub")).toBe(true);
    expect(playedAudios).toHaveLength(0);
  });

  it("plays queued summary when the call ends", () => {
    const hangUp = vi.fn();
    setConversationSpeaking(true, hangUp);

    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("msg_wait", "Waiting summary", "en", "/audio/queued.mp3", "conv_1");
    expect(playedAudios).toHaveLength(0);

    // Call ends
    setConversationSpeaking(false);

    expect(playedAudios).toEqual(["/audio/queued.mp3"]);
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(true);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBe("msg_wait");
  });

  it("drops summaries queued more than 10 minutes before the drain, while playing fresh ones", () => {
    vi.useFakeTimers();
    const hangUp = vi.fn();
    setConversationSpeaking(true, hangUp);

    // Summary 1 queued at t = 0
    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("msg_old", "Old summary", "en", "/audio/old.mp3", "conv_1");

    // Advance 11 minutes (> 10 minutes)
    vi.advanceTimersByTime(11 * 60_000);

    // Summary 2 queued at t = 11 min (fresh)
    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("msg_fresh", "Fresh summary", "en", "/audio/fresh.mp3", "conv_1");

    expect(playedAudios).toHaveLength(0);

    // Call ends and triggers drain
    setConversationSpeaking(false);

    // Old summary must be dropped, fresh summary must play
    expect(playedAudios).toEqual(["/audio/fresh.mp3"]);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBe("msg_fresh");
  });

  it("pressing play (speakNow) during a call hangs up first and then plays", () => {
    let callHungUp = false;
    const hangUp = vi.fn(() => {
      callHungUp = true;
      setConversationSpeaking(false);
    });
    setConversationSpeaking(true, hangUp);

    const started = useSpeechPlaybackStore
      .getState()
      .speakNow("msg_manual", "Manual click", "en", "/audio/manual.mp3", "conv_1");

    expect(hangUp).toHaveBeenCalledTimes(1);
    expect(callHungUp).toBe(true);
    expect(started).toBe(true);
    expect(playedAudios).toEqual(["/audio/manual.mp3"]);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBe("msg_manual");
  });

  it("with no call open, summary behavior runs immediately as before", () => {
    setConversationSpeaking(false);

    const started = useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("msg_direct", "Direct summary", "en", "/audio/direct.mp3", "conv_1");

    expect(started).toBe(true);
    expect(playedAudios).toEqual(["/audio/direct.mp3"]);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBe("msg_direct");
  });

  it("claimSpeechChannel silences activeAudio and cleans up document summary audio elements", () => {
    const el1 = document.createElement("audio");
    el1.dataset.summaryAudio = "true";
    Object.defineProperty(el1, "paused", { value: false, writable: true });
    document.body.appendChild(el1);
    const pause1 = vi.spyOn(el1, "pause");

    const el2 = document.createElement("audio");
    claimSpeechChannel(el2, "conv_1");

    expect(pause1).toHaveBeenCalled();
    document.body.removeChild(el1);
  });

  it("uses Gemini narrator when getLiveVoiceEngine() is gemini", () => {
    setLiveVoiceEngine("gemini");
    useVoiceBackendStore.getState().set("conv_live_gemini", "live");
    const stopFn = vi.fn();
    narrateViaGeminiLive.mockReturnValue(
      Promise.resolve({
        stream: new FakeMediaStream() as unknown as MediaStream,
        finished: new Promise<void>(() => {}),
        stop: stopFn,
      }),
    );

    const started = useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("msg_gem", "Gemini text", "pt-BR", undefined, "conv_live_gemini");

    expect(started).toBe(true);
    // The voice is told which chat it reads for, and the summary's language.
    expect(narrateViaGeminiLive).toHaveBeenCalledWith("Gemini text", {
      sessionId: "conv_live_gemini",
      lang: "pt-BR",
    });
    expect(narrateViaLive).not.toHaveBeenCalled();
  });

  it("lets a hub message wait for the hub report being read, instead of cutting it off", () => {
    // 19:37:58: the hub's answer to a new message arrived 3 s into omnigent's
    // report, and a newer summary from the same chat replaces the one playing.
    setLiveVoiceEngine("gemini");
    useVoiceBackendStore.getState().set("conv_hub", "live");
    narrateViaGeminiLive.mockReturnValue(
      Promise.resolve({
        stream: new FakeMediaStream() as unknown as MediaStream,
        finished: new Promise<void>(() => {}),
        stop: vi.fn(),
      }),
    );
    const store = useSpeechPlaybackStore.getState();
    store.speakLiveSummary("msg_report", "Relatório longo.", "pt-BR", undefined, "conv_hub", {
      waitTurn: true,
    });
    store.speakLiveSummary("msg_ack", "Mandei.", "pt-BR", undefined, "conv_hub", {
      waitTurn: true,
    });

    expect(narrateViaGeminiLive).toHaveBeenCalledTimes(1);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBe("msg_report");
  });

  it("narrates through the local Unmute relay when that engine is chosen", () => {
    setLiveVoiceEngine("unmute");
    useVoiceBackendStore.getState().set("conv_live_unmute", "live");
    narrateViaGeminiLive.mockReturnValue(
      Promise.resolve({
        stream: new FakeMediaStream() as unknown as MediaStream,
        finished: new Promise<void>(() => {}),
        stop: vi.fn(),
      }),
    );

    const started = useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("msg_unmute", "Unmute text", "en", undefined, "conv_live_unmute");

    expect(started).toBe(true);
    expect(narrateViaGeminiLive).toHaveBeenCalledWith("Unmute text", {
      endpoint: "/v1/live/unmute/ws",
      sessionId: "conv_live_unmute",
      lang: "en",
    });
    expect(narrateViaLive).not.toHaveBeenCalled();
  });

  it("uses GPT narrator when getLiveVoiceEngine() is gpt", () => {
    setLiveVoiceEngine("gpt");
    useVoiceBackendStore.getState().set("conv_live_gpt", "live");
    const stopFn = vi.fn();
    narrateViaLive.mockReturnValue(
      Promise.resolve({
        stream: new FakeMediaStream() as unknown as MediaStream,
        finished: new Promise<void>(() => {}),
        stop: stopFn,
      }),
    );

    const started = useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("msg_gpt", "GPT text", "pt-BR", undefined, "conv_live_gpt");

    expect(started).toBe(true);
    expect(narrateViaLive).toHaveBeenCalledWith("GPT text", {
      sessionId: "conv_live_gpt",
      lang: "pt-BR",
    });
    expect(narrateViaGeminiLive).not.toHaveBeenCalled();
  });

  it("replace mode still rewinds the interrupted recording", () => {
    const el1 = document.createElement("audio");
    Object.defineProperty(el1, "paused", { value: false, writable: true });
    Object.defineProperty(el1, "currentTime", {
      value: 42,
      writable: true,
      configurable: true,
    });
    claimSpeechChannel(el1, "conv_1");

    claimSpeechChannel(document.createElement("audio"), "conv_1");

    expect(el1.currentTime).toBe(0);
  });

  it("pause mode parks the recording without rewinding and resumes it when the call ends", () => {
    const el1 = document.createElement("audio");
    Object.defineProperty(el1, "paused", { value: false, writable: true });
    Object.defineProperty(el1, "currentTime", {
      value: 42,
      writable: true,
      configurable: true,
    });
    const pause1 = vi.spyOn(el1, "pause");
    const play1 = vi.spyOn(el1, "play").mockResolvedValue(undefined);
    claimSpeechChannel(el1, "conv_1");

    // The live call takes the channel: pause in place, keep the offset.
    claimSpeechChannel(document.createElement("audio"), "conv_1", "pause");
    expect(pause1).toHaveBeenCalled();
    expect(el1.currentTime).toBe(42);

    // The call ends: the recording continues the sentence, not restarts it.
    setConversationSpeaking(false);
    expect(play1).toHaveBeenCalled();
    expect(el1.currentTime).toBe(42);
  });

  it("drop mode clears the speaking flags so the queue is not wedged", () => {
    useSpeechPlaybackStore.setState({ isSpeaking: true, speakingItemId: "msg_drop" });
    const el1 = document.createElement("audio");
    Object.defineProperty(el1, "paused", { value: false, writable: true });
    Object.defineProperty(el1, "currentTime", {
      value: 42,
      writable: true,
      configurable: true,
    });
    claimSpeechChannel(el1, "conv_1");

    claimSpeechChannel(document.createElement("audio"), "conv_1", "drop");

    // Paused in place like pause mode, but the flags are cleared: nothing
    // resumes after the call and the queue is free to drain.
    expect(el1.currentTime).toBe(42);
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBeNull();
  });

  it("early-stop path stops active live narration for both engines", async () => {
    setLiveVoiceEngine("gemini");
    useVoiceBackendStore.getState().set("conv_live_stop", "live");
    const stopFn = vi.fn();
    narrateViaGeminiLive.mockReturnValue(
      Promise.resolve({
        stream: new FakeMediaStream() as unknown as MediaStream,
        finished: new Promise<void>(() => {}),
        stop: stopFn,
      }),
    );

    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("msg_stop", "Text", "pt-BR", undefined, "conv_live_stop");

    await new Promise<void>((resolve) => {
      setTimeout(resolve, 0);
    });
    useSpeechPlaybackStore.getState().stop();

    expect(stopFn).toHaveBeenCalled();
  });

  describe("auto-open live call on narration", () => {
    const tick = () =>
      new Promise<void>((resolve) => {
        setTimeout(resolve, 0);
      });
    const flushAutoOpen = async () => {
      // Dynamic import, permission query, then the store start.
      await tick();
      await tick();
    };

    function stubMicPermission(state: string) {
      Object.defineProperty(window.navigator, "permissions", {
        value: { query: vi.fn().mockResolvedValue({ state }) },
        configurable: true,
      });
    }

    beforeEach(() => {
      window.localStorage.removeItem("omnigent:live-voice-auto-open");
      liveConversationState.sessionId = null;
      liveConversationState.connecting = false;
      liveConversationState.start.mockReset().mockResolvedValue(undefined);
      registryEntry.boundAgentId = null;
      delete (window.navigator as unknown as { permissions?: unknown }).permissions;
    });

    afterEach(() => {
      window.localStorage.removeItem("omnigent:live-voice-auto-open");
      liveConversationState.sessionId = null;
      liveConversationState.connecting = false;
      registryEntry.boundAgentId = null;
      delete (window.navigator as unknown as { permissions?: unknown }).permissions;
    });

    const lastAudio = () => createdAudioElements[createdAudioElements.length - 1]!;
    const endNarration = async () => {
      lastAudio().dispatchEvent(new Event("ended"));
      await flushAutoOpen();
    };

    it("is on by default: the call opens at the natural end, not at the start", async () => {
      expect(readLiveAutoOpen()).toBe(true);
      stubMicPermission("granted");

      const started = useSpeechPlaybackStore
        .getState()
        .speakLiveSummary("msg_def", "Default on", "en", "/audio/def.mp3", "conv_def");

      await flushAutoOpen();

      // Started playing, but no call while the narration is still going.
      expect(started).toBe(true);
      expect(liveConversationState.start).not.toHaveBeenCalled();

      await endNarration();

      expect(liveConversationState.start).toHaveBeenCalledTimes(1);
    });

    it("stays off with an explicit stored false", async () => {
      window.localStorage.setItem("omnigent:live-voice-auto-open", "false");
      expect(readLiveAutoOpen()).toBe(false);

      useSpeechPlaybackStore
        .getState()
        .speakLiveSummary("msg_noauto", "No auto-open", "en", "/audio/noauto.mp3", "conv_noauto");

      await endNarration();

      expect(liveConversationState.start).not.toHaveBeenCalled();
    });

    it("opens a call with drop mode when opted in and the mic is already granted", async () => {
      writeLiveAutoOpen(true);
      stubMicPermission("granted");

      useSpeechPlaybackStore
        .getState()
        .speakLiveSummary("msg_auto", "Auto text", "en", "/audio/auto.mp3", "conv_auto");

      await endNarration();

      expect(liveConversationState.start).toHaveBeenCalledTimes(1);
      expect(liveConversationState.start).toHaveBeenCalledWith("conv_auto", null, {
        narration: "drop",
      });
    });

    it("hands the session's agent to the auto-opened call so questions reach Claude", async () => {
      writeLiveAutoOpen(true);
      stubMicPermission("granted");
      registryEntry.boundAgentId = "agent_123";

      useSpeechPlaybackStore
        .getState()
        .speakLiveSummary("msg_agent", "Agent text", "en", "/audio/agent.mp3", "conv_agent");

      await endNarration();

      expect(liveConversationState.start).toHaveBeenCalledWith("conv_agent", "agent_123", {
        narration: "drop",
      });
      registryEntry.boundAgentId = null;
    });

    it("never triggers a permission prompt: skips when the mic is not granted", async () => {
      writeLiveAutoOpen(true);
      stubMicPermission("prompt");

      useSpeechPlaybackStore
        .getState()
        .speakLiveSummary("msg_nomic", "No mic", "en", "/audio/nomic.mp3", "conv_nomic");

      await endNarration();

      expect(liveConversationState.start).not.toHaveBeenCalled();
    });

    it("does not open a second call when one is already open", async () => {
      writeLiveAutoOpen(true);
      stubMicPermission("granted");
      liveConversationState.sessionId = "conv_other";

      useSpeechPlaybackStore
        .getState()
        .speakLiveSummary("msg_second", "Second", "en", "/audio/second.mp3", "conv_second");

      await endNarration();

      expect(liveConversationState.start).not.toHaveBeenCalled();
    });

    it("does not auto-open when playback errors: nothing was heard", async () => {
      writeLiveAutoOpen(true);
      stubMicPermission("granted");

      useSpeechPlaybackStore
        .getState()
        .speakLiveSummary("msg_err", "Error", "en", "/audio/err.mp3", "conv_err");

      await flushAutoOpen();
      lastAudio().dispatchEvent(new Event("error"));
      await flushAutoOpen();

      expect(liveConversationState.start).not.toHaveBeenCalled();
      expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
    });

    it("does not auto-open from a manual play press: listening, not a handoff", async () => {
      writeLiveAutoOpen(true);
      stubMicPermission("granted");

      const started = useSpeechPlaybackStore
        .getState()
        .speakNow("msg_manual", "Manual", "en", "/audio/manual.mp3", "conv_manual");

      await flushAutoOpen();
      expect(started).toBe(true);
      await endNarration();

      expect(liveConversationState.start).not.toHaveBeenCalled();
    });

    it("opens a call when a live narration played audibly to its end", async () => {
      writeLiveAutoOpen(true);
      stubMicPermission("granted");
      setLiveVoiceEngine("gpt");
      useVoiceBackendStore.getState().set("conv_live_auto", "live");
      let settleFinished!: () => void;
      const finished = new Promise<void>((resolve) => {
        settleFinished = resolve;
      });
      narrateViaLive.mockReturnValue(
        Promise.resolve({
          stream: new FakeMediaStream() as unknown as MediaStream,
          finished,
          stop: vi.fn(),
        }),
      );

      useSpeechPlaybackStore
        .getState()
        .speakLiveSummary("msg_live", "Live text", "pt-BR", undefined, "conv_live_auto");

      await flushAutoOpen();
      // Nothing heard yet: handshake only.
      expect(liveConversationState.start).not.toHaveBeenCalled();

      lastAudio().dispatchEvent(new Event("playing"));
      settleFinished();
      await flushAutoOpen();

      expect(liveConversationState.start).toHaveBeenCalledTimes(1);
      expect(liveConversationState.start).toHaveBeenCalledWith("conv_live_auto", null, {
        narration: "drop",
      });
    });

    it("does not auto-open when a live session ends without ever playing", async () => {
      writeLiveAutoOpen(true);
      stubMicPermission("granted");
      setLiveVoiceEngine("gpt");
      useVoiceBackendStore.getState().set("conv_live_silent", "live");
      let settleFinished!: () => void;
      const finished = new Promise<void>((resolve) => {
        settleFinished = resolve;
      });
      narrateViaLive.mockReturnValue(
        Promise.resolve({
          stream: new FakeMediaStream() as unknown as MediaStream,
          finished,
          stop: vi.fn(),
        }),
      );

      useSpeechPlaybackStore
        .getState()
        .speakLiveSummary("msg_silent", "Silent", "pt-BR", undefined, "conv_live_silent");

      await flushAutoOpen();
      // Autoplay refused or the stream died: finished with no `playing` event.
      settleFinished();
      await flushAutoOpen();

      expect(liveConversationState.start).not.toHaveBeenCalled();
    });
  });
});
