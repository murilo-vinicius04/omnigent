import { beforeEach, describe, expect, it } from "vitest";
import {
  DEFAULT_VOLUME,
  isNarrationEnabled,
  readSessionVolume,
  useVolumeStore,
  writeSessionVolume,
} from "./sessionNarrationVolume";

describe("sessionNarrationVolume", () => {
  beforeEach(() => {
    localStorage.clear();
    useVolumeStore.setState({ levels: {} });
  });

  it("starts a session at full volume", () => {
    // Silence and "not configured yet" must never look the same.
    expect(readSessionVolume("conv_1")).toBe(DEFAULT_VOLUME);
    expect(readSessionVolume(null)).toBe(DEFAULT_VOLUME);
  });

  it("keeps a level per session, not per device", () => {
    useVolumeStore.getState().set("conv_1", 0.3);
    expect(useVolumeStore.getState().get("conv_1")).toBe(0.3);
    expect(useVolumeStore.getState().get("conv_2")).toBe(DEFAULT_VOLUME);
  });

  it("survives a reload", () => {
    useVolumeStore.getState().set("conv_1", 0.45);
    useVolumeStore.setState({ levels: {} }); // as if the page reloaded
    expect(useVolumeStore.getState().get("conv_1")).toBe(0.45);
  });

  it("clamps out-of-range and unparseable values", () => {
    useVolumeStore.getState().set("conv_1", 4);
    expect(useVolumeStore.getState().get("conv_1")).toBe(1);
    useVolumeStore.getState().set("conv_2", -2);
    expect(useVolumeStore.getState().get("conv_2")).toBe(0);
    localStorage.setItem("omnigent:narrate-volume:conv_3", "loud");
    expect(readSessionVolume("conv_3")).toBe(DEFAULT_VOLUME);
  });

  it("does not throw when storage is unavailable", () => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error("blocked");
    };
    expect(() => writeSessionVolume("conv_1", 0.5)).not.toThrow();
    Storage.prototype.setItem = original;
  });
});

describe("muting is how narration is turned off", () => {
  beforeEach(() => {
    localStorage.clear();
    useVolumeStore.setState({ levels: {} });
  });

  it("narrates a session nobody has touched", () => {
    // One control: a reader who never opened the mixer still gets read to.
    expect(isNarrationEnabled("conv_1")).toBe(true);
  });

  it("goes quiet when the session is muted, and only that session", () => {
    useVolumeStore.getState().set("conv_quiet", 0);
    expect(isNarrationEnabled("conv_quiet")).toBe(false);
    expect(isNarrationEnabled("conv_other")).toBe(true);
  });

  it("keeps the mute across a reload", () => {
    useVolumeStore.getState().set("conv_quiet", 0);
    useVolumeStore.setState({ levels: {} }); // as if the page reloaded
    expect(isNarrationEnabled("conv_quiet")).toBe(false);
  });

  it("a lowered but audible level still narrates", () => {
    useVolumeStore.getState().set("conv_1", 0.2);
    expect(isNarrationEnabled("conv_1")).toBe(true);
  });
});
