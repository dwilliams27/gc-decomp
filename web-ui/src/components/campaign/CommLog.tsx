import { useEffect, useRef, useState } from "react";
import { useCampaignStore } from "../../stores/campaignStore";
import type { CampaignEvent, CampaignMessage } from "../../api/types";

type LogEntry =
  | { kind: "message"; msg: CampaignMessage; sortKey: string }
  | { kind: "event"; evt: CampaignEvent; sortKey: string };

function eventLabel(type: string): { label: string; color: string } {
  switch (type) {
    case "worker_started":
      return { label: "WORKER STARTED", color: "text-green-400/80" };
    case "worker_completed":
      return { label: "WORKER COMPLETED", color: "text-blue-400/80" };
    case "worker_failed":
      return { label: "WORKER FAILED", color: "text-red-400/80" };
    case "match_achieved":
      return { label: "MATCH ACHIEVED", color: "text-yellow-400" };
    case "match_improved":
      return { label: "MATCH IMPROVED", color: "text-amber-400/80" };
    case "status_change":
      return { label: "STATUS CHANGE", color: "text-purple-400/80" };
    case "progress":
      return { label: "ACTIVITY", color: "text-cyan-400/50" };
    case "tool_call":
      return { label: "TOOL CALL", color: "text-yellow-500/60" };
    default:
      return { label: type.toUpperCase(), color: "text-white/50" };
  }
}

