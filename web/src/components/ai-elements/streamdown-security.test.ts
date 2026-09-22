import { describe, expect, it } from "vitest";
import { extractViewableCodePaths, extractWorkspaceFileLinks } from "./streamdown-security";

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

describe("extractViewableCodePaths", () => {
  it("keeps viewable files named in inline code, skipping code blocks and source files", () => {
    const markdown = [
      "The video is done: `~/SPOT/runs/video/spot_walk.mp4`, stills in `runs/video/still_0.png`.",
      "Script: `runs/video/record_walk.py`, command `ls`, again `~/SPOT/runs/video/spot_walk.mp4`.",
      "```bash",
      "xdg-open ~/SPOT/runs/video/other.mp4",
      "```",
    ].join("\n");

    expect(extractViewableCodePaths(markdown)).toEqual([
      "~/SPOT/runs/video/spot_walk.mp4",
      "runs/video/still_0.png",
    ]);
  });
});
