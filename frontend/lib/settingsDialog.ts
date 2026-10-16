/** Native top-layer dialog: keep the workspace and form drafts mounted underneath. */
export function showSettingsDialog(dialog: HTMLDialogElement) {
  const previouslyFocused = document.activeElement instanceof HTMLElement
    && document.activeElement !== document.body && document.activeElement !== document.documentElement
    ? document.activeElement : null;
  if (!dialog.open) dialog.showModal();
  return () => {
    if (dialog.open) dialog.close();
    // A menu entry may disappear as soon as it opens settings. Restore a useful
    // focus target without rebuilding the chat or changing its scroll position.
    const candidates = [previouslyFocused, ...document.querySelectorAll<HTMLElement>(
      '[data-settings-trigger], [data-chat-workspace] textarea, [data-chat-workspace] button',
    )];
    const target = candidates.find(element => element?.isConnected && !dialog.contains(element) && !element.matches(":disabled")
      && !element.closest('[inert], [hidden], dialog:not([open])') && element.getClientRects().length > 0);
    target?.focus({ preventScroll: true });
  };
}
