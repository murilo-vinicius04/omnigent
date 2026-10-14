import { cjk } from "@streamdown/cjk";
import { createMathPlugin } from "@streamdown/math";
import { mermaid } from "@streamdown/mermaid";
import { defaultRehypePlugins, type LinkSafetyConfig, type StreamdownProps } from "streamdown";
import { lazyCodePlugin } from "./lazyCodePlugin";

type StreamdownRehypePlugins = NonNullable<StreamdownProps["rehypePlugins"]>;
type StreamdownRehypePlugin = StreamdownRehypePlugins[number];
type StreamdownPluginTuple = Extract<StreamdownRehypePlugin, readonly unknown[]>;
type StreamdownHardenOptions = {
  allowedImagePrefixes: string[];
  allowedLinkPrefixes?: string[];
  allowedProtocols?: string[];
  allowDataImages?: boolean;
  defaultOrigin?: string;
};
type StreamdownHardenPlugin = StreamdownPluginTuple & {
  1: StreamdownHardenOptions;
};

export const STREAMDOWN_PLUGINS = {
  cjk,
  code: lazyCodePlugin,
  // Only `$$…$$` opens math. A single `$` is prose far more often than it is a
  // math delimiter — currency ($5), rates ($/PR, $/session), shell variables
  // ($LLM_API_KEY) — and single-dollar math pairs any two of them up, rendering
  // the whole span between them as letter-by-letter math soup. Explicit TeX
  // delimiters (`\(…\)`, `\[…\]`), which is what LLMs emit for real math, are
  // rewritten to `$$…$$` by `normalizeExplicitMathDelimiters`.
  math: createMathPlugin({ singleDollarTextMath: false }),
  mermaid,
};
export const SECURE_STREAMDOWN_REHYPE_PLUGINS = createStreamdownRehypePlugins(false);
export const FILE_LINK_STREAMDOWN_REHYPE_PLUGINS = createStreamdownRehypePlugins(true);

// Attribute carrying the original, unhardened href of a workspace-file link.
export const WORKSPACE_FILE_LINK_ATTR = "data-omnigent-file";

// An href that can only be a protocol URL, a protocol-relative URL, or an
// in-page anchor, never a path to a file in the session workspace. The
// lookahead keeps a cited position off the scheme branch: `notes.md:12` is a
// filename plus a line number, but is otherwise shaped exactly like a scheme.
const NON_FILE_HREF = /^(?:[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d+(?::\d+)?$)|\/\/|#)/;

/** A workspace-file link as written in the markdown source. */
export interface WorkspaceFileLinkSource {
  label: string;
  href: string;
}

/**
 * The file links in *markdown*, by the same rule as {@link markWorkspaceFileLinks},
 * first occurrence of each path only. The friendly view shows a rewrite in place
 * of the answer, so a video or page the answer linked would otherwise sit behind
 * "Show original".
 */
export function extractWorkspaceFileLinks(markdown: string): WorkspaceFileLinkSource[] {
  const links: WorkspaceFileLinkSource[] = [];
  const seen = new Set<string>();
  for (const match of markdown.matchAll(/(?<!!)\[([^\]\n]+)\]\(([^)\s]+)\)/g)) {
    const [, label, href] = match;
    if (NON_FILE_HREF.test(href) || href.includes("?") || href.includes("#")) continue;
    if (seen.has(href)) continue;
    seen.add(href);
    links.push({ label, href });
  }
  return links;
}

