import { useRef, useEffect } from "react";
import * as d3 from "d3";

interface Props {
  history: [number, number][]; // [iteration, match_pct]
}

export function MatchHistoryChart({ history }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || history.length < 2) return;

    const width = 300;
    const height = 60;
    const margin = { top: 4, right: 4, bottom: 16, left: 30 };
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;

    const x = d3
      .scaleLinear()
      .domain([history[0][0], history[history.length - 1][0]])
      .range([0, innerW]);

    const y = d3
      .scaleLinear()
      .domain([0, 100])
      .range([innerH, 0]);

    const line = d3
      .line<[number, number]>()
      .x((d) => x(d[0]))
      .y((d) => y(d[1]));

    const sel = d3.select(svg);
    sel.selectAll("*").remove();
    sel.attr("width", width).attr("height", height);

    const g = sel.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

    // Axes
    g.append("g")
      .attr("transform", `translate(0,${innerH})`)
      .call(d3.axisBottom(x).ticks(5).tickSize(3))
      .call((g) => g.selectAll("text").attr("fill", "#6b7280").attr("font-size", "8px"))
      .call((g) => g.selectAll("line,path").attr("stroke", "#374151"));

    g.append("g")
      .call(d3.axisLeft(y).ticks(3).tickSize(3).tickFormat((d) => `${d}%`))
      .call((g) => g.selectAll("text").attr("fill", "#6b7280").attr("font-size", "8px"))
      .call((g) => g.selectAll("line,path").attr("stroke", "#374151"));

    // Line
    g.append("path")
      .datum(history)
      .attr("fill", "none")
      .attr("stroke", "#3b82f6")
      .attr("stroke-width", 1.5)
      .attr("d", line);

    // Dots
    g.selectAll("circle")
      .data(history)
      .join("circle")
      .attr("cx", (d) => x(d[0]))
      .attr("cy", (d) => y(d[1]))
      .attr("r", 2)
      .attr("fill", "#3b82f6");
  }, [history]);

  if (history.length < 2) return null;

  return <svg ref={svgRef} className="mt-2" />;
}
