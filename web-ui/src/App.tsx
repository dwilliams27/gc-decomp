import { StarMapCanvas } from "./components/starmap/StarMapCanvas";
import { useStarPositions } from "./components/starmap/useStarPositions";
import { useCampaignEventSSE, useCampaignMessageSSE } from "./components/starmap/useCampaignSSE";
import { ConstellationLabels } from "./components/starmap/ConstellationLabels";
import { FunctionTooltip } from "./components/starmap/FunctionTooltip";
import { CampaignSelector } from "./components/campaign/CampaignSelector";
import { CommLog } from "./components/campaign/CommLog";
import { CampaignStats } from "./components/campaign/CampaignStats";
import { useCampaignStore } from "./stores/campaignStore";
import { useStarMapStore } from "./stores/starMapStore";

export default function App() {
  // Load star positions
  useStarPositions();

  // SSE connections
  const selectedCampaignId = useCampaignStore((s) => s.selectedCampaignId);
  const selectedCampaign = useCampaignStore((s) => s.selectedCampaign);
  const isLive =
    selectedCampaign?.status === "running" ||
    selectedCampaign?.status === "pending";

  useCampaignEventSSE(isLive ? selectedCampaignId : null);
  useCampaignMessageSSE(isLive ? selectedCampaignId : null);

  const loaded = useStarMapStore((s) => s.loaded);

  return (
    <div className="relative w-screen h-screen overflow-hidden bg-[#020408]">
      {/* Loading state */}
      {!loaded && (
        <div className="absolute inset-0 z-50 flex items-center justify-center">
          <div className="text-white/30 font-mono text-sm animate-pulse">
            Mapping stellar positions...
          </div>
        </div>
      )}

      {/* Canvas */}
      <StarMapCanvas />

      {/* HTML overlays */}
      <ConstellationLabels />
      <FunctionTooltip />
      <CampaignSelector />
      <CampaignStats />
      <CommLog />

      {/* Vignette overlay */}
      <div
        className="fixed inset-0 pointer-events-none z-0"
        style={{
          background:
            "radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.4) 100%)",
        }}
      />
    </div>
  );
}
