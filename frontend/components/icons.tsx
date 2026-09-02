/**
 * Minimalist line marks. All of them inherit `currentColor` and use a single
 * stroke weight, so an icon never becomes the loudest thing on screen.
 */

type IconProps = { className?: string };

const strokeProps = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.4,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

/**
 * Ensō — the open brush circle. Used as the product mark and the assistant
 * avatar. The deliberate gap is the point: it reads as unfinished and calm
 * rather than as a logo or a status ring.
 */
export function EnsoMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M16.8 5.5a7.6 7.6 0 1 0 3.1 4.6" />
    </svg>
  );
}

export function UserMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <circle cx="12" cy="9" r="3.2" />
      <path d="M5.8 19.2a6.4 6.4 0 0 1 12.4 0" />
    </svg>
  );
}

export function ArrowUpMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps} strokeWidth={1.6}>
      <path d="M12 19V6" />
      <path d="m6.5 11.5 5.5-5.5 5.5 5.5" />
    </svg>
  );
}

export function PlusMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M12 5.5v13M5.5 12h13" />
    </svg>
  );
}

export function CloseMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />
    </svg>
  );
}

/** Opens the conversation sidebar on narrow screens. */
export function MenuMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M4.5 7h15M4.5 12h15M4.5 17h15" />
    </svg>
  );
}

export function TrashMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M5.5 7.5h13M9.5 7.5V5.8a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1.7M10 11v6M14 11v6" />
      <path d="M7 7.5l.7 11a2 2 0 0 0 2 1.9h4.6a2 2 0 0 0 2-1.9l.7-11" />
    </svg>
  );
}

/** The manual trigger for the daily behavioral-activation record. */
export function NotebookMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <rect x="5" y="3.5" width="14" height="17" rx="2.3" />
      <path d="M9 8.2h6M9 12h6M9 15.8h3.5" />
    </svg>
  );
}

/** The disclosure arrow on the custom time-slot select. */
export function ChevronDownMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M5.5 9.5 12 15.5l6.5-6" />
    </svg>
  );
}

export function ChatBubbleMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M4.5 6.8a2.3 2.3 0 0 1 2.3-2.3h10.4a2.3 2.3 0 0 1 2.3 2.3v7.4a2.3 2.3 0 0 1-2.3 2.3H9.8L5.8 19.8a.4.4 0 0 1-.68-.29V16.5h-.02a2.3 2.3 0 0 1-.6-1.55Z" />
    </svg>
  );
}

/** Opens a conversation row's action menu. */
export function EllipsisMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" fill="currentColor">
      <circle cx="5.5" cy="12" r="1.5" />
      <circle cx="12" cy="12" r="1.5" />
      <circle cx="18.5" cy="12" r="1.5" />
    </svg>
  );
}

/** Renames a conversation. */
export function PencilMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M15.6 4.9a1.9 1.9 0 0 1 2.7 0l.8.8a1.9 1.9 0 0 1 0 2.7L9 18.5l-3.9 1 1-3.9Z" />
      <path d="M14.2 6.3 17.7 9.8" />
    </svg>
  );
}

/** Pins a conversation to the top of the sidebar. */
export function PinMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M12 15.2V21" />
      <path d="M8.4 3.6h7.2l-.9 6 2.6 2.4a.7.7 0 0 1-.5 1.2H7.2a.7.7 0 0 1-.5-1.2l2.6-2.4Z" />
    </svg>
  );
}

/** Exports a conversation as a downloadable file. */
export function ShareMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M12 15V4M8.5 7.5 12 4l3.5 3.5" />
      <path d="M5.5 13.5v4.2a2.3 2.3 0 0 0 2.3 2.3h8.4a2.3 2.3 0 0 0 2.3-2.3v-4.2" />
    </svg>
  );
}

/** Opens the subject's own profile. */
export function IdCardMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <rect x="3.5" y="5.5" width="17" height="13" rx="2.3" />
      <circle cx="9" cy="11" r="1.9" />
      <path d="M6.2 15.8a3 3 0 0 1 5.6 0M14 10.2h4M14 13.4h2.8" />
    </svg>
  );
}

/** Opens the change-password dialog. */
export function KeyMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <circle cx="8.2" cy="8.2" r="3.7" />
      <path d="m10.9 10.9 7.4 7.4M15.6 16.2l1.7-1.7M18 18.6l1.6-1.6" />
    </svg>
  );
}

/** Ends the session and returns to the sign-in screen. */
export function SignOutMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M14.5 5.2H7.8a2 2 0 0 0-2 2v9.6a2 2 0 0 0 2 2h6.7" />
      <path d="M18.5 12H11M15.8 8.8 19 12l-3.2 3.2" />
    </svg>
  );
}

