import { describe, expect, it } from "vitest";
import { isAcpPermissionSession } from "./acpPermissionMode";

describe("isAcpPermissionSession", () => {
  it("covers the Muse Code ACP harness and its alias", () => {
    expect(isAcpPermissionSession({ harness: "muse-code" })).toBe(true);
    expect(isAcpPermissionSession({ harness: "muse" })).toBe(true);
  });

  it("keeps covering the other ACP harnesses", () => {
    expect(isAcpPermissionSession({ harness: "grok" })).toBe(true);
    expect(isAcpPermissionSession({ harness: "devin" })).toBe(true);
    expect(isAcpPermissionSession({ harness: "acp:openclaw" })).toBe(true);
  });

  it("rejects native harnesses and empty sessions", () => {
    expect(isAcpPermissionSession({ harness: "claude-native" })).toBe(false);
    expect(isAcpPermissionSession({ harness: null })).toBe(false);
    expect(isAcpPermissionSession(null)).toBe(false);
  });
});
