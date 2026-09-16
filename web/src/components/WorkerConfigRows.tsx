import { ConfigRow } from "@/components/HarnessConfigControls";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useHostModelOptions } from "@/hooks/useHosts";
import type { WorkerChoice } from "@/lib/teamWorker";

// Radix rejects "" as an item value, so "the worker's own default" needs a sentinel.
const DEFAULT_MODEL = "__default__";
const DEFAULT_EFFORT = "__default__";

function defaultLabel(value: string | null): string {
  return value ? `Default (${value})` : "Default";
}

/**
 * The Worker control: which one worker the orchestrator delegates to, and the
 * model and reasoning effort it runs. Shared by the new-conversation dialog and a running
 * conversation's Configure dialog.
 */
export function WorkerConfigRows({
  choices,
  hostId,
  worker,
  model,
  effort,
  onWorkerChange,
  onModelChange,
  onEffortChange,
  testIdPrefix,
}: {
  choices: WorkerChoice[];
  hostId: string | null;
  /** Declared sub-agent name; "" shows the first choice, the bundle's default. */
  worker: string;
  /** Model id; "" is the worker's own default. */
  model: string;
  /** Effort level; "" is the worker's own default. */
  effort: string;
  onWorkerChange: (worker: string) => void;
  onModelChange: (model: string) => void;
  onEffortChange: (effort: string) => void;
  testIdPrefix: string;
}) {
  const selected = choices.find((choice) => choice.name === worker) ?? choices[0];
  // A worker that names its own models (Codex, on the free OpenAI pool) skips
  // the host catalog; the others list what their harness can run here.
  const askHost = selected !== undefined && selected.models.length === 0 && !!selected.harness;
  const { data: hostModels } = useHostModelOptions(hostId, selected?.harness ?? "", askHost);
  if (!selected) return null;

  const models =
    selected.models.length > 0
      ? selected.models.map((id) => ({ id, label: id }))
      : (hostModels ?? []).map((option) => ({ id: option.id, label: option.displayName || option.id }));
  // Keep a stored pick visible even when the list no longer carries it.
  if (model && !models.some((option) => option.id === model)) {
    models.push({ id: model, label: model });
  }
  const efforts = [...selected.efforts];
  if (effort && !efforts.includes(effort)) efforts.push(effort);

  return (
    <>
      <ConfigRow label="Worker" description="Who does the work the orchestrator delegates">
        <Select
          value={selected.name}
          onValueChange={(value) => {
            onWorkerChange(value);
            // A model id belongs to one worker's harness; start the new one on its defaults.
            if (value !== selected.name) {
              onModelChange("");
              onEffortChange("");
            }
          }}
          componentId={`${testIdPrefix}.worker`}
          valueHasNoPii
        >
          <SelectTrigger className="w-full" data-testid={`${testIdPrefix}-worker`} aria-label="Worker">
            <SelectValue />
          </SelectTrigger>
          <SelectContent position="popper" align="start">
            {choices.map((choice) => (
              <SelectItem key={choice.name} value={choice.name}>
                {choice.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </ConfigRow>
      <ConfigRow label="Worker model" description={`The model ${selected.label} runs`}>
        <Select
          value={model || DEFAULT_MODEL}
          onValueChange={(value) => onModelChange(value === DEFAULT_MODEL ? "" : value)}
          componentId={`${testIdPrefix}.worker_model`}
          valueHasNoPii
        >
          <SelectTrigger
            className="w-full"
            data-testid={`${testIdPrefix}-worker-model`}
            aria-label="Worker model"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent position="popper" align="start">
            <SelectItem value={DEFAULT_MODEL}>{defaultLabel(selected.defaultModel)}</SelectItem>
            {models.map((option) => (
              <SelectItem key={option.id} value={option.id}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </ConfigRow>
      {selected.efforts.length > 0 && (
        <ConfigRow label="Worker effort" description={`How hard ${selected.label} reasons`}>
          <Select
            value={effort || DEFAULT_EFFORT}
            onValueChange={(value) => onEffortChange(value === DEFAULT_EFFORT ? "" : value)}
            componentId={`${testIdPrefix}.worker_effort`}
            valueHasNoPii
          >
            <SelectTrigger
              className="w-full"
              data-testid={`${testIdPrefix}-worker-effort`}
              aria-label="Worker effort"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent position="popper" align="start">
              <SelectItem value={DEFAULT_EFFORT}>{defaultLabel(selected.defaultEffort)}</SelectItem>
              {efforts.map((level) => (
                <SelectItem key={level} value={level}>
                  {level}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </ConfigRow>
      )}
    </>
  );
}
