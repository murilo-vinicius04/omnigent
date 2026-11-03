import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Host } from "@/hooks/useHosts";
import { HostUpdateNotice } from "./HostUpdateNotice";

const mocks = vi.hoisted(() => ({
  warning: vi.fn(),
  loading: vi.fn(),
  error: vi.fn(),
  dismiss: vi.fn(),
  start: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), {
    warning: mocks.warning,
    loading: mocks.loading,
    error: mocks.error,
    dismiss: mocks.dismiss,
  }),
}));

let hosts: Host[] = [];
vi.mock("@/hooks/useHosts", () => ({ useHosts: () => ({ data: hosts }) }));
vi.mock("@/hooks/useHostUpdate", () => ({
  useHostUpdate: () => ({ start: mocks.start, startedAt: {}, error: "" }),
}));

const outdated: Host = {
  host_id: "h1",
  name: "lab-pc",
  owner: "hipolito",
  status: "online",
  code: { id: "zzzz", label: "92692e5e", root: "/r" },
  update: { state: "outdated", server_label: "614f4cd8+35", last: null },
};
const tooOld: Host = {
  ...outdated,
  code: null,
  update: { state: "unknown", server_label: null, last: null },
};
const event = { preventDefault: vi.fn() };

beforeEach(() => sessionStorage.clear());
afterEach(() => {
  cleanup();
  Object.values(mocks).forEach((m) => m.mockReset());
});

describe("HostUpdateNotice", () => {
  it("shows nothing when every machine runs the server's code", () => {
    hosts = [{ ...outdated, update: { state: "current", server_label: "x", last: null } }];
    render(<HostUpdateNotice />);
    expect(mocks.warning).not.toHaveBeenCalled();
    expect(mocks.loading).not.toHaveBeenCalled();
  });

  it("raises a toast for an outdated machine whose Update button starts the update", () => {
    hosts = [outdated];
    render(<HostUpdateNotice />);

    const [message, options] = mocks.warning.mock.calls[0];
    expect(message).toContain("lab-pc runs older code");
    expect(options).toMatchObject({ id: "host-update:h1", duration: Number.POSITIVE_INFINITY });
    expect(options.action.label).toBe("Update");

    options.action.onClick(event);

    expect(event.preventDefault).toHaveBeenCalled();
    expect(mocks.start).toHaveBeenCalledWith("h1", false);
  });

  it("offers to copy the command for a machine too old to update itself", () => {
    hosts = [tooOld];
    render(<HostUpdateNotice />);

    const [, options] = mocks.warning.mock.calls[0];
    expect(options.action.label).toBe("Copy command");
  });

  it("remembers a dismissed notice and clears its toast", () => {
    hosts = [tooOld];
    const first = render(<HostUpdateNotice />);
    mocks.warning.mock.calls[0][1].cancel.onClick();
    first.rerender(<HostUpdateNotice />);
    expect(mocks.dismiss).toHaveBeenCalledWith("host-update:h1");

    mocks.warning.mockReset();
    first.unmount();
    render(<HostUpdateNotice />);
    expect(mocks.warning).not.toHaveBeenCalled();
  });
});
