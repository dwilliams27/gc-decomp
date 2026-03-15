import { useEffect, useState } from "react";
import { useCampaignStore } from "../../stores/campaignStore";
import { useStarMapStore } from "../../stores/starMapStore";
import { campaignApi } from "../../api/campaigns";

export function CampaignSelector() {
  const campaigns = useCampaignStore((s) => s.campaigns);
  const selectedId = useCampaignStore((s) => s.selectedCampaignId);
  const setCampaigns = useCampaignStore((s) => s.setCampaigns);
  const setSelectedCampaignId = useCampaignStore((s) => s.setSelectedCampaignId);
  const setSelectedCampaign = useCampaignStore((s) => s.setSelectedCampaign);
  const clearMessages = useCampaignStore((s) => s.clearMessages);
  const setMode = useStarMapStore((s) => s.setMode);
  const addMessages = useCampaignStore((s) => s.addMessages);
  const addEvents = useCampaignStore((s) => s.addEvents);
  const processEvent = useStarMapStore((s) => s.processEvent);

  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      campaignApi.listCampaigns().then((data) => {
        if (!cancelled) setCampaigns(data.campaigns);
      });
    };
    load();
    const interval = setInterval(load, 10000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [setCampaigns]);

  const selectCampaign = (id: number | null) => {
    setSelectedCampaignId(id);
    clearMessages();
    if (id === null) {
      setSelectedCampaign(null);
      setMode("live");
      return;
    }
    const campaign = campaigns.find((c) => c.id === id);
    const isLive = campaign?.status === "running" || campaign?.status === "pending";
    setMode(isLive ? "live" : "history");

    campaignApi.getCampaign(id).then((detail) => {
      setSelectedCampaign(detail);
    });
    campaignApi.getCampaignMessages(id, 0, 500).then((resp) => {
      if (resp.messages.length > 0) addMessages(resp.messages);
    });
    campaignApi.getCampaignEvents(id, 0, 500).then((resp) => {
      if (resp.events.length > 0) {
        addEvents(resp.events);
        // Drive star pulsing from initial events
        for (const evt of resp.events) processEvent(evt);
      }
    });

    setExpanded(false);
  };

  const liveCampaigns = campaigns.filter(
    (c) => c.status === "running" || c.status === "pending",
  );
  const pastCampaigns = campaigns.filter(
    (c) => c.status !== "running" && c.status !== "pending",
  );

  // Collapsed: just show a small pill with campaign count
  if (!expanded) {
    const liveCount = liveCampaigns.length;
    return (
      <div className="absolute top-4 left-4 z-20">
        <button
          onClick={() => setExpanded(true)}
          className="bg-black/60 backdrop-blur-sm rounded-lg border border-white/10 px-3 py-1.5 text-xs font-mono text-white/50 hover:text-white/80 hover:border-white/20 transition-colors flex items-center gap-2"
        >
          {liveCount > 0 && (
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
          )}
          Campaigns
          {selectedId !== null && (
            <span className="text-white/30">#{selectedId}</span>
          )}
        </button>
      </div>
    );
  }

  return (
    <div className="absolute top-4 left-4 z-20 max-w-72">
      <div className="bg-black/70 backdrop-blur-sm rounded-lg border border-white/10 p-3">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-xs font-bold text-white/60 uppercase tracking-wider">
            Campaigns
          </h2>
          <button
            onClick={() => setExpanded(false)}
            className="text-white/30 hover:text-white/60 text-xs px-1"
          >
            &times;
          </button>
        </div>

        {liveCampaigns.length > 0 && (
          <div className="mb-2">
            <div className="text-[10px] text-green-400/80 uppercase tracking-wider mb-1">
              Live
            </div>
            {liveCampaigns.map((c) => (
              <button
                key={c.id}
                onClick={() => selectCampaign(c.id)}
                className={`w-full text-left px-2 py-1 rounded text-xs transition-colors ${
                  selectedId === c.id
                    ? "bg-green-500/20 text-green-300"
                    : "text-white/70 hover:bg-white/10"
                }`}
              >
                <span className="inline-block w-2 h-2 rounded-full bg-green-400 mr-1.5 animate-pulse" />
                #{c.id} {c.source_file.split("/").pop()}
              </button>
            ))}
          </div>
        )}

        {pastCampaigns.length > 0 && (
          <div>
            <div className="text-[10px] text-white/40 uppercase tracking-wider mb-1">
              History
            </div>
            <div className="max-h-40 overflow-y-auto">
              {pastCampaigns.slice(0, 10).map((c) => (
                <button
                  key={c.id}
                  onClick={() => selectCampaign(c.id)}
                  className={`w-full text-left px-2 py-1 rounded text-xs transition-colors ${
                    selectedId === c.id
                      ? "bg-blue-500/20 text-blue-300"
                      : "text-white/50 hover:bg-white/10"
                  }`}
                >
                  <span
                    className={`inline-block w-2 h-2 rounded-full mr-1.5 ${
                      c.status === "completed"
                        ? "bg-blue-400"
                        : c.status === "stopped"
                          ? "bg-yellow-400"
                          : "bg-red-400"
                    }`}
                  />
                  #{c.id} {c.source_file.split("/").pop()}
                </button>
              ))}
            </div>
          </div>
        )}

        {campaigns.length === 0 && (
          <div className="text-xs text-white/30 italic">
            No campaigns yet
          </div>
        )}
      </div>
    </div>
  );
}
