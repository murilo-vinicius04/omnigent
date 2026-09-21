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

import {
  claimSpeechChannel,
  clearSpeechQueue,
  resetSpokenMessageTracking,
  setConversationSpeaking,
  useSpeechPlaybackStore,
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
    expect(narrateViaGeminiLive).toHaveBeenCalledWith("Gemini text");
    expect(narrateViaLive).not.toHaveBeenCalled();
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
    expect(narrateViaLive).toHaveBeenCalledWith("GPT text");
    expect(narrateViaGeminiLive).not.toHaveBeenCalled();
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
});
