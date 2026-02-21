interface Props {
  name: string;
  match_pct: number;
  size: number;
  status: string;
  x: number;
  y: number;
}

export function TreemapTooltip({ name, match_pct, size, status, x, y }: Props) {
  return (
    <div
      className="pointer-events-none fixed z-50 rounded bg-gray-800 px-3 py-2 text-xs text-gray-100 shadow-lg"
      style={{ left: x + 12, top: y + 12 }}
    >
      <div className="font-bold">{name}</div>
      <div className="mt-1 text-gray-400">
        Match: <span className="text-white">{match_pct.toFixed(1)}%</span>
      </div>
      <div className="text-gray-400">
        Size: <span className="text-white">{size} bytes</span>
      </div>
      <div className="text-gray-400">
        Status: <span className="text-white">{status}</span>
      </div>
    </div>
  );
}
