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

/**
 * The Worker control: which one worker the orchestrator delegates to, and the
 * model it runs. Shared by the new-conversation dialog and a running
 * conversation's Configure dialog.
 */
export function WorkerConfigRows({
  choices,
  hostId,
  worker,
  model,
  onWorkerChange,
  onModelChange,
  testIdPrefix,
}: {
  choices: WorkerChoice[];
  hostId: string | null;
  /** Declared sub-agent name; "" shows the first choice, the bundle's default. */
  worker: string;
  /** Model id; "" is the worker's own default. */
  model: string;
  onWorkerChange: (worker: string) => void;
  onModelChange: (model: string) => void;
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

  return (
    <>
      <ConfigRow label="Worker" description="Who does the work the orchestrator delegates">
        <Select
          value={selected.name}
          onValueChange={(value) => {
            onWorkerChange(value);
            // A model id belongs to one worker's harness; start the new one on its default.
            if (value !== selected.name) onModelChange("");
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
            <SelectItem value={DEFAULT_MODEL}>Default</SelectItem>
            {models.map((option) => (
              <SelectItem key={option.id} value={option.id}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </ConfigRow>
    </>
  );
}
