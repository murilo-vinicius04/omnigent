/**
 * Muse model picker options for the `muse-code` ACP harness.
 *
/**
 * Muse model picker options for the `muse-code` ACP harness.
 *
 * Static on purpose: the adapter only publishes its model catalog after a
 * session starts (the `/models` refresh inside Muse), so there is no host
 * listing to read here. Ids are Meta's published model ids (Wonder ships in
 * Muse Code on every plan; Wonder Full needs a Power / Maximum
 * subscription). The adapter also accepts a manual id outside this list (it
 * validates before a turn) — this is the common set, not a closed
 * vocabulary, and it needs a refresh when Meta ships a new model.
 *
 * Note: `-contributor` suffixed ids are deliberately absent. Contributor is
 * not a model in Muse Code — it is a separate host-side toggle (already on
 * for this host) — and the host refuses a turn addressed to one.
 *
 * Lives in a leaf module (no React / store imports) so both the picker UI
 * and the store can read it without a circular import.
 */
export const MUSE_CODE_MODELS = [
  { id: "muse-wonder-1.0", label: "Muse Wonder" },
  { id: "muse-wonder-full-1.0", label: "Muse Wonder Full" },
  { id: "muse-spark-1.3", label: "Muse Spark 1.3" },
  { id: "muse-spark-1.2", label: "Muse Spark 1.2" },
] as const;
