import AuthGate from "@/components/AuthGate";

export default function Home() {
  return (
    // The rail sits flush to the viewport; only message/composer reading
    // widths are capped inside the workspace.
    // `overflow-clip`, not `overflow-hidden`. Both hide the overflow, but a
    // `hidden` box is still a scroll container — it just has no visible
    // scrollbar — so anything that focuses a descendant (clicking the
    // composer, a browser restoring focus) silently scrolls this box and takes
    // the header off the top of the screen with it. `clip` creates no scroll
    // container at all, which makes h-[100dvh] a genuine hard boundary.
    <main className="app-canvas relative flex h-[100dvh] overflow-clip bg-canvas">
      {/* Ambient warmth behind the glass. Two very low-opacity, heavily blurred
          washes — enough to keep the flat surface from looking dead, far too
          faint to read as a glow effect.

          These deliberately bleed past the viewport, so the wrapper clips them
          itself rather than leaving <main> to do it: overflow contributed by an
          absolutely-positioned child still counts toward the scrollable area
          even when it is invisible. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 overflow-clip"
      >
        <div className="ambient-orb ambient-orb-a" />
        <div className="ambient-orb ambient-orb-b" />
        <div className="ambient-orb ambient-orb-c" />
      </div>

      {/* Renders either the sign-in screen or the workspace. Both live behind
          this one client boundary — see AuthGate on why it is not a render
          prop from this server component. */}
      <AuthGate />

    </main>
  );
}
