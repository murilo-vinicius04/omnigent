import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";

import { useSessionAgent } from "@/hooks/useAgents";
import { useSession } from "@/hooks/useSession";
import { updateSession } from "@/lib/sessionsApi";
import {
  WORKER_EFFORT_LABEL,
  WORKER_LABEL,
  WORKER_MODEL_LABEL,
  type WorkerControl,
} from "@/lib/teamWorker";

/**
 * The Worker control for a running conversation: the choices its agent offers,
 * the stored pick, and a save that writes the labels the runner reads on every
 * delegation. `null` for any agent without worker choices.
 */
export function useWorkerControl(sessionId: string | null | undefined): WorkerControl | null {
  const queryClient = useQueryClient();
  const id = sessionId ?? null;
  const { data: agent } = useSessionAgent(id);
  const { session } = useSession(id);
  const choices = agent?.worker_choices;
  const worker = session?.labels?.[WORKER_LABEL] ?? "";
  const model = session?.labels?.[WORKER_MODEL_LABEL] ?? "";
  const effort = session?.labels?.[WORKER_EFFORT_LABEL] ?? "";
  const hostId = session?.hostId ?? null;

  const onSave = useCallback(
    async (nextWorker: string, nextModel: string, nextEffort: string) => {
      if (!id) return;
      await updateSession(id, {
        labels: {
          [WORKER_LABEL]: nextWorker,
          [WORKER_MODEL_LABEL]: nextModel,
          [WORKER_EFFORT_LABEL]: nextEffort,
        },
      });
      await queryClient.invalidateQueries({ queryKey: ["session", id] });
    },
    [id, queryClient],
  );

  return useMemo(
    () =>
      choices && choices.length > 0 ? { choices, worker, model, effort, hostId, onSave } : null,
    [choices, worker, model, effort, hostId, onSave],
  );
}
