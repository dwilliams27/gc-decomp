import { useEffect, useRef, type RefObject } from "react";
import * as d3 from "d3";
import { useWorkerStore } from "../../stores/workerStore";
import { matchColor } from "../../utils/colors";
import type { CirclePackingHandle } from "./CirclePacking";

/**
 * Watches workerStore for changes and applies D3 transitions to circles:
 * - Blue pulse stroke for active workers
 * - Green flash + expand for matches
 * - Smooth color transitions for match improvements
 */
export function useMonitorAnimations(
  circleRef: RefObject<CirclePackingHandle | null>,
) {
  const prevStatusRef = useRef<Record<string, string>>({});
  const prevMatchRef = useRef<Record<string, number>>({});
  const activeAnimsRef = useRef<Set<string>>(new Set());

  const workers = useWorkerStore((s) => s.workers);

  useEffect(() => {
    const handle = circleRef.current;
    if (!handle) return;

    const prevStatus = prevStatusRef.current;
    const prevMatch = prevMatchRef.current;

    for (const [name, worker] of Object.entries(workers)) {
      const circle = handle.getCircle(name);
      if (!circle) continue;

      const sel = d3.select(circle);
      const oldStatus = prevStatus[name];
      const oldMatch = prevMatch[name] ?? 0;

      // Worker started running — add blue pulse
      if (worker.status === "running" && oldStatus !== "running") {
        sel
          .attr("stroke", "#3b82f6")
          .attr("stroke-width", 2)
          .classed("worker-pulse", true);
      }

      // Worker finished (matched) — celebration animation
      if (
        worker.status === "matched" &&
        oldStatus !== "matched" &&
        !activeAnimsRef.current.has(name)
      ) {
        activeAnimsRef.current.add(name);
        const origR = parseFloat(sel.attr("r")) || 3;

        // Flash white, expand, then settle to green
        sel
          .classed("worker-pulse", false)
          .attr("stroke", "none")
          .attr("stroke-width", 0)
          .transition()
          .duration(150)
          .attr("fill", "#ffffff")
          .attr("r", origR * 1.5)
          .style("opacity", 1)
          .transition()
          .duration(800)
          .attr("fill", "#22c55e")
          .attr("r", origR)
          .style("opacity", 0.85)
          .on("end", () => {
            activeAnimsRef.current.delete(name);
          });

        // Spawn particles
        spawnParticles(handle, circle);
      }

      // Worker finished (failed/crashed) — remove pulse, dim slightly
      if (
        (worker.status === "failed" || worker.status === "crashed") &&
        oldStatus === "running"
      ) {
        sel
          .classed("worker-pulse", false)
          .attr("stroke", "none")
          .attr("stroke-width", 0)
          .transition()
          .duration(400)
          .attr("fill", matchColor(worker.matchPct))
          .style("opacity", 0.85);
      }

      // Match improved (still running) — smooth color transition
      if (
        worker.status === "running" &&
        worker.matchPct > oldMatch &&
        !activeAnimsRef.current.has(name)
      ) {
        sel
          .transition()
          .duration(800)
          .attr("fill", matchColor(worker.matchPct));
      }

      prevStatus[name] = worker.status;
      prevMatch[name] = worker.matchPct;
    }
  }, [workers, circleRef]);
}

/** Spawn 6 green particle circles that burst outward and fade. */
function spawnParticles(handle: CirclePackingHandle, circle: SVGCircleElement) {
  const svg = handle.getSvg();
  if (!svg) return;

  // Get circle position in SVG coordinate space
  const cx = parseFloat(circle.getAttribute("cx") || "0");
  const cy = parseFloat(circle.getAttribute("cy") || "0");

  // Find the parent <g> that the circle lives in (the zoom group)
  const parent = circle.parentElement;
  if (!parent) return;
  const g = d3.select(parent);

  for (let i = 0; i < 6; i++) {
    const angle = (i / 6) * Math.PI * 2;
    const dist = 15 + Math.random() * 10;
    const tx = cx + Math.cos(angle) * dist;
    const ty = cy + Math.sin(angle) * dist;

    g.append("circle")
      .attr("cx", cx)
      .attr("cy", cy)
      .attr("r", 1.5)
      .attr("fill", "#22c55e")
      .style("opacity", 0.9)
      .transition()
      .duration(600 + Math.random() * 400)
      .ease(d3.easeQuadOut)
      .attr("cx", tx)
      .attr("cy", ty)
      .attr("r", 0.5)
      .style("opacity", 0)
      .remove();
  }
}
