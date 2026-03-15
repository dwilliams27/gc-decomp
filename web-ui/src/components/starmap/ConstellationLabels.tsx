import { useEffect, useState } from "react";
import { useStarMapStore } from "../../stores/starMapStore";

export function ConstellationLabels() {
  const centroids = useStarMapStore((s) => s.centroids);
  const loaded = useStarMapStore((s) => s.loaded);
  const [hoveredName, setHoveredName] = useState<string | null>(null);

  useEffect(() => {
    if (!loaded || centroids.length === 0) return;

    const onMove = (e: MouseEvent) => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      const mx = e.clientX / w;
      const my = e.clientY / h;

      // Find nearest centroid within threshold
      let best: string | null = null;
      let bestDist = 0.04; // ~40px at 1000px viewport
      for (const c of centroids) {
        const dx = mx - c.x;
        const dy = my - c.y;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d < bestDist) {
          bestDist = d;
          best = c.name;
        }
      }
      setHoveredName(best);
    };

    window.addEventListener("mousemove", onMove);
    return () => window.removeEventListener("mousemove", onMove);
  }, [loaded, centroids]);

  if (!loaded || !hoveredName) return null;

  const c = centroids.find((c) => c.name === hoveredName);
  if (!c) return null;

  const sx = c.x * window.innerWidth;
  const sy = c.y * window.innerHeight;

  return (
    <div className="fixed inset-0 pointer-events-none z-10">
      <div
        className="absolute"
        style={{
          left: sx,
          top: sy - 16,
          transform: "translate(-50%, -100%)",
        }}
      >
        <div className="text-[10px] text-white/30 font-mono uppercase tracking-widest text-center whitespace-nowrap">
          {c.name}
          <div className="text-[8px] text-white/15">
            {c.functionCount} fn
          </div>
        </div>
      </div>
    </div>
  );
}
