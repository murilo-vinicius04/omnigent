import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  NARRATE_WITHIN_MS,
  narrateLatestSummary,
  resetCrossSessionNarration,
  startCrossSessionNarration,
} from "./crossSessionNarration";
import { useVolumeStore } from "./sessionNarrationVolume";
import {
  clearInMemorySpokenTracking,
  clearSpeechQueue,
  isMessageSpoken,
  resetSpokenMessageTracking,
  useSpeechPlaybackStore,
} from "./speechPlayback";

const fetchSessionItemsPage = vi.fn();
vi.mock("@/lib/sessionsApi", () => ({
  fetchSessionItemsPage: (...args: unknown[]) => fetchSessionItemsPage(...args),
}));

let frameListener: ((frame: unknown) => void) | null = null;
const unsubscribe = vi.fn();
vi.mock("@/lib/sessionUpdatesSocket", () => ({
  sessionUpdatesSocket: {
    subscribe: (listener: (frame: unknown) => void) => {
      frameListener = listener;
      return unsubscribe;
    },
  },
}));

const NOW_S = 1_700_000_000;
const now = () => NOW_S * 1000;

function summaryItem(overrides: Record<string, unknown> = {}) {
  return {
    response_id: "resp_1",
    created_at: NOW_S - 5,
    data: {
      content: [
        { type: "spoken_summary", text: "the turn finished", lang: "en-US", audio_file_id: "f_1" },
      ],
    },
    ...overrides,
  };
}

describe("crossSessionNarration", () => {
  beforeEach(() => {
    resetCrossSessionNarration();
    resetSpokenMessageTracking();
    clearInMemorySpokenTracking();
    localStorage.clear();
    sessionStorage.clear();
    fetchSessionItemsPage.mockReset();
    frameListener = null;
    // spyOn returns the SAME spy when the property is already spied, so its
    // history outlives the test that made it unless cleared here.
    vi.clearAllMocks();
    clearSpeechQueue();
    useSpeechPlaybackStore.setState({ isSpeaking: false, speakingItemId: null });
    useVolumeStore.getState().set("conv_1", 1); // narration on for sessions with no choice
  });

  it("speaks a summary from a session the reader is not looking at", async () => {
    const speak = vi.spyOn(useSpeechPlaybackStore.getState(), "speakLiveSummary");
    fetchSessionItemsPage.mockResolvedValue({ items: [summaryItem()], hasMore: false });

    await narrateLatestSummary("conv_other", now);

    expect(speak).toHaveBeenCalledWith(
      "resp_1",
      "the turn finished",
      "en-US",
      "/v1/sessions/conv_other/resources/files/f_1/content",
      "conv_other",
    );
  });

  it("never re-speaks a summary the reader already heard", async () => {
    const speak = vi.spyOn(useSpeechPlaybackStore.getState(), "speakLiveSummary");
    fetchSessionItemsPage.mockResolvedValue({ items: [summaryItem()], hasMore: false });

    await narrateLatestSummary("conv_1", now);
    await narrateLatestSummary("conv_1", now); // e.g. another frame for the same turn

    expect(speak).toHaveBeenCalledTimes(1);
  });

  it("stays quiet about a summary older than the window", async () => {
    // A reconnect or a laptop waking up must not read out a backlog.
    const speak = vi.spyOn(useSpeechPlaybackStore.getState(), "speakLiveSummary");
    const old = summaryItem({ created_at: NOW_S - NARRATE_WITHIN_MS / 1000 - 60 });
    fetchSessionItemsPage.mockResolvedValue({ items: [old], hasMore: false });

    await narrateLatestSummary("conv_1", now);

    expect(speak).not.toHaveBeenCalled();
  });

  it("takes the newest summary when the lookback holds more than one", async () => {
    const speak = vi.spyOn(useSpeechPlaybackStore.getState(), "speakLiveSummary");
    fetchSessionItemsPage.mockResolvedValue({
      items: [
        summaryItem({ response_id: "resp_old" }),
        { response_id: "resp_mid", data: { content: [{ type: "output_text", text: "hi" }] } },
        summaryItem({ response_id: "resp_new", created_at: NOW_S - 1 }),
      ],
      hasMore: false,
    });

    await narrateLatestSummary("conv_1", now);

    expect(speak).toHaveBeenCalledTimes(1);
    expect(speak.mock.calls[0]?.[0]).toBe("resp_new");
  });

  it("survives a failed lookup", async () => {
    fetchSessionItemsPage.mockRejectedValue(new Error("offline"));
    await expect(narrateLatestSummary("conv_1", now)).resolves.toBeUndefined();
  });

  it("looks only at finished turns, and only once per revision", async () => {
    fetchSessionItemsPage.mockResolvedValue({ items: [], hasMore: false });
    startCrossSessionNarration(now);
    expect(frameListener).not.toBeNull();

    frameListener?.({ type: "changed", items: [{ id: "c1", status: "running", updated_at: 1 }] });
    expect(fetchSessionItemsPage).not.toHaveBeenCalled();

    frameListener?.({ type: "changed", items: [{ id: "c1", status: "idle", updated_at: 2 }] });
    frameListener?.({ type: "changed", items: [{ id: "c1", status: "idle", updated_at: 2 }] });
    await vi.waitFor(() => expect(fetchSessionItemsPage).toHaveBeenCalledTimes(1));

    // A later change on the same session (the summary item landing) is a new
    // revision, so it is examined again.
    frameListener?.({ type: "changed", items: [{ id: "c1", status: "idle", updated_at: 3 }] });
    await vi.waitFor(() => expect(fetchSessionItemsPage).toHaveBeenCalledTimes(2));
  });

  it("ignores the snapshot frame a reconnect replays", async () => {
    fetchSessionItemsPage.mockResolvedValue({ items: [], hasMore: false });
    startCrossSessionNarration(now);

    frameListener?.({ type: "snapshot", items: [{ id: "c1", status: "idle", updated_at: 9 }] });

    expect(fetchSessionItemsPage).not.toHaveBeenCalled();
  });
});

