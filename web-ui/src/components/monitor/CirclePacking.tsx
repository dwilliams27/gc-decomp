import {
  useEffect,
  useRef,
  useCallback,
  useState,
  useImperativeHandle,
  forwardRef,
} from "react";
import { useQuery } from "@tanstack/react-query";
import * as d3 from "d3";
import { api } from "../../api/client";
import type { TreemapLeaf, TreemapNode } from "../../api/types";
import { matchColor } from "../../utils/colors";

function isLeaf(d: TreemapNode | TreemapLeaf): d is TreemapLeaf {
  return "match_pct" in d;
}

export interface CirclePackingHandle {
  /** O(1) lookup: function name -> SVG circle element */
  getCircle: (name: string) => SVGCircleElement | undefined;
  /** Get the SVG element for direct D3 manipulation */
  getSvg: () => SVGSVGElement | null;
}

export const CirclePacking = forwardRef<CirclePackingHandle>(
  function CirclePacking(_props, ref) {
    const svgRef = useRef<SVGSVGElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const circleMapRef = useRef<Map<string, SVGCircleElement>>(new Map());
    const [zoomedLibrary, setZoomedLibrary] = useState<string | null>(null);

    const { data, isLoading, error } = useQuery({
      queryKey: ["treemap"],
      queryFn: api.getTreemap,
      refetchInterval: 10_000,
    });

    useImperativeHandle(ref, () => ({
      getCircle: (name: string) => circleMapRef.current.get(name),
      getSvg: () => svgRef.current,
    }));

    const render = useCallback(
      (rootData: TreemapNode, zoomLib: string | null) => {
        const svg = svgRef.current;
        const container = containerRef.current;
        if (!svg || !container || !rootData) return;

        const width = container.clientWidth;
        const height = container.clientHeight;
        const size = Math.min(width, height);

        // Build hierarchy for d3.pack
        const hierarchy = d3
          .hierarchy(rootData, (d: any) =>
            isLeaf(d) ? null : (d as TreemapNode).children,
          )
          .sum((d: any) => (isLeaf(d) ? Math.max(d.size, 1) : 0))
          .sort((a, b) => (b.value || 0) - (a.value || 0));

        d3.pack<TreemapNode | TreemapLeaf>()
          .size([size, size])
          .padding((d) => (d.depth === 0 ? 2 : d.depth === 1 ? 6 : 1))(
          hierarchy as any,
        );

        const sel = d3.select(svg);
        sel.selectAll("*").remove();
        sel.attr("width", width).attr("height", height);

        // SVG filter for bright blue glow on active circles
        const defs = sel.append("defs");
        const glowFilter = defs.append("filter").attr("id", "active-glow");
        glowFilter
          .append("feGaussianBlur")
          .attr("stdDeviation", "4")
          .attr("result", "blur");
        glowFilter
          .append("feFlood")
          .attr("flood-color", "#3b82f6")
          .attr("flood-opacity", "0.8")
          .attr("result", "color");
        glowFilter
          .append("feComposite")
          .attr("in", "color")
          .attr("in2", "blur")
          .attr("operator", "in")
          .attr("result", "glow");
        const glowMerge = glowFilter.append("feMerge");
        glowMerge.append("feMergeNode").attr("in", "glow");
        glowMerge.append("feMergeNode").attr("in", "glow");
        glowMerge.append("feMergeNode").attr("in", "SourceGraphic");

        // Center the pack layout
        const offsetX = (width - size) / 2;
        const offsetY = (height - size) / 2;
        const g = sel
          .append("g")
          .attr("transform", `translate(${offsetX},${offsetY})`);

        const allNodes = hierarchy.descendants();

        // Determine zoom target
        let viewRoot = hierarchy;
        if (zoomLib) {
          const libNode = allNodes.find(
            (n) => n.depth === 1 && n.data.name === zoomLib,
          );
          if (libNode) viewRoot = libNode;
        }

        // Compute zoom transform
        const vx = (viewRoot as any).x;
        const vy = (viewRoot as any).y;
        const vr = (viewRoot as any).r;
        const k = size / (vr * 2.05);

        const zoomG = g
          .append("g")
          .attr(
            "transform",
            `translate(${size / 2},${size / 2}) scale(${k}) translate(${-vx},${-vy})`,
          );

        // Draw library circles (depth === 1)
        const libraryNodes = allNodes.filter((n) => n.depth === 1);
        zoomG
          .selectAll<SVGCircleElement, (typeof libraryNodes)[0]>(
            "circle.library",
          )
          .data(libraryNodes)
          .join("circle")
          .attr("class", "library")
          .attr("cx", (d: any) => d.x)
          .attr("cy", (d: any) => d.y)
          .attr("r", (d: any) => d.r)
          .attr("fill", "rgba(255,255,255,0.03)")
          .attr("stroke", "rgba(255,255,255,0.1)")
          .attr("stroke-width", 0.5)
          .style("cursor", "pointer")
          .on("click", (_event: MouseEvent, d: any) => {
            if (zoomLib === d.data.name) {
              setZoomedLibrary(null);
            } else {
              setZoomedLibrary(d.data.name);
            }
          });

        // Library labels (depth === 1)
        zoomG
          .selectAll<SVGTextElement, (typeof libraryNodes)[0]>("text.lib-label")
          .data(libraryNodes)
          .join("text")
          .attr("class", "lib-label")
          .attr("x", (d: any) => d.x)
          .attr("y", (d: any) => d.y - d.r + 12 / k)
          .attr("text-anchor", "middle")
          .attr("fill", "rgba(255,255,255,0.35)")
          .attr("font-size", `${Math.max(10, 12 / k)}px`)
          .attr("font-family", "monospace")
          .style("pointer-events", "none")
          .text((d: any) => d.data.name);

        // Draw function circles (leaves) — inactive first, then active on top
        const leaves = allNodes.filter((n) => !n.children);
        const inactive = leaves.filter(
          (d: any) => (d.data as TreemapLeaf).status !== "in_progress",
        );
        const active = leaves.filter(
          (d: any) => (d.data as TreemapLeaf).status === "in_progress",
        );
        const newCircleMap = new Map<string, SVGCircleElement>();

        // Inactive function circles
        zoomG
          .selectAll<SVGCircleElement, (typeof inactive)[0]>("circle.fn")
          .data(inactive)
          .join("circle")
          .attr("class", "fn")
          .attr("cx", (d: any) => d.x)
          .attr("cy", (d: any) => d.y)
          .attr("r", (d: any) => Math.max(d.r, 0.5))
          .attr("fill", (d: any) =>
            matchColor((d.data as TreemapLeaf).match_pct),
          )
          .attr("stroke", "none")
          .attr("stroke-width", 0)
          .style("opacity", 0.85)
          .each(function (d: any) {
            newCircleMap.set((d.data as TreemapLeaf).name, this);
          });

        // Active (in_progress) circles — enlarged, bright blue, with glow filter
        zoomG
          .selectAll<SVGCircleElement, (typeof active)[0]>("circle.fn-active")
          .data(active)
          .join("circle")
          .attr("class", "fn-active worker-pulse")
          .attr("cx", (d: any) => d.x)
          .attr("cy", (d: any) => d.y)
          .attr("r", (d: any) => Math.max(d.r * 2.5, 4 / k))
          .attr("fill", "#3b82f6")
          .attr("stroke", "#93c5fd")
          .attr("stroke-width", 1.5 / k)
          .attr("filter", "url(#active-glow)")
          .style("opacity", 1)
          .each(function (d: any) {
            newCircleMap.set((d.data as TreemapLeaf).name, this);
          });

        circleMapRef.current = newCircleMap;

        // Click background to zoom out
        g.insert("rect", ":first-child")
          .attr("width", size)
          .attr("height", size)
          .attr("fill", "transparent")
          .on("click", () => {
            if (zoomLib) setZoomedLibrary(null);
          });
      },
      [zoomedLibrary],
    );

    useEffect(() => {
      if (!data) return;
      render(data, zoomedLibrary);

      const observer = new ResizeObserver(() => {
        render(data, zoomedLibrary);
      });
      if (containerRef.current) observer.observe(containerRef.current);
      return () => observer.disconnect();
    }, [data, render, zoomedLibrary]);

    if (isLoading) {
      return (
        <div className="flex h-full w-full items-center justify-center text-gray-500">
          Loading circle packing...
        </div>
      );
    }

    if (error) {
      return (
        <div className="flex h-full w-full items-center justify-center text-red-500">
          Error: {(error as Error).message}
        </div>
      );
    }

    return (
      <div ref={containerRef} className="h-full w-full">
        <svg ref={svgRef} className="h-full w-full" />
      </div>
    );
  },
);
