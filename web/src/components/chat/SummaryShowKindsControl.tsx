import { useCallback, useId, useState } from "react";
import { Switch } from "@/components/ui/switch";
import {
  CHOOSABLE_SHOW_KINDS,
  type ChoosableShowKind,
  readShowKinds,
  writeShowKinds,
} from "@/lib/summaryShowPreferences";

const LABELS: Record<ChoosableShowKind, { title: string; description: string }> = {
  table: { title: "Tables", description: "Results and comparisons, shown in full." },
  image: { title: "Images", description: "Charts and screenshots produced by the answer." },
  link: { title: "Links", description: "Sources and docs the answer cited." },
  output: { title: "Command output", description: "A few quoted lines when they are the finding." },
  code: { title: "Code", description: "Source from the answer. Off by default: usually noise." },
};

/**
 * Which parts of an answer may appear under its summary.
 *
 * The summary earns its place by being shorter than the answer, so what it
 * shows is the reader's call: source under every turn is noise to one reader
 * and the point for another. Files are absent on purpose — attaching one was
 * already the decision to show it.
 */
export function SummaryShowKindsControl() {
  const [kinds, setKinds] = useState(() => readShowKinds());
  const groupId = useId();

  const toggle = useCallback((kind: ChoosableShowKind, next: boolean) => {
    setKinds((current) => {
      const updated = { ...current, [kind]: next };
      writeShowKinds(updated);
      return updated;
    });
  }, []);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex min-w-0 flex-col">
        <span id={groupId} className="text-ui font-medium">
          Show under the summary
        </span>
        <span className="text-ui text-muted-foreground">
          Parts of the answer worth looking at are shown rather than read aloud. Files you are sent
          always appear.
        </span>
      </div>

      {CHOOSABLE_SHOW_KINDS.map((kind) => (
        <div key={kind} className="flex items-start justify-between gap-6 pl-1">
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="text-ui">{LABELS[kind].title}</span>
            <span className="text-ui text-muted-foreground">{LABELS[kind].description}</span>
          </div>
          <Switch
            checked={kinds[kind]}
            onCheckedChange={(next) => toggle(kind, next)}
            aria-label={LABELS[kind].title}
            data-testid={`summary-show-kind-${kind}`}
            className="mt-0.5 shrink-0"
            componentId={`settings.general.summary_show_${kind}`}
          />
        </div>
      ))}
    </div>
  );
}
