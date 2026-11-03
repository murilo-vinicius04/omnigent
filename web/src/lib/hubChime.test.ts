import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_CHIME_VOLUME,
  HUB_CHIME_ENABLED_KEY,
  HUB_CHIME_VOLUME_KEY,
  playHubChimeSound,
  readHubChimeEnabled,
  readHubChimeVolume,
  startHubChimeListener,
  useHubChimeStore,
  writeHubChimeEnabled,
  writeHubChimeVolume,
} from "./hubChime";

const hubLastId = vi.fn();
const hubUpdates = vi.fn();

vi.mock("./hubApi", () => ({
  hubLastId: () => hubLastId(),
  hubUpdates: (...args: unknown[]) => hubUpdates(...args),
}));

class FakeGainNode {
  gain = {
    value: 1,
    setValueAtTime: vi.fn(),
    exponentialRampToValueAtTime: vi.fn(),
  };
  connect = vi.fn();
  disconnect = vi.fn();
}

class FakeOscillatorNode {
  type = "sine";
  frequency = {
    value: 440,
    setValueAtTime: vi.fn(),
  };
  connect = vi.fn();
  disconnect = vi.fn();
  start = vi.fn();
  stop = vi.fn();
  onended: (() => void) | null = null;
}

class FakeAudioContext {
  state = "running";
  currentTime = 100;
  destination = {};
  resume() {
    return Promise.resolve();
  }
  createGain() {
    return new FakeGainNode();
  }
  createOscillator() {
    return new FakeOscillatorNode();
  }
}

describe("hubChime preferences", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useHubChimeStore.setState({ enabled: false, volume: DEFAULT_CHIME_VOLUME });
  });

  it("reads and writes enabled state in localStorage", () => {
    expect(readHubChimeEnabled()).toBe(false);
    writeHubChimeEnabled(true);
    expect(readHubChimeEnabled()).toBe(true);
    expect(window.localStorage.getItem(HUB_CHIME_ENABLED_KEY)).toBe("true");

    writeHubChimeEnabled(false);
    expect(readHubChimeEnabled()).toBe(false);
    expect(window.localStorage.getItem(HUB_CHIME_ENABLED_KEY)).toBe("false");
  });

  it("reads and writes volume state with clamping", () => {
    expect(readHubChimeVolume()).toBe(DEFAULT_CHIME_VOLUME);
    writeHubChimeVolume(0.8);
    expect(readHubChimeVolume()).toBe(0.8);
    expect(window.localStorage.getItem(HUB_CHIME_VOLUME_KEY)).toBe("0.8");

    writeHubChimeVolume(1.5);
    expect(readHubChimeVolume()).toBe(1.0);

    writeHubChimeVolume(-0.2);
    expect(readHubChimeVolume()).toBe(0.0);
  });

  it("updates store state reactively", () => {
    const store = useHubChimeStore.getState();
    store.setEnabled(true);
    expect(useHubChimeStore.getState().enabled).toBe(true);
    expect(readHubChimeEnabled()).toBe(true);

    store.setVolume(0.65);
    expect(useHubChimeStore.getState().volume).toBe(0.65);
    expect(readHubChimeVolume()).toBe(0.65);
  });
});

describe("hubChime audio synthesis", () => {
  let originalAudioContext: unknown;

  beforeEach(() => {
    originalAudioContext = window.AudioContext;
    (window as unknown as { AudioContext: typeof FakeAudioContext }).AudioContext =
      FakeAudioContext;
  });

  afterEach(() => {
    (window as unknown as { AudioContext: unknown }).AudioContext = originalAudioContext;
  });

  it("synthesizes finished chime with two tones", () => {
    const played = playHubChimeSound("finished", 0.5);
    expect(played).toBe(true);
  });

  it("synthesizes attention chime with three tones", () => {
    const played = playHubChimeSound("attention", 0.5);
    expect(played).toBe(true);
  });

  it("handles suspended audio context by resuming", () => {
    class SuspendedContext extends FakeAudioContext {
      override state = "suspended";
    }
    (window as unknown as { AudioContext: typeof SuspendedContext }).AudioContext =
      SuspendedContext;
    const played = playHubChimeSound("finished", 0.5);
    expect(played).toBe(true);
  });

  it("playChime in store respects enabled flag unless preview", () => {
    useHubChimeStore.setState({ enabled: false });
    const spy = vi.spyOn(FakeAudioContext.prototype, "createOscillator");

    useHubChimeStore.getState().playChime("finished");
    expect(spy).not.toHaveBeenCalled();

    useHubChimeStore.getState().playChime("preview");
    expect(spy).toHaveBeenCalled();

    spy.mockClear();
    useHubChimeStore.setState({ enabled: true });
    useHubChimeStore.getState().playChime("finished");
    expect(spy).toHaveBeenCalled();
  });
});

describe("startHubChimeListener", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useHubChimeStore.setState({ enabled: true, volume: 0.4 });
    hubLastId.mockResolvedValue(10);
    hubUpdates.mockResolvedValue({ updates: [], lastId: 10 });
  });

  it("listens for updates and triggers chime on finished and attention", async () => {
    const onChime = vi.fn();
    hubLastId.mockResolvedValueOnce(5);
    hubUpdates.mockResolvedValueOnce({
      updates: [
        { id: 6, kind: "finished", text: "Task done", session_name: "ProjectA" },
        { id: 7, kind: "attention", text: "Needs input", session_name: "ProjectB" },
      ],
      lastId: 7,
    });

    const stop = startHubChimeListener(onChime);

    await vi.waitFor(() => {
      expect(hubLastId).toHaveBeenCalled();
      expect(hubUpdates).toHaveBeenCalledWith(5, 25);
      expect(onChime).toHaveBeenCalledTimes(2);
      expect(onChime).toHaveBeenNthCalledWith(1, "finished", "Task done");
      expect(onChime).toHaveBeenNthCalledWith(2, "attention", "Needs input");
    });

    stop();
  });

  it("stops listening when cleaned up", async () => {
    const stop = startHubChimeListener();
    stop();
    hubUpdates.mockClear();
    await new Promise((r) => {
      setTimeout(r, 50);
    });
    expect(hubUpdates).not.toHaveBeenCalled();
  });
});