export function CommLog() {
  const messages = useCampaignStore((s) => s.messages);
  const events = useCampaignStore((s) => s.events);
  const selectedId = useCampaignStore((s) => s.selectedCampaignId);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);

  // Auto-scroll only if already at bottom
  const isAtBottomRef = useRef(true);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && isAtBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages.length, events.length]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  };

  if (!selectedId) return null;

  // Merge messages and events chronologically
  const entries: LogEntry[] = [];
  for (const msg of messages) {
    entries.push({ kind: "message", msg, sortKey: msg.created_at ?? "" });
  }
  for (const evt of events) {
    entries.push({ kind: "event", evt, sortKey: evt.created_at ?? "" });
  }
  entries.sort((a, b) => a.sortKey.localeCompare(b.sortKey));

  if (!expanded) {
    return (
      <div className="absolute bottom-4 right-4 z-20">
        <button
          onClick={() => setExpanded(true)}
          className="bg-black/60 backdrop-blur-sm rounded-lg border border-green-500/20 px-3 py-1.5 text-xs font-mono text-green-400/50 hover:text-green-400/80 hover:border-green-500/30 transition-colors flex items-center gap-2"
        >
          Transmissions
          {entries.length > 0 && (
            <span className="text-green-400/30">{entries.length}</span>
          )}
        </button>
      </div>
    );
  }

  return (
    <div className="absolute bottom-4 right-4 z-20 w-80" style={{ height: "40vh" }}>
      <div className="h-full bg-black/80 backdrop-blur-sm rounded-lg border border-green-500/20 flex flex-col overflow-hidden">
        <div className="px-3 py-2 border-b border-green-500/20 flex-shrink-0 flex items-center justify-between">
          <h2 className="text-xs font-bold text-green-400/80 uppercase tracking-wider font-mono">
            Transmissions
          </h2>
          <button
            onClick={() => setExpanded(false)}
            className="text-green-400/30 hover:text-green-400/60 text-xs px-1"
          >
            &times;
          </button>
        </div>
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto p-3 space-y-2 font-mono text-xs"
        >
          {entries.length === 0 && (
            <div className="text-green-500/30 italic">
              Waiting for transmissions...
            </div>
          )}
          {entries.map((entry) => {
            if (entry.kind === "message") {
              const msg = entry.msg;
              if (msg.role === "orchestrator") {
                return (
                  <div key={`m-${msg.id}`} className="comm-entry">
                    <div className="text-green-500/40 text-[10px] mb-0.5">
                      [{msg.session_number}.{msg.turn_number}] ORCHESTRATOR
                    </div>
                    <div className="text-green-400/90 leading-relaxed whitespace-pre-wrap break-words">
                      {msg.content.trim().length > 500
                        ? msg.content.trim().slice(0, 500) + "..."
                        : msg.content.trim()}
                    </div>
                  </div>
                );
              }
              if (msg.role === "tool_call") {
                let toolName = "tool";
                try {
                  const parsed = JSON.parse(msg.content);
                  toolName = parsed.name || "tool";
                } catch {
                  /* ignore */
                }
                return (
                  <div key={`m-${msg.id}`} className="comm-entry">
                    <div className="text-yellow-500/60 text-[10px]">
                      [{msg.session_number}.{msg.turn_number}] TOOL: {toolName}
                    </div>
                  </div>
                );
              }
              if (msg.role === "tool_result") {
                let resultLabel = "RESULT";
                let resultDetail = msg.content.trim();
                let resultColor = "text-blue-300/50";
                try {
                  const parsed = JSON.parse(msg.content);
                  if (parsed.tool) resultLabel = `RESULT: ${parsed.tool}`;
                  if (parsed.error) {
                    resultDetail = parsed.error;
                    resultColor = "text-red-400/60";
                  } else if (parsed.output != null) {
                    resultDetail = String(parsed.output);
                  } else if (parsed.match_pct != null) {
                    resultDetail = `${parsed.match_pct}% match`;
                    if (parsed.detail) resultDetail += ` · ${parsed.detail}`;
                  } else if (parsed.fuzzy_match_percent != null) {
                    resultDetail = `${parsed.fuzzy_match_percent}% fuzzy`;
                    if (parsed.structural_match_percent != null)
                      resultDetail += ` / ${parsed.structural_match_percent}% structural`;
                  }
                } catch {
                  /* plain text content — use as-is */
                }
                return (
                  <div key={`m-${msg.id}`} className="comm-entry">
                    <div className="text-blue-400/40 text-[10px] mb-0.5">
                      [{msg.session_number}.{msg.turn_number}] {resultLabel}
                    </div>
                    <div className={`${resultColor} leading-relaxed whitespace-pre-wrap break-words max-h-32 overflow-hidden`}>
                      {resultDetail.length > 500
                        ? resultDetail.slice(0, 500) + "..."
                        : resultDetail}
                    </div>
                  </div>
                );
              }
              return null;
            }

            // Event entry
            const evt = entry.evt;
            const { label, color } = eventLabel(evt.event_type);
            const fnName = evt.function_name;
            let detail = "";
            if (evt.data) {
              try {
                const d = typeof evt.data === "string" ? JSON.parse(evt.data) : evt.data;
                if (evt.event_type === "match_improved" || evt.event_type === "progress") {
                  const parts: string[] = [];
                  if (d.observed_match_pct != null) parts.push(`${d.observed_match_pct.toFixed(1)}%`);
                  if (d.live_best_match_pct != null && d.live_best_match_pct !== d.observed_match_pct)
                    parts.push(`best ${d.live_best_match_pct.toFixed(1)}%`);
                  if (d.detail) parts.push(d.detail);
                  detail = parts.join(" · ");
                } else if (evt.event_type === "tool_call" && d.tool) {
                  detail = d.tool;
                } else if (d.best_match_pct !== undefined) {
                  detail = `${d.best_match_pct}% match`;
                } else if (d.status) {
                  detail = d.status;
                }
              } catch {
                /* ignore */
              }
            }

            return (
              <div key={`e-${evt.id}`} className="comm-entry">
                <div className={`${color} text-[10px]`}>
                  {label}
                  {fnName && (
                    <span className="text-white/40 ml-1">{fnName}</span>
                  )}
                </div>
                {detail && (
                  <div className="text-white/30 text-[10px]">{detail}</div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