describe("speech queue", () => {
  beforeEach(() => {
    resetSpokenMessageTracking();
    clearInMemorySpokenTracking();
    localStorage.clear();
    sessionStorage.clear();
    vi.clearAllMocks();
    clearSpeechQueue();
    useVolumeStore.getState().set("conv_1", 1);
    useSpeechPlaybackStore.setState({ isSpeaking: false, speakingItemId: null });
  });

  it("waits its turn instead of talking over the summary already playing", () => {
    // Two conversations finishing together used to play at once.
    const played: string[] = [];
    const RealAudio = window.Audio;
    const made: HTMLAudioElement[] = [];
    vi.stubGlobal(
      "Audio",
      class extends RealAudio {
        constructor(src?: string) {
          super(src);
          made.push(this as unknown as HTMLAudioElement);
          played.push(src ?? "");
        }
      },
    );
    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => {});

    const speak = useSpeechPlaybackStore.getState().speakLiveSummary;
    speak("resp_a", "first", "en-US", "/a.mp3", "conv_a");
    speak("resp_b", "second", "en-US", "/b.mp3", "conv_b");

    expect(played).toEqual(["/a.mp3"]); // the second one is waiting

    made[0]!.dispatchEvent(new Event("ended"));
    expect(played).toEqual(["/a.mp3", "/b.mp3"]); // ...and follows on

    vi.unstubAllGlobals();
  });

  it("lets a newer summary from the SAME conversation replace the one playing", () => {
    // Queueing is for other conversations. Within one, the newer summary
    // supersedes the old, so finishing the stale one helps nobody.
    const played: string[] = [];
    const RealAudio = window.Audio;
    vi.stubGlobal(
      "Audio",
      class extends RealAudio {
        constructor(src?: string) {
          super(src);
          played.push(src ?? "");
        }
      },
    );
    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => {});

    const speak = useSpeechPlaybackStore.getState().speakLiveSummary;
    speak("resp_a", "first", "en-US", "/a.mp3", "conv_same");
    speak("resp_b", "second", "en-US", "/b.mp3", "conv_same");

    expect(played).toEqual(["/a.mp3", "/b.mp3"]);
    vi.unstubAllGlobals();
  });

  it("drops what is waiting when the reader asks for quiet", () => {
    const played: string[] = [];
    const RealAudio = window.Audio;
    const made: HTMLAudioElement[] = [];
    vi.stubGlobal(
      "Audio",
      class extends RealAudio {
        constructor(src?: string) {
          super(src);
          made.push(this as unknown as HTMLAudioElement);
          played.push(src ?? "");
        }
      },
    );
    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => {});

    const { speakLiveSummary, stop } = useSpeechPlaybackStore.getState();
    speakLiveSummary("resp_a", "first", "en-US", "/a.mp3", "conv_a");
    speakLiveSummary("resp_b", "second", "en-US", "/b.mp3", "conv_b");
    stop();

    made[0]!.dispatchEvent(new Event("ended"));
    expect(played).toEqual(["/a.mp3"]);

    vi.unstubAllGlobals();
  });
});

describe("autoplay blocked by the browser", () => {
  beforeEach(() => {
    resetSpokenMessageTracking();
    clearInMemorySpokenTracking();
    localStorage.clear();
    sessionStorage.clear();
    vi.clearAllMocks();
    clearSpeechQueue();
    useSpeechPlaybackStore.setState({ isSpeaking: false, speakingItemId: null });
  });

  it("does not burn the summary when play() is refused", async () => {
    // Chrome refuses programmatic playback until the page has been interacted
    // with. Marking happens before playback starts, so a refusal used to leave
    // the summary "already spoken" and permanently silent.
    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockRejectedValue(
      new DOMException("blocked", "NotAllowedError"),
    );
    vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => {});

    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("resp_blocked", "resumo", "pt-BR", "/a.mp3", "conv_1");

    await vi.waitFor(() => expect(isMessageSpoken("resp_blocked")).toBe(false));
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
  });
});
