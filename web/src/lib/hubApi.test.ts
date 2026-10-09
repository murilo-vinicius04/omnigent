import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock("@/lib/identity", () => ({ authenticatedFetch: h.fetch }));

import { hubIsThinking, mergeEntries, useHubStore, type HubEntry } from "@/lib/hubApi";

function entry(
  id: number,
  role: "you" | "hub" = "hub",
  kind: HubEntry["kind"] = "message",
): HubEntry {
  return {
    id,
    at: id,
    role,
    kind,
    text: `e${id}`,
    via: null,
    session_id: null,
    session_name: null,
    detail: null,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("hub store", () => {
  beforeEach(() => {
    h.fetch.mockReset();
    useHubStore.setState({
      entries: [],
      sessions: [],
      lastId: 0,
      loaded: false,
      error: null,
      sending: false,
    });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("merges entries once each, in order", () => {
    const merged = mergeEntries([entry(1), entry(3)], [entry(2), entry(3)]);
    expect(merged.map((e) => e.id)).toEqual([1, 2, 3]);
  });

  it("is thinking while the last line is theirs", () => {
    expect(hubIsThinking([entry(1, "you")])).toBe(true);
    expect(hubIsThinking([entry(1, "you"), entry(2)])).toBe(false);
    expect(hubIsThinking([])).toBe(false);
  });

  it("loads the log and the sessions", async () => {
    h.fetch.mockResolvedValueOnce(
      json({
        entries: [entry(1, "you"), entry(2)],
        last_id: 2,
        sessions: [{ id: "s1", name: "amarras", working: true }],
      }),
    );
    await useHubStore.getState().load();
    const state = useHubStore.getState();
    expect(h.fetch).toHaveBeenCalledWith("/v1/hub");
    expect(state.entries.map((e) => e.id)).toEqual([1, 2]);
    expect(state.lastId).toBe(2);
    expect(state.sessions[0]?.name).toBe("amarras");
    expect(state.loaded).toBe(true);
  });

  it("posts what they typed and shows it at once", async () => {
    h.fetch.mockResolvedValueOnce(json(entry(5, "you"), 202));
    await useHubStore.getState().send("  manda pra brasa  ");
    const [url, init] = h.fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/hub/messages");
    expect(JSON.parse(String(init.body))).toEqual({ text: "manda pra brasa" });
    expect(useHubStore.getState().entries.map((e) => e.id)).toEqual([5]);
    expect(useHubStore.getState().lastId).toBe(5);
  });

  it("keeps the error when a send fails", async () => {
    h.fetch.mockResolvedValueOnce(json({ detail: "hub unavailable" }, 503));
    await expect(useHubStore.getState().send("oi")).rejects.toThrow("hub unavailable");
    expect(useHubStore.getState().error).toBe("hub unavailable");
    expect(useHubStore.getState().sending).toBe(false);
  });

  it("follows new entries after the last one it has", async () => {
    useHubStore.setState({ lastId: 4 });
    let calls = 0;
    h.fetch.mockImplementation(async (url: string) => {
      if (url.startsWith("/v1/hub/entries")) {
        calls += 1;
        if (calls === 1) return json({ entries: [entry(5, "hub", "finished")], last_id: 5 });
        return new Promise(() => {}); // the next long-poll just waits
      }
      return json({ sessions: [] });
    });
    const stop = useHubStore.getState().follow();
    await vi.waitFor(() => expect(useHubStore.getState().entries.map((e) => e.id)).toEqual([5]));
    expect(h.fetch.mock.calls[0]?.[0]).toBe("/v1/hub/entries?after=4&wait=25");
    // A finished report refreshes what the sessions are doing.
    await vi.waitFor(() => expect(h.fetch).toHaveBeenCalledWith("/v1/hub/sessions"));
    stop();
  });
});
