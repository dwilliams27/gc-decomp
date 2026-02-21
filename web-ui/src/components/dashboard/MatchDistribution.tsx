interface Props {
  histogram: { range: string; count: number }[];
}

export function MatchDistribution({ histogram }: Props) {
  const max = Math.max(...histogram.map((h) => h.count), 1);

  return (
    <div className="mt-4">
      <h3 className="mb-2 text-xs font-semibold text-gray-500 uppercase tracking-wide">
        Match Distribution
      </h3>
      <div className="space-y-1">
        {histogram.map((bucket) => (
          <div key={bucket.range} className="flex items-center gap-2 text-xs">
            <span className="w-12 text-right text-gray-500 font-mono">
              {bucket.range}
            </span>
            <div className="flex-1 h-3 rounded bg-gray-800">
              <div
                className="h-3 rounded bg-blue-600"
                style={{ width: `${(bucket.count / max) * 100}%` }}
              />
            </div>
            <span className="w-10 text-right text-gray-400 font-mono">
              {bucket.count}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
