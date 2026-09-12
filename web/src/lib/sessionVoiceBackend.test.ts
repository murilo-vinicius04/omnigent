import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_BACKEND,
  currentVoiceBackend,
  readSessionBackend,
  useVoiceBackendStore,
  writeSessionBackend,
} from "./sessionVoiceBackend";

describe("sessionVoiceBackend", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useVoiceBackendStore.setState({ choices: {} });
  });

  it("defaults to the free local voice", () => {
    expect(DEFAULT_BACKEND).toBe("local");
    expect(readSessionBackend("conv_a")).toBe("local");
    expect(readSessionBackend(null)).toBe("local");
  });

  it("keeps the choice per session, not globally", () => {
    useVoiceBackendStore.getState().set("conv_a", "live");
    expect(currentVoiceBackend("conv_a")).toBe("live");
    expect(currentVoiceBackend("conv_b")).toBe("local");
  });

  it("persists a choice across a reload", () => {
    writeSessionBackend("conv_a", "live");
    useVoiceBackendStore.setState({ choices: {} });
    expect(currentVoiceBackend("conv_a")).toBe("live");
  });

  it("falls back to the free voice when storage is unreadable", () => {
    // A blocked store must never strand a session on the paid voice: a broken
    // preference should cost nothing rather than spend quietly.
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readSessionBackend("conv_a")).toBe("local");
    getItem.mockRestore();
  });

  it("survives a write it cannot persist", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    expect(() => {
      useVoiceBackendStore.getState().set("conv_a", "live");
    }).not.toThrow();
    // The in-memory choice still applies for this page load.
    expect(currentVoiceBackend("conv_a")).toBe("live");
    setItem.mockRestore();
  });

  it("treats an unrecognised stored value as local", () => {
    window.localStorage.setItem("omnigent:voice-backend:conv_a", "gpt9");
    expect(readSessionBackend("conv_a")).toBe("local");
  });
});
