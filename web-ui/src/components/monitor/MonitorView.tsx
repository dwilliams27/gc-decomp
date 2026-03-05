import { useRef } from "react";
import { CirclePacking } from "./CirclePacking";
import type { CirclePackingHandle } from "./CirclePacking";
import { BatchProgressOverlay } from "./BatchProgressOverlay";
import { RateDisplay } from "./RateDisplay";
import { ActivityTicker } from "./ActivityTicker";
import { LibraryScoreboard } from "./LibraryScoreboard";
import { MonitorExitButton } from "./MonitorExitButton";
import { useMonitorAnimations } from "./useMonitorAnimations";

export function MonitorView() {
  const circleRef = useRef<CirclePackingHandle>(null);

  useMonitorAnimations(circleRef);

  return (
    <div className="monitor-bg relative h-screen w-screen overflow-hidden bg-gray-950">
      {/* Background vignette */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_40%,rgba(0,0,0,0.6)_100%)]" />

      {/* Circle packing fills the entire viewport */}
      <div className="absolute inset-0">
        <CirclePacking ref={circleRef} />
      </div>

      {/* Floating overlay panels */}
      <div className="pointer-events-none absolute inset-0">
        {/* Top-left: Batch progress */}
        <div className="pointer-events-auto absolute top-4 left-4">
          <BatchProgressOverlay />
        </div>

        {/* Top-center: Rate display */}
        <div className="pointer-events-auto absolute top-4 left-1/2 -translate-x-1/2">
          <RateDisplay />
        </div>

        {/* Top-right: Exit button + connection status */}
        <div className="pointer-events-auto absolute top-4 right-4">
          <MonitorExitButton />
        </div>

        {/* Bottom-left: Activity ticker */}
        <div className="pointer-events-auto absolute bottom-4 left-4">
          <ActivityTicker />
        </div>

        {/* Bottom-right: Library scoreboard */}
        <div className="pointer-events-auto absolute right-4 bottom-4">
          <LibraryScoreboard />
        </div>
      </div>
    </div>
  );
}
