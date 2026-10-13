/** The words for a hub tool while it runs, as the call and the status pill show it. */
export function describeHubTool(tool: string, project: string): string {
  const where = project || "a project";
  if (tool === "session_status" || tool === "project_status") {
    return project === "all" ? "Looking at every project" : `Looking at ${where}`;
  }
  if (tool === "ask_claude") return `Sending to ${where}'s Claude`;
  if (tool === "check_machine") return `Checking on ${where} (read-only)`;
  return `${tool} — ${where}`;
}
