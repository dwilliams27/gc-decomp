/** Match% -> color: red(0) -> amber(50) -> green(100) */
export function matchColor(pct: number): string {
  if (pct >= 100) return "#22c55e"; // green-500
  if (pct >= 80) return "#84cc16"; // lime-500
  if (pct >= 60) return "#eab308"; // yellow-500
  if (pct >= 40) return "#f97316"; // orange-500
  if (pct >= 20) return "#ef4444"; // red-500
  return "#dc2626"; // red-600
}
