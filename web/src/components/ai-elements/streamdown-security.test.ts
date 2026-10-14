import { describe, expect, it } from "vitest";
import {
  codeBareFilePaths,
  extractFilePathMentions,
  extractWorkspaceFileLinks,
} from "./streamdown-security";

describe("extractWorkspaceFileLinks", () => {
  it("keeps file links and drops URLs, anchors, images and repeats", () => {
    const markdown = [
      "Recorded: [clip.mp4](/home/u/ws/runs/clip.mp4) and [viewer](runs/viewer.html).",
      "Again [the clip](/home/u/ws/runs/clip.mp4), docs at [site](https://example.com/a),",
      "[top](#summary), [mail](mailto:a@b.c), ![chart](/home/u/ws/chart.png),",
      "and [query](/home/u/ws/a.html?x=1).",
    ].join("\n");

    expect(extractWorkspaceFileLinks(markdown)).toEqual([
      { label: "clip.mp4", href: "/home/u/ws/runs/clip.mp4" },
      { label: "viewer", href: "runs/viewer.html" },
    ]);
  });

  it("finds nothing in plain prose", () => {
    expect(extractWorkspaceFileLinks("No links here, just `runs/clip.mp4` in code.")).toEqual([]);
  });
});

describe("extractFilePathMentions", () => {
  it("keeps paths in inline code and in plain text, skipping code blocks, links and URLs", () => {
    const markdown = [
      "The video is done: `~/SPOT/runs/video/spot_walk.mp4`, stills in `runs/video/still_0.png`.",
      "Script: `runs/video/record_walk.py`, command `ls`, again `~/SPOT/runs/video/spot_walk.mp4`.",
      "The plan is here: /home/u/RA-L/reviews/JOYSTICK_EXPERIMENT_PLAN.md.",
      "Docs at https://example.com/docs/guide.html and [the notes](docs/notes.md).",
      "```bash",
      "xdg-open ~/SPOT/runs/video/other.mp4",
      "```",
    ].join("\n");

    expect(extractFilePathMentions(markdown)).toEqual([
      "~/SPOT/runs/video/spot_walk.mp4",
      "runs/video/still_0.png",
      "runs/video/record_walk.py",
      "/home/u/RA-L/reviews/JOYSTICK_EXPERIMENT_PLAN.md",
    ]);
  });
});

describe("codeBareFilePaths", () => {
  it("puts a path written as plain text in inline code, so it links like one in backticks", () => {
    expect(codeBareFilePaths("The plan is here:\n\n/home/u/RA-L/reviews/PLAN.md\n")).toBe(
      "The plan is here:\n\n`/home/u/RA-L/reviews/PLAN.md`\n",
    );
    expect(codeBareFilePaths("Saved to ~/runs/walk.mp4, and docs/notes.md too.")).toBe(
      "Saved to `~/runs/walk.mp4`, and `docs/notes.md` too.",
    );
  });

  it("leaves code, links, URLs and words with slashes alone", () => {
    const untouched = [
      "Already `~/runs/walk.mp4` and [plan](/home/u/plan.md) and ![c](/home/u/c.png).",
      "See https://github.com/NVlabs/GraspGenX/blob/main/README.md and <https://a.b/c.md>.",
      "Use read/write access and 3.5/4.0 scores; the /v1/sessions route.",
      "```",
      "cat /home/u/plan.md",
      "```",
    ].join("\n");
    expect(codeBareFilePaths(untouched)).toBe(untouched);
  });
});
