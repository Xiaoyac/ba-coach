/** Render the visible application viewport without asking for screen-share permission. */
export async function captureViewport(): Promise<string> {
  const { toJpeg } = await import("html-to-image");
  return toJpeg(document.body, {
    width: window.innerWidth,
    height: window.innerHeight,
    canvasWidth: window.innerWidth,
    canvasHeight: window.innerHeight,
    pixelRatio: 1,
    quality: 0.72,
    cacheBust: true,
    backgroundColor: getComputedStyle(document.body).backgroundColor,
    // Feedback controls and the complete report dialog opt out here. The
    // dialog is also captured before it mounts, but this filter is the hard
    // safety boundary for slow rendering and manual re-captures.
    filter: (node) =>
      !(node instanceof HTMLElement && node.dataset.screenshotExclude === "true"),
  });
}
