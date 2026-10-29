import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { HubMachineDialog } from "./HubMachineDialog";

vi.mock("@/hooks/useHosts", () => ({
  useHosts: () => ({
    data: [
      { host_id: "local", name: "Nexus", status: "online" },
      { host_id: "remote", name: "Hipólito", status: "online" },
      { host_id: "asleep", name: "Offline machine", status: "offline" },
    ],
  }),
}));
afterEach(cleanup);

it("requires a machine, resets the folder when changing hosts and sends the selected destination", async () => {
  const save = vi.fn().mockResolvedValue(undefined);
  const close = vi.fn();
  render(<HubMachineDialog title="Machine" onClose={close} onSave={save} />);
  const choose = screen.getByRole("button", { name: "Choose machine" });
  expect(choose).toBeDisabled();
  fireEvent.change(screen.getByRole("combobox", { name: "Machine" }), {
    target: { value: "local" },
  });
  fireEvent.change(screen.getByLabelText("Folder on this machine"), {
    target: { value: "/home/nexus" },
  });
  fireEvent.change(screen.getByRole("combobox", { name: "Machine" }), {
    target: { value: "remote" },
  });
  expect(screen.getByLabelText("Folder on this machine")).toHaveValue("");
  expect(choose).toBeDisabled();
  expect(screen.getByRole("option", { name: "Offline machine (offline)" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Folder on this machine"), {
    target: { value: "/home/murilo" },
  });
  fireEvent.click(choose);
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith({ host_id: "remote", workspace: "/home/murilo" }),
  );
  await waitFor(() => expect(close).toHaveBeenCalled());
});

it("keeps the dialog open when a remote machine refuses the request", async () => {
  const save = vi.fn().mockRejectedValue(new Error("machine offline"));
  const close = vi.fn();
  render(
    <HubMachineDialog
      title="Terminal"
      optionalFolder
      onClose={close}
      onSave={save}
      initial={{ host_id: "remote", workspace: "" }}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Choose machine" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("machine offline");
  expect(close).not.toHaveBeenCalled();
});
