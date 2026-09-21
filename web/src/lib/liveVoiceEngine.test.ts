import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchGeminiLiveAvailability,
  getLiveVoiceEngine,
  setLiveVoiceEngine,
} from "./liveVoiceEngine";

const KEY = "omnigent:live-voice-engine";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("getLiveVoiceEngine", () => {
  it("defaults to gpt when nothing is stored", () => {
    expect(getLiveVoiceEngine()).toBe("gpt");
  });

  it("reads a persisted gemini choice", () => {
    setLiveVoiceEngine("gemini");
    expect(getLiveVoiceEngine()).toBe("gemini");
  });

  it("reads a persisted unmute choice", () => {
    setLiveVoiceEngine("unmute");
    expect(getLiveVoiceEngine()).toBe("unmute");
  });

  it("falls back to gpt on an invalid stored value", () => {
    window.localStorage.setItem(KEY, "claude");
    expect(getLiveVoiceEngine()).toBe("gpt");
  });

  it("falls back to gpt when localStorage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("quota");
    });
    expect(getLiveVoiceEngine()).toBe("gpt");
  });
});

describe("setLiveVoiceEngine", () => {
  it("persists the choice", () => {
    setLiveVoiceEngine("gemini");
    expect(window.localStorage.getItem(KEY)).toBe("gemini");
  });

  it("swallows storage failures", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    expect(() => setLiveVoiceEngine("gemini")).not.toThrow();
  });
});

describe("fetchGeminiLiveAvailability", () => {
  it("returns configured when the server reports configured", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ configured: true, model: "gemini-2.0" }), { status: 200 }),
      ),
    );
    expect(await fetchGeminiLiveAvailability()).toBe("configured");
    vi.unstubAllGlobals();
  });

  it("returns unconfigured when configured is not true", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () => new Response(JSON.stringify({ configured: false, model: "" }), { status: 200 }),
      ),
    );
    expect(await fetchGeminiLiveAvailability()).toBe("unconfigured");
    vi.unstubAllGlobals();
  });

  it("returns unknown on a non-2xx response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("nope", { status: 401 })),
    );
    expect(await fetchGeminiLiveAvailability()).toBe("unknown");
    vi.unstubAllGlobals();
  });

  it("returns unknown on a network error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      }),
    );
    expect(await fetchGeminiLiveAvailability()).toBe("unknown");
    vi.unstubAllGlobals();
  });
});
