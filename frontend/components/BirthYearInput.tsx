"use client";

/** Only a year is collected. Never infer or submit a month/day or default year. */
export default function BirthYearInput({value, onChange, disabled = false}: {
  value: string; onChange: (value: string) => void; disabled?: boolean;
}) {
  return <input aria-label="出生年份" type="text" inputMode="numeric" autoComplete="bday-year"
    pattern="[0-9]{4}" maxLength={4} value={value} onChange={event => onChange(event.target.value)}
    disabled={disabled} placeholder="例如 2000" required
    className="archive-input min-h-11 w-full text-sm disabled:opacity-50" />;
}
