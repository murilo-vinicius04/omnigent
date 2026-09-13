import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const narrateViaLive = vi.fn();

vi.mock("./liveVoice", () => ({
  narrateViaLive: (...args: unknown[]) => narrateViaLive(...args),
  LiveVoiceUnavailable: class extends Error {},
}));

import {
  isMessageSpoken,
  useSpeechPlaybackStore,
  resetSpokenMessageTracking,
} from "./speechPlayback";
import { useVoiceBackendStore } from "./sessionVoiceBackend";

// jsdom implements neither MediaStream nor the srcObject it is attached to.
if (typeof globalThis.MediaStream === "undefined") {
  (globalThis as unknown as { MediaStream: unknown }).MediaStream = function MediaStream() {};
}

/** A live session that never finishes on its own, so tests control the end. */
function fakeLive() {
  let settle: () => void = () => {};
  const finished = new Promise<void>((resolve) => {
    settle = resolve;
  });
  const stop = vi.fn(() => {
    settle();
  });
  return { stream: new MediaStream(), finished, stop, end: () => settle() };
}

/** Recording URLs handed to an audio element, so tests can see what played. */
const played: string[] = [];

describe("speech playback routing between voices", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    useVoiceBackendStore.setState({ choices: {} });
    resetSpokenMessageTracking();
    useSpeechPlaybackStore.setState({ isSpeaking: false, speakingItemId: null });
    // jsdom has no real media element playback.
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    played.length = 0;
    const RealAudio = window.Audio;
    vi.stubGlobal(
      "Audio",
      class extends RealAudio {
        constructor(src?: string) {
          super(src);
          if (src) played.push(src);
        }
      },
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the recording when the session is on the local voice", () => {
    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("m1", "All tests pass.", "en", "/audio/m1.mp3", "conv_a");
    expect(narrateViaLive).not.toHaveBeenCalled();
    expect(played).toEqual(["/audio/m1.mp3"]);
  });

  it("reads through the live voice when the session has chosen it", async () => {
    useVoiceBackendStore.getState().set("conv_a", "live");
    narrateViaLive.mockResolvedValue(fakeLive());

    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("m1", "All tests pass.", "en", "/audio/m1.mp3", "conv_a");

    await vi.waitFor(() => {
      expect(narrateViaLive).toHaveBeenCalledWith("All tests pass.");
    });
    expect(played).toEqual([]);
  });

  it("speaks without waiting for a recording that the live voice never needs", async () => {
    useVoiceBackendStore.getState().set("conv_a", "live");
    narrateViaLive.mockResolvedValue(fakeLive());

    // No audioUrl: the local path would decline, the live path must not.
    const started = useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("m1", "All tests pass.", "en", undefined, "conv_a");

    expect(started).toBe(true);
    await vi.waitFor(() => {
      expect(narrateViaLive).toHaveBeenCalled();
    });
  });

  it("never plays the local recording on the live voice, even when live cannot open", async () => {
    useVoiceBackendStore.getState().set("conv_a", "live");
    narrateViaLive.mockRejectedValue(new Error("no credits remaining"));

    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("m1", "All tests pass.", "en", "/audio/m1.mp3", "conv_a");

    await vi.waitFor(() => {
      expect(narrateViaLive).toHaveBeenCalled();
      expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
    });
    // The reader chose live: the local voice is the wrong voice, not a rescue.
    expect(played).toEqual([]);
    // Left unmarked, so pressing play tries the live voice again.
    expect(isMessageSpoken("m1")).toBe(false);
  });

  it("never plays a recording for a live session whose summary has no text", () => {
    useVoiceBackendStore.getState().set("conv_a", "live");

    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("m1", "   ", "en", "/audio/m1.mp3", "conv_a");

    expect(narrateViaLive).not.toHaveBeenCalled();
    expect(played).toEqual([]);
  });

  it("hangs up rather than pausing when the reader asks for quiet", async () => {
    useVoiceBackendStore.getState().set("conv_a", "live");
    const live = fakeLive();
    narrateViaLive.mockResolvedValue(live);

    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("m1", "All tests pass.", "en", undefined, "conv_a");
    await vi.waitFor(() => {
      expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(true);
    });

    useSpeechPlaybackStore.getState().stop();
    // A paused session would go on billing by wall clock.
    expect(live.stop).toHaveBeenCalled();
  });

  it("hangs up a session the reader already moved past", async () => {
    useVoiceBackendStore.getState().set("conv_a", "live");
    const live = fakeLive();
    let release: (v: typeof live) => void = () => {};
    narrateViaLive.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );

    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("m1", "All tests pass.", "en", undefined, "conv_a");
    // The handshake takes a moment; the reader stops during it.
    useSpeechPlaybackStore.getState().stop();
    release(live);

    await vi.waitFor(() => {
      expect(live.stop).toHaveBeenCalled();
    });
  });
});
