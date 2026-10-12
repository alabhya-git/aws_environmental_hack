// Deterministic wording for the codes the API returns. No language model is involved.

export const REASONS: Record<string, string> = {
  PERSONAL_HISTORY: "Based on this diner's own earlier plates.",
  COLD_START_COHORT: "No history for this diner yet, so this uses other diners' plates for this recipe.",
  COLD_START_CONVENTIONAL: "No history yet. This is the kitchen's usual first serving.",
  DINER_REQUEST_HONOURED: "The diner asked for this portion. A request is always honoured.",
  PERSONALISATION_DECLINED: "This diner opted out of personal suggestions, so this is the usual serving.",
  CHANGE_LIMITED: "Moved by at most one scoop from the last serving; the estimate would go further.",
  INCLUDES_WANTED_MORE_EVIDENCE: "Includes plates where the diner said they wanted more.",
  SECONDS_NOT_ACCESSIBLE_COVERAGE_RULE: "Seconds are not accessible this service, so the portion covers most of the likely appetite instead of minimising leftovers.",
  AVAILABILITY_LIMIT_NOT_MET_AT_MAX_PORTION: "Even the largest portion leaves a high chance of needing two trips for seconds.",
  POSTERIOR_AT_AMOUNT_BOUNDARY: "The estimate is pressing against the model's range. Treat it with caution.",
  POSTERIOR_AT_SPREAD_BOUNDARY: "This diner's amounts vary more than the model's range allows for.",
  OBSERVATION_OUTSIDE_MODEL_RANGE: "One or more plates fall outside what the model considers possible.",
};

export const BASIS: Record<string, string> = {
  personal: "Personal estimate",
  cohort: "Cohort estimate",
  conventional: "Usual serving",
  requested: "Diner's request",
  opted_out: "Usual serving (opted out)",
};

export const CLASS_LABEL: Record<string, { short: string; tone: "used" | "skip" | "open" }> = {
  interval: { short: "Enough → used as “about this much”", tone: "used" },
  right_censored: { short: "Wanted more → used as “at least this much”", tone: "used" },
  ambiguous_empty: { short: "Empty plate, not asked → not used", tone: "skip" },
  confounded: { short: "Leftover for another reason → not used", tone: "skip" },
  unsure_leftover: { short: "Unsure, unexplained leftover → not used", tone: "skip" },
  transfer_unknown: { short: "Food shared → consumption unknown", tone: "skip" },
  missing_return: { short: "No return recorded → missing, not zero", tone: "skip" },
  open: { short: "Served, plate not back yet", tone: "open" },
  declined: { short: "Declined this food", tone: "skip" },
};

export const SATISFACTION: Record<string, string> = { enough: "Enough", wanted_more: "Wanted more", unsure: "Not sure / not asked" };

export const LEFTOVER_REASONS: Array<[string, string]> = [
  ["enough_food", "Had enough"],
  ["dislike", "Didn't like it"],
  ["interrupted", "Had to leave"],
  ["shared", "Shared with someone"],
  ["unknown", "Don't know"],
];

export const SECONDS_STATUS: Array<[string, string]> = [
  ["not_requested", "Didn't ask for seconds"],
  ["served", "Got seconds"],
  ["unavailable", "Asked, none available"],
  ["delayed", "Asked, had to wait"],
  ["declined", "Wanted more, didn't go back"],
];

export function grams(g: number | null | undefined): string {
  if (g === null || g === undefined || Number.isNaN(g)) return "–";
  return `${Math.round(g).toLocaleString("en-IN")} g`;
}
export function kg(g: number | null | undefined, digits = 1): string {
  if (g === null || g === undefined || Number.isNaN(g)) return "–";
  return `${(g / 1000).toFixed(digits)} kg`;
}
export function pct(x: number | null | undefined, digits = 0): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return `${(x * 100).toFixed(digits)}%`;
}
export function scoopsText(s: number | null | undefined): string {
  if (s === null || s === undefined) return "–";
  const whole = Math.floor(s);
  const half = Math.abs(s - whole - 0.5) < 0.01;
  const n = half ? (whole ? `${whole}½` : "½") : String(Number(s.toFixed(2)));
  return `${n} ${s === 1 ? "scoop" : "scoops"}`;
}
export function istTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}
