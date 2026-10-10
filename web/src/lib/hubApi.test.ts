import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock("@/lib/identity", () => ({ authenticatedFetch: h.fetch }));

import { openHub } from "@/lib/hubApi";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("openHub", () => {
  beforeEach(() => h.fetch.mockReset());

  it("returns the hub conversation's id", async () => {
    h.fetch.mockResolvedValueOnce(json({ session_id: "conv_hub", sessions: [] }));
    await expect(openHub()).resolves.toBe("conv_hub");
    expect(h.fetch).toHaveBeenCalledWith("/v1/hub");
  });

  it("says why it could not", async () => {
    h.fetch.mockResolvedValueOnce(json({ detail: "hub unavailable" }, 503));
    await expect(openHub()).rejects.toThrow("hub unavailable");
  });
});
