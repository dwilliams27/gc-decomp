import { useEffect, useState } from "react";
import { useCampaignStore } from "../../stores/campaignStore";

function formatDuration(startIso: string | null, endIso: string | null): string {
  if (!startIso) return "--:--:--";
  const start = new Date(startIso).getTime();
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  const elapsed = Math.max(0, Math.floor((end - start) / 1000));
  const h = Math.floor(elapsed / 3600);
  const m = Math.floor((elapsed % 3600) / 60);
  const s = elapsed % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function CampaignStats() {
  const campaign = useCampaignStore((s) => s.selectedCampaign);
  // Tick every second so elapsed time updates live
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!campaign || campaign.status !== "running") return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [campaign]);

  if (!campaign) return null;

  const tasks = campaign.tasks || [];
  const total = tasks.length;
  const completed = tasks.filter((t) => t.status === "completed").length;
  const matched = tasks.filter(
    (t) => t.status === "completed" && t.best_match_pct >= 100,
  ).length;
  const running = tasks.filter((t) => t.status === "running").length;
  const failed = tasks.filter((t) => t.status === "failed").length;
  const pending = tasks.filter((t) => t.status === "pending").length;

  const isFinished = campaign.status === "completed" || campaign.status === "stopped";

  return (
    <div className="absolute top-4 left-1/2 -translate-x-1/2 z-20">
      <div className="bg-black/70 backdrop-blur-sm rounded-lg border border-white/10 px-4 py-2 flex items-center gap-6 text-xs font-mono">
        <div className="text-center">
          <div className="text-white/40 uppercase text-[10px]">Campaign</div>
          <div className="text-white/90">
            #{campaign.id}{" "}
            <span
              className={
                campaign.status === "running"
                  ? "text-green-400"
                  : campaign.status === "completed"
                    ? "text-blue-400"
                    : campaign.status === "stopped"
                      ? "text-yellow-400"
                      : "text-white/50"
              }
            >
              {campaign.status}
            </span>
          </div>
        </div>
        <div className="text-center">
          <div className="text-white/40 uppercase text-[10px]">Elapsed</div>
          <div className="text-white/90 tabular-nums">
            {formatDuration(
              campaign.started_at,
              isFinished ? (campaign.completed_at ?? campaign.updated_at) : null,
            )}
          </div>
        </div>
        <div className="text-center">
          <div className="text-white/40 uppercase text-[10px]">Progress</div>
          <div className="text-white/90">
            {completed}/{total}
            {matched > 0 && (
              <span className="text-yellow-400 ml-1">({matched} matched)</span>
            )}
          </div>
        </div>
        <div className="flex gap-3">
          {running > 0 && (
            <div className="text-center">
              <div className="text-green-400/60 uppercase text-[10px]">Active</div>
              <div className="text-green-400">{running}</div>
            </div>
          )}
          {pending > 0 && (
            <div className="text-center">
              <div className="text-white/40 uppercase text-[10px]">Queue</div>
              <div className="text-white/60">{pending}</div>
            </div>
          )}
          {failed > 0 && (
            <div className="text-center">
              <div className="text-red-400/60 uppercase text-[10px]">Failed</div>
              <div className="text-red-400">{failed}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
