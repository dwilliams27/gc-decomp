import { useCampaignStore } from "../../stores/campaignStore";
import { useStarMapStore } from "../../stores/starMapStore";

export function CampaignTimeline() {
  const timeline = useCampaignStore((s) => s.timeline);
  const mode = useStarMapStore((s) => s.mode);
  const playbackSpeed = useStarMapStore((s) => s.playbackSpeed);
  const paused = useStarMapStore((s) => s.paused);
  const setPlaybackSpeed = useStarMapStore((s) => s.setPlaybackSpeed);
  const setPaused = useStarMapStore((s) => s.setPaused);
  const selectedId = useCampaignStore((s) => s.selectedCampaignId);

  if (!selectedId || mode !== "history" || !timeline) return null;

  const events = timeline.events;
  const totalEvents = events.length;

  // Event type markers
  const markers = events.map((e, i) => {
    const pct = totalEvents > 1 ? (i / (totalEvents - 1)) * 100 : 50;
    let color = "bg-white/30";
    if (e.event_type === "match_achieved") color = "bg-yellow-400";
    else if (e.event_type === "worker_started") color = "bg-green-400/60";
    else if (e.event_type === "worker_completed") color = "bg-blue-400/60";
    else if (e.event_type === "worker_failed") color = "bg-red-400/60";
    else if (e.event_type === "status_change") color = "bg-purple-400/60";
    return { pct, color, event: e };
  });

  const speeds = [1, 2, 5, 10];

  return (
    <div className="absolute bottom-4 left-4 right-4 z-20">
      <div className="bg-black/70 backdrop-blur-sm rounded-lg border border-white/10 px-4 py-3">
        <div className="flex items-center gap-3 mb-2">
          <button
            onClick={() => setPaused(!paused)}
            className="text-white/70 hover:text-white text-xs font-mono px-2 py-0.5 border border-white/20 rounded"
          >
            {paused ? "PLAY" : "PAUSE"}
          </button>
          <div className="flex gap-1">
            {speeds.map((s) => (
              <button
                key={s}
                onClick={() => setPlaybackSpeed(s)}
                className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                  playbackSpeed === s
                    ? "bg-white/20 text-white"
                    : "text-white/40 hover:text-white/70"
                }`}
              >
                {s}x
              </button>
            ))}
          </div>
          <div className="text-[10px] text-white/40 font-mono ml-auto">
            {totalEvents} events
          </div>
        </div>
        {/* Timeline bar */}
        <div className="relative h-2 bg-white/5 rounded-full">
          {markers.map((m, i) => (
            <div
              key={i}
              className={`absolute top-0 w-1.5 h-2 rounded-full ${m.color}`}
              style={{ left: `${m.pct}%`, transform: "translateX(-50%)" }}
              title={`${m.event.event_type}: ${m.event.function_name || ""}`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
