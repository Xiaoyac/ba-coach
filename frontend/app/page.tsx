import AuthGate from "@/components/AuthGate";
import ThemeToggle from "@/components/ThemeToggle";

export default function Home() {
  return (
    // No `justify-center` here any more: with the conversation sidebar,
    // centering the *whole* [sidebar + chat] block as one unit means the
    // sidebar floats away from the real viewport edge by however much wider
    // than max-w-[74rem] the screen is — exactly the "wasted space on the
    // left" the sidebar was supposed to fix. Alignment is delegated to
    // ConversationWorkspace instead: the sidebar sits flush against this
    // padding, and the chat card centers itself in whatever space is left.
    //
    // `overflow-clip`, not `overflow-hidden`. Both hide the overflow, but a
    // `hidden` box is still a scroll container — it just has no visible
    // scrollbar — so anything that focuses a descendant (clicking the
    // composer, a browser restoring focus) silently scrolls this box and takes
    // the header off the top of the screen with it. `clip` creates no scroll
    // container at all, which makes h-[100dvh] a genuine hard boundary.
    <main className="relative flex h-[100dvh] overflow-clip bg-canvas px-4 py-5 sm:py-6">
      {/* Ambient warmth behind the glass. Two very low-opacity, heavily blurred
          washes — enough to keep the flat surface from looking dead, far too
          faint to read as a glow effect.

          These deliberately bleed past the viewport, so the wrapper clips them
          itself rather than leaving <main> to do it: overflow contributed by an
          absolutely-positioned child still counts toward the scrollable area
          even when it is invisible. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-clip"
      >
        <div className="absolute -top-40 left-1/2 h-[34rem] w-[34rem] -translate-x-1/2 rounded-full bg-wash-a blur-[130px]" />
        <div className="absolute -bottom-56 right-1/4 h-[28rem] w-[28rem] rounded-full bg-wash-b blur-[130px]" />
      </div>

      {/* Renders either the sign-in screen or the workspace. Both live behind
          this one client boundary — see AuthGate on why it is not a render
          prop from this server component. */}
      <AuthGate />

      <ThemeToggle />
    </main>
  );
}
