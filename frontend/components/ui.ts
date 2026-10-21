/**
 * Shared class strings for the Rubber & Steel look.
 *
 * Toggleable styles are functions that return EITHER the on OR the off colors, never
 * both: with two conflicting Tailwind utilities on one element (bg-transparent and
 * bg-chalk), the one later in the generated CSS wins, not the one later in className.
 */

const btnBase =
  "inline-flex min-h-11 items-center gap-2 border-2 px-4 py-2 text-[13px] font-semibold uppercase tracking-[0.06em] transition-[border-color,background-color,transform] duration-150 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50";

export const btn = (on = false) =>
  `${btnBase} ${on ? "border-orange bg-orange text-ink" : "border-line bg-transparent text-chalk hover:border-orange"}`;

export const btnPrimary =
  "inline-flex min-h-11 items-center gap-2 border-2 border-orange bg-orange px-5 py-2 text-[13px] font-bold uppercase tracking-[0.08em] text-ink transition-[background-color,transform] duration-150 hover:bg-orange-hover active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50";

const chipBase =
  "min-h-9 border-2 px-3 py-1.5 text-xs font-bold uppercase tracking-[0.08em] transition-colors duration-150";

export const chip = (on = false) =>
  `${chipBase} ${on ? "border-chalk bg-chalk text-ink" : "border-line bg-transparent text-muted hover:border-steel hover:text-chalk"}`;

export const label = "text-[11px] font-bold uppercase tracking-[0.12em] text-muted";

export const field =
  "min-h-11 w-full border-2 border-line bg-rubber-inset px-3 py-2 font-bold text-chalk tabular placeholder:text-faint focus:border-orange focus:outline-none";