/** Shown while the warm theme is active — i.e. what a click switches *to*. */
export function MoonMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.2 8.2 0 1 0 10.2 10.2Z" />
    </svg>
  );
}

/** Shown while the dark theme is active. Rays are short — a full starburst
 *  reads as a warning glyph at this size. */
export function SunMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <circle cx="12" cy="12" r="3.8" />
      <path d="M12 3.2v1.9M12 18.9v1.9M20.8 12h-1.9M5.1 12H3.2M18.2 5.8l-1.35 1.35M7.15 16.85 5.8 18.2M18.2 18.2l-1.35-1.35M7.15 7.15 5.8 5.8" />
    </svg>
  );
}

/** A quiet clock/list mark for previously submitted daily records. */
export function HistoryMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M4.8 8.2V4.8m0 0h3.4M4.9 4.9A8.1 8.1 0 1 1 3.9 15" />
      <path d="M12 7.4v5l3.2 1.8" />
    </svg>
  );
}

export function ArrowLeftMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M19 12H5M10.5 6.5 5 12l5.5 5.5" />
    </svg>
  );
}

/** Copies a single message without introducing a heavy toolbar. */
export function CopyMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <rect x="8" y="8" width="11" height="11" rx="2" />
      <path d="M16 8V6.5A1.5 1.5 0 0 0 14.5 5h-9A1.5 1.5 0 0 0 4 6.5v9A1.5 1.5 0 0 0 5.5 17H8" />
    </svg>
  );
}

/** Quiet success feedback after a message reaches the clipboard. */
export function CheckMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps} strokeWidth={1.7}>
      <path d="m5.5 12.5 4 4 9-9" />
    </svg>
  );
}

export function PromptMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path {...strokeProps} d="M6.5 3.8h8.2l2.8 2.8v13.6H6.5z" />
      <path {...strokeProps} d="M14.5 3.8v3h3M9 11h6M9 14.5h6M9 18h4" />
    </svg>
  );
}

/** Account directory and role delegation for administrators. */
export function UsersMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <circle cx="9" cy="9" r="3" />
      <path d="M3.8 19a5.3 5.3 0 0 1 10.4 0M15.5 6.4a3 3 0 0 1 0 5.2M16.8 14.5a5.1 5.1 0 0 1 3.4 4.5" />
    </svg>
  );
}

/** Reserved for actions that affect other accounts or everyone’s coaching
 *  experience. Deliberately a calm outline instead of an alarm-like badge. */
export function ShieldMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M12 3.2 19 6v5.25c0 4.45-2.95 7.72-7 9.55-4.05-1.83-7-5.1-7-9.55V6l7-2.8Z" />
      <path d="m8.8 12 2.05 2.05 4.35-4.35" />
    </svg>
  );
}

/** Administrator module sandbox — four stages around a resettable centre. */
export function SandboxMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <rect x="4" y="4" width="5" height="5" rx="1.2" />
      <rect x="15" y="4" width="5" height="5" rx="1.2" />
      <rect x="4" y="15" width="5" height="5" rx="1.2" />
      <rect x="15" y="15" width="5" height="5" rx="1.2" />
      <path d="M9.4 12a2.6 2.6 0 1 0 .75-1.84M9.4 9.5v2.2h2.2" />
    </svg>
  );
}

export function EnvelopeMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <rect x="3.5" y="5.5" width="17" height="13" rx="2.5" />
      <path d="m5 8 7 5 7-5" />
    </svg>
  );
}

/** Opens the calm, in-product problem report flow. */
export function ReportMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M5 5.8A2.3 2.3 0 0 1 7.3 3.5h9.4A2.3 2.3 0 0 1 19 5.8v7.4a2.3 2.3 0 0 1-2.3 2.3H10l-4.2 3.2a.5.5 0 0 1-.8-.4Z" />
      <path d="M12 7.1v3.5M12 13.1v.1" strokeWidth={1.7} />
    </svg>
  );
}

/** Screenshot attachment indicator in the report dialog. */
export function CameraMark({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...strokeProps}>
      <path d="M4 8.2A2.2 2.2 0 0 1 6.2 6h2l1.2-1.7h5.2L15.8 6h2A2.2 2.2 0 0 1 20 8.2v8.1a2.2 2.2 0 0 1-2.2 2.2H6.2A2.2 2.2 0 0 1 4 16.3Z" />
      <circle cx="12" cy="12.2" r="3.2" />
    </svg>
  );
}
