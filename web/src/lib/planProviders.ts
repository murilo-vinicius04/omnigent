/**
 * Plan-limit provider id -> vendor family, mirroring the server's
 * ``PLAN_PROVIDER_FAMILY``.
 *
 * The tray names Gemini's row after the client Omnigent reads quota from
 * (``antigravity``), while tokens are counted against the vendor. Mapping the
 * two keeps one hue per vendor across the pills and both charts.
 */
export const PLAN_PROVIDER_FAMILY: Record<string, string> = {
  claude: "claude",
  antigravity: "gemini",
  gemini: "gemini",
  openai: "openai",
  grok: "grok",
};
