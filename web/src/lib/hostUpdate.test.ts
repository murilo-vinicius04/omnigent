import { describe, expect, it } from "vitest";

import type { Host } from "@/hooks/useHosts";
import { hostUpdateViews } from "./hostUpdate";

function host(overrides: Partial<Host> = {}): Host {
  return {
    host_id: "h1",
    name: "lab-pc",
    owner: "hipolito",
    status: "online",
    code: { id: "zzzz", label: "92692e5e", root: "/home/u/omnigent" },
    update: { state: "outdated", server_label: "614f4cd8+35", last: null },
    ...overrides,
  };
}

describe("hostUpdateViews", () => {
  it("offers Update for an online host on older code and names both versions", () => {
    const [view] = hostUpdateViews([host()], {});
    expect(view.force).toBe(false);
    expect(view.message).toBe("lab-pc runs older code (92692e5e; server 614f4cd8+35).");
  });

  it("tells the user to sync a host that reports no version by hand, with no button", () => {
    const [view] = hostUpdateViews(
      [host({ code: null, update: { state: "unknown", server_label: null, last: null } })],
      {},
      "https://omnigent-vm.tail0994ab.ts.net",
    );
    expect(view.force).toBeNull();
    expect(view.message).toContain("update itself");
    expect(view.command).toContain("https://omnigent-vm.tail0994ab.ts.net/v1/code/update-script");
    expect(view.command).toContain("--server https://omnigent-vm.tail0994ab.ts.net");
    expect(view.command).not.toContain("--restart");
  });

  it("asks for a restart, not the script, when the host is on the server's own machine", () => {
    const local = host({
      code: null,
      update: { state: "unknown", server_label: null, last: null, same_machine: true },
    });
    const [view] = hostUpdateViews([local], {}, "https://s");
    expect(view.command).toBeUndefined();
    expect(view.message).toContain("restart its host");
  });

  it("stays quiet for current, offline and unreported hosts", () => {
    const current = host({ update: { state: "current", server_label: "x", last: null } });
    expect(
      hostUpdateViews([current, host({ status: "offline" }), host({ update: null })], {}),
    ).toEqual([]);
  });

  it("shows progress after the user pressed Update, without a button", () => {
    const [view] = hostUpdateViews([host()], { h1: 1_000 });
    expect(view).toMatchObject({ message: "Updating lab-pc…", force: null });
  });

  it("offers Update anyway when the host was busy with the current attempt", () => {
    const last = { status: "busy", message: "2 runner(s) are alive", restarting: false, at: 2 };
    const [view] = hostUpdateViews(
      [host({ update: { state: "outdated", server_label: "s", last } })],
      {
        h1: 1_000,
      },
    );
    expect(view).toMatchObject({ force: true });
    expect(view.message).toContain("2 runner(s) are alive");
  });

  it("reports a failure of the current attempt and ignores an older result", () => {
    const failed = { status: "failed", message: "uv sync failed", restarting: false, at: 5 };
    const withLast = (last: typeof failed) =>
      host({ update: { state: "outdated", server_label: "s", last } });

    expect(hostUpdateViews([withLast(failed)], { h1: 1_000 })[0].message).toContain(
      "uv sync failed",
    );
    expect(hostUpdateViews([withLast({ ...failed, at: 0.5 })], { h1: 1_000 })[0].message).toBe(
      "Updating lab-pc…",
    );
  });
});
