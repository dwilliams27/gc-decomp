import { useEffect, useRef, useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import * as d3 from "d3";
import { api } from "../../api/client";
import type { TreemapLeaf, TreemapNode } from "../../api/types";
import { useSelectionStore } from "../../stores/selectionStore";
import { TreemapTooltip } from "./TreemapTooltip";

/** Match% -> color: red(0) -> amber(50) -> green(100) */
function matchColor(pct: number): string {
  if (pct >= 100) return "#22c55e"; // green-500
  if (pct >= 80) return "#84cc16"; // lime-500
  if (pct >= 60) return "#eab308"; // yellow-500
  if (pct >= 40) return "#f97316"; // orange-500
  if (pct >= 20) return "#ef4444"; // red-500
  return "#dc2626"; // red-600
}

function isLeaf(d: TreemapNode | TreemapLeaf): d is TreemapLeaf {
  return "match_pct" in d;
}

interface HoveredNode {
  name: string;
  match_pct: number;
  size: number;
  status: string;
  x: number;
  y: number;
}

export function TreemapView() {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [breadcrumb, setBreadcrumb] = useState<string[]>(["root"]);
  const [hovered, setHovered] = useState<HoveredNode | null>(null);
  const selectFunction = useSelectionStore((s) => s.selectFunction);

  const { data, isLoading, error } = useQuery({
    queryKey: ["treemap"],
    queryFn: api.getTreemap,
  });

  const currentPath = useRef<string[]>(["root"]);

  const render = useCallback(
    (rootData: TreemapNode, path: string[]) => {
      const svg = svgRef.current;
      const container = containerRef.current;
      if (!svg || !container || !rootData) return;

      const width = container.clientWidth;
      const height = container.clientHeight;

      // Navigate to the right subtree based on path
      let node: TreemapNode = rootData;
      for (let i = 1; i < path.length; i++) {
        const child = node.children.find((c) => c.name === path[i]);
        if (child && "children" in child) {
          node = child as TreemapNode;
        } else {
          break;
        }
      }

      const hierarchy = d3
        .hierarchy(node)
        .sum((d: any) => (isLeaf(d) ? Math.max(d.size, 1) : 0))
        .sort((a, b) => (b.value || 0) - (a.value || 0));

      d3.treemap<TreemapNode | TreemapLeaf>()
        .size([width, height])
        .paddingInner(1)
        .paddingOuter(2)
        .round(true)(hierarchy as any);

      const sel = d3.select(svg);
      sel.selectAll("*").remove();
      sel.attr("width", width).attr("height", height);

      const leaves = hierarchy.leaves();

      const rects = sel
        .selectAll<SVGRectElement, (typeof leaves)[0]>("rect")
        .data(leaves)
        .join("rect")
        .attr("x", (d: any) => d.x0)
        .attr("y", (d: any) => d.y0)
        .attr("width", (d: any) => Math.max(0, d.x1 - d.x0))
        .attr("height", (d: any) => Math.max(0, d.y1 - d.y0))
        .attr("fill", (d: any) => {
          const leaf = d.data as TreemapLeaf;
          return matchColor(leaf.match_pct);
        })
        .attr("stroke", (d: any) => {
          const leaf = d.data as TreemapLeaf;
          return leaf.status === "in_progress" ? "#3b82f6" : "none";
        })
        .attr("stroke-width", (d: any) => {
          const leaf = d.data as TreemapLeaf;
          return leaf.status === "in_progress" ? 2 : 0;
        })
        .attr("rx", 1)
        .style("cursor", "pointer");

      rects.on("mouseover", (event: MouseEvent, d: any) => {
        const leaf = d.data as TreemapLeaf;
        setHovered({
          name: leaf.name,
          match_pct: leaf.match_pct,
          size: leaf.size,
          status: leaf.status,
          x: event.clientX,
          y: event.clientY,
        });
      });

      rects.on("mousemove", (event: MouseEvent, d: any) => {
        const leaf = d.data as TreemapLeaf;
        setHovered({
          name: leaf.name,
          match_pct: leaf.match_pct,
          size: leaf.size,
          status: leaf.status,
          x: event.clientX,
          y: event.clientY,
        });
      });

      rects.on("mouseout", () => setHovered(null));

      rects.on("click", (_event: MouseEvent, d: any) => {
        const leaf = d.data as TreemapLeaf;
        if (leaf.id) {
          selectFunction(leaf.id);
        }
      });

      // Labels for large enough rects
      sel
        .selectAll<SVGTextElement, (typeof leaves)[0]>("text")
        .data(leaves.filter((d: any) => (d.x1 - d.x0) > 40 && (d.y1 - d.y0) > 14))
        .join("text")
        .attr("x", (d: any) => d.x0 + 3)
        .attr("y", (d: any) => d.y0 + 12)
        .text((d: any) => {
          const leaf = d.data as TreemapLeaf;
          const w = d.x1 - d.x0 - 6;
          const charFit = Math.floor(w / 6.5);
          return leaf.name.length > charFit
            ? leaf.name.slice(0, charFit - 1) + "\u2026"
            : leaf.name;
        })
        .attr("fill", "#fff")
        .attr("font-size", "10px")
        .attr("font-family", "monospace")
        .style("pointer-events", "none");
    },
    [selectFunction],
  );

  // Initial render + resize
  useEffect(() => {
    if (!data) return;
    render(data, currentPath.current);

    const observer = new ResizeObserver(() => {
      render(data, currentPath.current);
    });
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [data, render]);

  const zoomTo = (path: string[]) => {
    currentPath.current = path;
    setBreadcrumb(path);
    if (data) render(data, path);
  };

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        Loading treemap...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center text-red-500">
        Error: {(error as Error).message}
      </div>
    );
  }

  // Build clickable zoom targets from breadcrumb
  const zoomTargets = data
    ? getZoomTargets(data, currentPath.current)
    : [];

  return (
    <div className="flex h-full flex-col">
      {/* Breadcrumb + zoom targets */}
      <div className="flex items-center gap-2 border-b border-gray-800 bg-gray-900 px-4 py-2 text-sm">
        <button
          onClick={() => zoomTo(["root"])}
          className="text-blue-400 hover:text-blue-300"
        >
          root
        </button>
        {breadcrumb.slice(1).map((segment, i) => (
          <span key={i} className="flex items-center gap-2">
            <span className="text-gray-600">/</span>
            <button
              onClick={() => zoomTo(breadcrumb.slice(0, i + 2))}
              className="text-blue-400 hover:text-blue-300"
            >
              {segment}
            </button>
          </span>
        ))}
        {zoomTargets.length > 0 && (
          <>
            <span className="ml-4 text-gray-600">|</span>
            <span className="text-gray-500">Zoom into:</span>
            {zoomTargets.map((name) => (
              <button
                key={name}
                onClick={() => zoomTo([...currentPath.current, name])}
                className="rounded bg-gray-800 px-2 py-0.5 text-xs text-gray-300 hover:bg-gray-700"
              >
                {name}
              </button>
            ))}
          </>
        )}
      </div>
      <div ref={containerRef} className="relative flex-1">
        <svg ref={svgRef} className="h-full w-full" />
        {hovered && <TreemapTooltip {...hovered} />}
      </div>
    </div>
  );
}

/** Get child names that can be zoomed into (non-leaf children). */
function getZoomTargets(root: TreemapNode, path: string[]): string[] {
  let node: TreemapNode = root;
  for (let i = 1; i < path.length; i++) {
    const child = node.children.find((c) => c.name === path[i]);
    if (child && "children" in child) {
      node = child as TreemapNode;
    } else {
      return [];
    }
  }
  return node.children
    .filter((c) => "children" in c)
    .map((c) => c.name)
    .slice(0, 20); // Limit to avoid overflow
}
