import { useEffect, useRef } from "react";
import { useStarMapStore } from "../../stores/starMapStore";
import { useCampaignStore } from "../../stores/campaignStore";
import type { CampaignEvent, CampaignMessage } from "../../api/types";

/** Subscribe to campaign event SSE stream. */
export function useCampaignEventSSE(campaignId: number | null) {
  const processEvent = useStarMapStore((s) => s.processEvent);
  const addEvent = useCampaignStore((s) => s.addEvent);
  const lastIdRef = useRef(0);

  useEffect(() => {
    if (!campaignId) return;
    lastIdRef.current = 0;

    const url = `/api/campaigns/${campaignId}/events/stream?after_id=0`;
    const source = new EventSource(url);

    source.addEventListener("campaign_event", (e: MessageEvent) => {
      try {
        const event: CampaignEvent = JSON.parse(e.data);
        lastIdRef.current = event.id;
        processEvent(event);
        addEvent(event);
      } catch {
        // ignore parse errors
      }
    });

    source.onerror = () => {
      // EventSource auto-reconnects
    };

    return () => source.close();
  }, [campaignId, processEvent, addEvent]);
}

/** Subscribe to campaign message SSE stream for comm log. */
export function useCampaignMessageSSE(campaignId: number | null) {
  const addMessage = useCampaignStore((s) => s.addMessage);
  const lastIdRef = useRef(0);

  useEffect(() => {
    if (!campaignId) return;
    lastIdRef.current = 0;

    const url = `/api/campaigns/${campaignId}/messages/stream?after_id=0`;
    const source = new EventSource(url);

    source.addEventListener("campaign_message", (e: MessageEvent) => {
      try {
        const msg: CampaignMessage = JSON.parse(e.data);
        lastIdRef.current = msg.id;
        addMessage(msg);
      } catch {
        // ignore parse errors
      }
    });

    source.onerror = () => {
      // EventSource auto-reconnects
    };

    return () => source.close();
  }, [campaignId, addMessage]);
}
