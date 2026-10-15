// ACP (Grok / Devin / acp:<slug>) permission stances. Distinct from Claude
// Code's shift+tab cycle: ACP only has "ask" vs "don't ask".

const ACP_PERMISSION_MODE_LABEL_KEY = "omnigent.acp.permission_mode";

const ACP_PERMISSION_HARNESSES = new Set(["grok", "grok-build", "devin", "acp"]);

export interface AcpPermissionModeOption {
  value: string;
  label: string;
  description: string;
}

export const ACP_PERMISSION_MODES: AcpPermissionModeOption[] = [
  {
    value: "auto",
    label: "Ask",
    description: "Prompts before tools Grok considers risky",
  },
  {
    value: "bypassPermissions",
    label: "Don't ask",
    description: "Runs tools without prompting; policy still blocks the dangerous set",
  },
];

export const ACP_DEFAULT_PERMISSION_MODE = "auto";

/** Whether this session runs an ACP harness that honors live permission_mode. */
export function isAcpPermissionSession(
  session: { harness?: string | null } | null | undefined,
): boolean {
  const harness = session?.harness;
  if (!harness) return false;
  return ACP_PERMISSION_HARNESSES.has(harness) || harness.startsWith("acp:");
}

export function acpPermissionModeLabel(mode: string | null | undefined): string {
  if (!mode) return "";
  return ACP_PERMISSION_MODES.find((m) => m.value === mode)?.label ?? mode;
}

/**
 * The persisted ACP permission stance, or null when the session has never
 * been switched. Callers treat null as ``auto`` for ACP sessions.
 */
export function acpPermissionModeFromSession(
  session: { labels?: Record<string, string | null> | null } | null | undefined,
): string | null {
  const labelled = session?.labels?.[ACP_PERMISSION_MODE_LABEL_KEY];
  if (typeof labelled === "string" && labelled) return labelled;
  return null;
}
