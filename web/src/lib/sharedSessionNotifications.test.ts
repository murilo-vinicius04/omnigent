import { beforeEach, describe, expect, it } from "vitest";
import {
  filterNotifiableSessions,
  isOwnedByViewer,
  readNotifySharedSessions,
  writeNotifySharedSessions,
} from "./sharedSessionNotifications";

beforeEach(() => {
  window.localStorage.clear();
});

describe("shared-session notification preference", () => {
  it("defaults to owned sessions only", () => {
    expect(readNotifySharedSessions()).toBe(false);
  });

  it("round-trips the opt-in and back", () => {
    writeNotifySharedSessions(true);
    expect(readNotifySharedSessions()).toBe(true);
    writeNotifySharedSessions(false);
    expect(readNotifySharedSessions()).toBe(false);
  });

  it("treats a corrupt value as owned-only", () => {
    window.localStorage.setItem("omnigent:notify-shared-sessions", "yes");
    expect(readNotifySharedSessions()).toBe(false);
  });
});

describe("isOwnedByViewer", () => {
  it("reads a null owner as owned", () => {
    expect(isOwnedByViewer({ owner: null }, "me@example.com")).toBe(true);
    expect(isOwnedByViewer({}, null)).toBe(true);
  });

  it("matches the owner against the viewer", () => {
    expect(isOwnedByViewer({ owner: "me@example.com" }, "me@example.com")).toBe(true);
    expect(isOwnedByViewer({ owner: "hipolito" }, "me@example.com")).toBe(false);
  });

  it("owns nothing foreign while identity is unresolved", () => {
    expect(isOwnedByViewer({ owner: "hipolito" }, null)).toBe(false);
  });
});

describe("filterNotifiableSessions", () => {
  const mine = { id: "a", owner: "me@example.com" };
  const shared = { id: "b", owner: "hipolito" };
  const legacy = { id: "c", owner: null };

  it("drops shared rows by default", () => {
    expect(filterNotifiableSessions([mine, shared, legacy], "me@example.com", false)).toEqual([
      mine,
      legacy,
    ]);
  });

  it("passes everything through once opted in", () => {
    expect(filterNotifiableSessions([mine, shared, legacy], "me@example.com", true)).toEqual([
      mine,
      shared,
      legacy,
    ]);
  });
});
