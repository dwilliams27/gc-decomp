import { useEffect } from "react";
import { useStarMapStore } from "../../stores/starMapStore";
import { campaignApi } from "../../api/campaigns";
import type {
  StarPosition,
  ConstellationEdge,
  LibraryCentroid,
  StarLibrary,
} from "../../api/types";

/** Deterministic pseudo-random from a seed (0..1). */
function seeded(seed: number): number {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

/** Star visual radius from function byte size (log scale). */
function starRadius(size: number): number {
  return Math.max(1.5, Math.min(5, Math.log2(size / 8 + 1)));
}

function computeLayout(libraries: StarLibrary[]): {
  stars: StarPosition[];
  edges: ConstellationEdge[];
  centroids: LibraryCentroid[];
} {
  // Sky region: x in [0.04, 0.96], y in [0.04, 0.58] (as fractions)
  // Bottom 35% is landscape, top 65% is sky with some margin
  const skyLeft = 0.04, skyRight = 0.96;
  const skyTop = 0.04, skyBottom = 0.56;
  const skyW = skyRight - skyLeft;
  const skyH = skyBottom - skyTop;

  // Arrange library centroids in a loose grid
  const n = libraries.length;
  const cols = Math.ceil(Math.sqrt(n * 1.6));
  const rows = Math.ceil(n / cols);

  const centroids: LibraryCentroid[] = libraries.map((lib, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    // Base grid position
    let x = skyLeft + ((col + 0.5) / cols) * skyW;
    let y = skyTop + ((row + 0.5) / rows) * skyH;
    // Jitter
    x += (seeded(i * 3 + 1) - 0.5) * (skyW / cols) * 0.5;
    y += (seeded(i * 3 + 2) - 0.5) * (skyH / rows) * 0.4;
    // Clamp
    x = Math.max(skyLeft + 0.02, Math.min(skyRight - 0.02, x));
    y = Math.max(skyTop + 0.02, Math.min(skyBottom - 0.02, y));
    return { name: lib.name, x, y, functionCount: lib.functions.length };
  });

  const centroidMap = new Map(centroids.map((c) => [c.name, c]));

  // Place functions around their library centroid
  const stars: StarPosition[] = [];
  for (const lib of libraries) {
    const c = centroidMap.get(lib.name)!;
    // Spread proportional to sqrt(count), in fraction coords
    const spread = Math.min(0.10, 0.02 + 0.007 * Math.sqrt(lib.functions.length));

    // Group by source file
    const byFile = new Map<string, StarLibrary["functions"]>();
    for (const f of lib.functions) {
      const arr = byFile.get(f.source_file) || [];
      arr.push(f);
      byFile.set(f.source_file, arr);
    }

    let fileIdx = 0;
    for (const [, funcs] of byFile) {
      const fileAngle = (fileIdx / Math.max(byFile.size, 1)) * 2 * Math.PI;
      const fileDist = spread * 0.5;
      const fileCx = c.x + Math.cos(fileAngle) * fileDist;
      const fileCy = c.y + Math.sin(fileAngle) * fileDist * 0.7; // compress vertically

      for (const f of funcs) {
        const angle = seeded(f.id) * 2 * Math.PI;
        const dist = seeded(f.id + 7777) * spread * 0.45;
        const x = Math.max(0.01, Math.min(0.99, fileCx + Math.cos(angle) * dist));
        const y = Math.max(0.01, Math.min(0.62, fileCy + Math.sin(angle) * dist * 0.7));

        stars.push({
          id: f.id,
          x, y,
          radius: starRadius(f.size),
          library: lib.name,
          sourceFile: f.source_file,
          name: f.name,
          matchPct: f.match_pct,
          size: f.size,
          status: f.status,
          attempts: f.attempts,
        });
      }
      fileIdx++;
    }
  }

  // Constellation edges: connect same-file functions
  const edges: ConstellationEdge[] = [];
  const idsByFile = new Map<string, number[]>();
  for (const s of stars) {
    const arr = idsByFile.get(s.sourceFile) || [];
    arr.push(s.id);
    idsByFile.set(s.sourceFile, arr);
  }
  for (const ids of idsByFile.values()) {
    for (let i = 1; i < ids.length; i++) {
      edges.push({ source: ids[i - 1], target: ids[i] });
    }
  }

  return { stars, edges, centroids };
}

export function useStarPositions() {
  const setStars = useStarMapStore((s) => s.setStars);
  const loaded = useStarMapStore((s) => s.loaded);

  useEffect(() => {
    if (loaded) return;
    let cancelled = false;
    campaignApi.getStarmap().then((data) => {
      if (cancelled) return;
      const { stars, edges, centroids } = computeLayout(data.libraries);
      setStars(stars, edges, centroids);
    });
    return () => { cancelled = true; };
  }, [loaded, setStars]);
}
