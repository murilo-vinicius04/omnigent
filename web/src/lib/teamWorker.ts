/**
 * The single worker an orchestrator (nexus) delegates to, picked by a person.
 * Stored as session labels so it can change mid-conversation; the runner
 * reads them on every delegation. See `omnigent/team_worker.py`.
 */
export const WORKER_LABEL = "team.worker";
export const WORKER_MODEL_LABEL = "team.worker_model";
export const WORKER_EFFORT_LABEL = "team.worker_effort";

/** One worker the Worker control offers, as `GET /v1/agents` reports it. */
export interface WorkerChoice {
  name: string;
  label: string;
  harness: string | null;
  /** Models to offer when the host cannot list the harness's catalog. */
  models: string[];
  /** Reasoning efforts to offer; empty hides the effort row. */
  efforts: string[];
  /** What the worker runs when nothing is picked, shown on the Default item. */
  defaultModel: string | null;
  defaultEffort: string | null;
}

/** One worker choice as `GET /v1/agents` sends it. */
export interface WorkerChoiceWire {
  name: string;
  label?: string;
  harness?: string | null;
  models?: string[];
  efforts?: string[];
  default_model?: string | null;
  default_effort?: string | null;
}

/** Normalize the wire rows; absent on agents that do not opt in. */
export function workerChoicesFromWire(rows: WorkerChoiceWire[] | undefined): WorkerChoice[] {
  return (rows ?? []).map((row) => ({
    name: row.name,
    label: row.label || row.name,
    harness: row.harness ?? null,
    models: row.models ?? [],
    efforts: row.efforts ?? [],
    defaultModel: row.default_model ?? null,
    defaultEffort: row.default_effort ?? null,
  }));
}

/**
 * What a running conversation's Configure dialog needs for the Worker control.
 * Built by the chat page, which owns the session queries, and passed down so the
 * composer itself stays free of data fetching.
 */
export interface WorkerControl {
  choices: WorkerChoice[];
  /** The stored pick; "" when nothing was picked. */
  worker: string;
  model: string;
  effort: string;
  hostId: string | null;
  onSave: (worker: string, model: string, effort: string) => Promise<void>;
}
