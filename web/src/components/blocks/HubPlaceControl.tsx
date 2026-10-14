import { useState } from "react";
import { CheckIcon, ChevronDownIcon } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { correctHubPlace, type HubPlace } from "@/lib/hubApi";

/** The link title the hub puts on where a message for a busy lane went. */
export const HUB_PLACE_TITLE = "omnigent-hub-place";

const PLACES: { place: HubPlace; label: string; detail: string }[] = [
  { place: "steer", label: "Into its work now", detail: "a direction or correction for that task" },
  { place: "after", label: "After it finishes", detail: "a next step that needs its result" },
  { place: "new", label: "A new lane", detail: "a different task, in parallel" },
];

/** What a place link names: its placement and where it went. */
export function readPlaceLink(href: string): { id: string; picked: HubPlace } | null {
  try {
    const url = new URL(href, window.location.href);
    const id = decodeURIComponent(url.pathname.split("/").pop() ?? "");
    const picked = url.searchParams.get("picked");
    if (!id || !PLACES.some((p) => p.place === picked)) return null;
    return { id, picked: picked as HubPlace };
  } catch {
    return null;
  }
}

function rememberedAnswer(id: string): HubPlace | null {
  try {
    const kept = window.localStorage.getItem(`hub-place:${id}`);
    return PLACES.some((p) => p.place === kept) ? (kept as HubPlace) : null;
  } catch {
    return null;
  }
}

function rememberAnswer(id: string, place: HubPlace): void {
  try {
    window.localStorage.setItem(`hub-place:${id}`, place);
  } catch {
    // Only a convenience: the hub keeps the answer itself.
  }
}

/**
 * Where the hub put a message for a busy lane, beside the project's chip. A
 * click lets them say where it belonged; the hub fixes what it still can and
 * learns from it.
 */
export function HubPlaceControl({ id, picked }: { id: string; picked: HubPlace }) {
  const [answered, setAnswered] = useState<HubPlace | null>(() => rememberedAnswer(id));
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const shown = answered ?? picked;
  const label = PLACES.find((p) => p.place === shown)?.label ?? shown;

  async function answer(right: HubPlace): Promise<void> {
    setBusy(true);
    try {
      const reply = await correctHubPlace(id, right);
      setAnswered(right);
      rememberAnswer(id, right);
      setSaid(right === picked ? "Thanks, noted." : reply.text);
    } catch (error) {
      setSaid(`Couldn't tell the hub: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-1 align-middle">
      <DropdownMenu>
        <DropdownMenuTrigger
          disabled={busy}
          data-testid="hub-place-control"
          title="Where the hub put it. Click if it belonged elsewhere."
          className="inline-flex items-center gap-0.5 rounded-full border border-dashed border-border px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          {answered && answered !== picked ? `Moved: ${label.toLowerCase()}` : label}
          <ChevronDownIcon className="size-3" aria-hidden="true" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
            Where did this belong?
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {PLACES.map((p) => (
            <DropdownMenuItem key={p.place} onSelect={() => void answer(p.place)}>
              <span className="flex flex-col">
                <span className="flex items-center gap-1">
                  {p.label}
                  {p.place === picked && (
                    <span className="text-xs text-muted-foreground">(where it went)</span>
                  )}
                  {p.place === answered && <CheckIcon className="size-3" aria-hidden="true" />}
                </span>
                <span className="text-xs text-muted-foreground">{p.detail}</span>
              </span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {said && (
        <span className="text-xs text-muted-foreground" role="status">
          {said}
        </span>
      )}
    </span>
  );
}