// A file path written as plain text, not in a link or code: absolute, under
// home, or relative with a folder, ending in a file name with an extension.
const BARE_PATH =
  /(?<![\w/.~`[(<:@-])(~?\/?(?:[\w.@+-]+\/)+[\w@+-][\w.@+-]*\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w/])/g;

// What a line's bare paths are looked for outside of: links, code spans,
// autolinks and URLs.
const NOT_BARE = /!?\[[^\]\n]*\]\([^)\n]*\)|`[^`\n]+`|<[^>\n]+>|[a-zA-Z][\w+.-]*:\/\/\S+/g;

const FENCE = /^\s*(```|~~~)/;

/** Each line of *markdown* outside code fences, with a callback that may rewrite it. */
function mapProseLines(markdown: string, rewrite: (line: string) => string): string {
  let fenced = false;
  return markdown
    .split("\n")
    .map((line) => {
      if (FENCE.test(line)) {
        fenced = !fenced;
        return line;
      }
      return fenced ? line : rewrite(line);
    })
    .join("\n");
}

/** *line* with its bare paths passed through *each*, links, code and URLs untouched. */
function mapBarePaths(line: string, each: (path: string) => string): string {
  let out = "";
  let last = 0;
  for (const kept of line.matchAll(NOT_BARE)) {
    const start = kept.index ?? 0;
    out += line.slice(last, start).replace(BARE_PATH, (_, path: string) => each(path));
    out += kept[0];
    last = start + kept[0].length;
  }
  return out + line.slice(last).replace(BARE_PATH, (_, path: string) => each(path));
}

/**
 * *markdown* with each file path written as plain text put in inline code, so
 * it renders like a path the agent wrote as `` `~/runs/plan.md` ``: a link to
 * the file when it is one in the workspace. Agents in a new chat often write
 * `/home/u/ws/plan.md` bare, which would otherwise be dead text.
 */
export function codeBareFilePaths(markdown: string): string {
  if (!markdown.includes("/")) return markdown;
  return mapProseLines(markdown, (line) => mapBarePaths(line, (path) => `\`${path}\``));
}

/**
 * Every file path *markdown* names outside code blocks, in order, first
 * occurrence only: inline-code paths with a folder in them and paths written
 * as plain text. The friendly view lists the ones that are workspace files,
 * since its rewrite of the answer has no paths in it.
 */
export function extractFilePathMentions(markdown: string): string[] {
  const paths: string[] = [];
  const add = (path: string) => {
    if (!paths.includes(path)) paths.push(path);
    return path;
  };
  mapProseLines(markdown, (line) => {
    for (const match of line.matchAll(/(?<!`)`([^`\s]+)`(?!`)/g)) {
      if (match[1].includes("/")) add(match[1]);
    }
    return mapBarePaths(line, add);
  });
  return paths;
}

// Where a file link's href is parked once the path moves to the data
// attribute. Must be a *named* fragment: harden passes a fragment-only href
// through only when `new URL(href, base).hash` round-trips, and a bare "#"
// parses to an empty hash, so it would be blocked like any unresolvable URL.
const PARKED_FILE_HREF = "#omnigent-file";

interface HastElement {
  type: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastElement[];
}

// Streamdown enables a link-safety confirmation modal by default: clicking any
// markdown link pops an "Open external link?" dialog instead of following the
// link. Disable it so chat links behave like ordinary links — a plain click
// opens them and cmd/ctrl-click opens a new tab. With safety off Streamdown
// still renders the anchor as `<a target="_blank" rel="noreferrer">`, so we
// keep new-tab opening plus referrer/reverse-tabnabbing protection without the
// extra confirmation click.
export const CHAT_LINK_SAFETY: LinkSafetyConfig = { enabled: false };

/** Rewrites safe local `file:` URIs before sanitization removes their hrefs. */
export function rewriteFileUriLinks() {
  return (tree: HastElement) => {
    visitElements(tree, (node) => {
      if (node.tagName !== "a") return;
      const href = node.properties?.href;
      if (typeof href !== "string" || !/^file:/i.test(href)) return;
      const path = fileUriToLocalPath(href);
      if (!path) return;
      node.properties = { ...node.properties, href: path };
    });
  };
}

/** Returns a local absolute path only when rewriting preserves href meaning. */
function fileUriToLocalPath(href: string): string | null {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  if (url.protocol !== "file:" || url.hostname || url.search || url.hash) return null;
  let path: string;
  try {
    path = decodeURIComponent(url.pathname);
  } catch {
    return null;
  }
  // Keep the rewritten href absolute without introducing URL delimiters.
  if (!path.startsWith("/") || path.startsWith("//") || path === "/" || /[?#]/.test(path)) {
    return null;
  }
  return path;
}

/**
 * Moves the href of every file-path link onto {@link WORKSPACE_FILE_LINK_ATTR}
 * and parks the href on an inert fragment.
 *
 * Agents routinely link a file they just wrote (`[foo.md](/abs/ws/foo.md)`).
 * Left alone, Streamdown's harden pass either keeps such a link as a real
 * anchor (so clicking it navigates the app origin and 404s), or, for a path
 * with no leading slash, strips the href and appends " [blocked]", which
 * reads as though the app censored the link. Both are wrong: the link
 * names a workspace file and should open the FileViewer.
 *
 * Running before harden hands the path to a renderer that owns the click
 * instead. A fragment href is chosen because harden passes those through
 * untouched, while an anchor with *no* href is blocked like any other
 * unresolvable URL. Only hrefs that could name a real file are moved: a URL,
 * a `mailto:`/`javascript:` scheme, or anything carrying a query or fragment
 * is left for harden to judge exactly as before.
 */
export function markWorkspaceFileLinks() {
  return (tree: HastElement) => {
    visitElements(tree, (node) => {
      if (node.tagName !== "a") return;
      const href = node.properties?.href;
      if (typeof href !== "string" || !href) return;
      if (NON_FILE_HREF.test(href) || href.includes("?") || href.includes("#")) return;
      node.properties = {
        ...node.properties,
        href: PARKED_FILE_HREF,
        [WORKSPACE_FILE_LINK_ATTR]: href,
      };
    });
  };
}

function visitElements(node: HastElement, visitor: (node: HastElement) => void): void {
  if (node.type === "element") visitor(node);
  for (const child of node.children ?? []) {
    visitElements(child, visitor);
  }
}

function isStreamdownHardenPlugin(
  plugin: StreamdownRehypePlugin,
): plugin is StreamdownHardenPlugin {
  return Array.isArray(plugin) && plugin.length >= 2 && isHardenOptions(plugin[1]);
}

function isHardenOptions(value: unknown): value is StreamdownHardenOptions {
  return (
    typeof value === "object" &&
    value !== null &&
    "allowedImagePrefixes" in value &&
    Array.isArray(value.allowedImagePrefixes)
  );
}

function createStreamdownRehypePlugins(markFileLinks: boolean): StreamdownRehypePlugins {
  const plugins: StreamdownRehypePlugins = [];
  let sawSanitize = false;

  for (const [key, plugin] of Object.entries(defaultRehypePlugins)) {
    // Preserve local file URIs before sanitize removes their href.
    if (key === "sanitize") {
      sawSanitize = true;
      if (markFileLinks) plugins.push(rewriteFileUriLinks);
    }

    if (key !== "harden") {
      plugins.push(plugin);
      continue;
    }

    // Immediately before harden, and so after Streamdown's `sanitize` step,
    // which drops the marker attribute as unknown. Sanitize has also already
    // stripped any dangerous href, leaving nothing to hand over for such a link.
    if (markFileLinks) {
      plugins.push(markWorkspaceFileLinks);
    }

    if (!isStreamdownHardenPlugin(plugin)) {
      throw new Error("Streamdown harden plugin must be a [plugin, options] tuple");
    }

    plugins.push([
      plugin[0],
      { ...plugin[1], allowedImagePrefixes: [] },
    ] satisfies StreamdownPluginTuple);
  }

  if (markFileLinks && !sawSanitize) {
    throw new Error("Streamdown default rehype plugins carry no sanitize step");
  }

  return plugins;
}
