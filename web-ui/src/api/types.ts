/** TypeScript types for the Star Map campaign visualizer. */

// --- Starmap data ---

export interface StarFunction {
  id: number;
  name: string;
  address: number;
  size: number;
  source_file: string;
  match_pct: number;
  status: string;
  attempts: number;
}

export interface StarLibrary {
  name: string;
  functions: StarFunction[];
}

export interface StarmapResponse {
  libraries: StarLibrary[];
}

// --- Campaigns ---

export interface CampaignSummary {
  id: number;
  source_file: string;
  status: string;
  orchestrator_provider: string;
  worker_provider_policy: string;
  max_active_workers: number;
  timeout_hours: number;
  notes: string;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string | null;
}

export interface CampaignTask {
  id: number;
  campaign_id: number;
  function_id: number | null;
  function_name: string | null;
  source_file: string;
  provider: string;
  scope: string;
  status: string;
  priority: number;
  best_match_pct: number;
  termination_reason: string;
  error: string;
  worker_id: string;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface CampaignDetail extends CampaignSummary {
  tasks: CampaignTask[];
}

export interface CampaignListResponse {
  total: number;
  page: number;
  per_page: number;
  campaigns: CampaignSummary[];
}

// --- Campaign Events ---

export interface CampaignEvent {
  id: number;
  campaign_id: number;
  task_id: number | null;
  function_name: string | null;
  event_type: string;
  data: Record<string, unknown>;
  created_at: string | null;
}

export interface CampaignEventsResponse {
  events: CampaignEvent[];
  last_id: number;
}

// --- Campaign Messages ---

export interface CampaignMessage {
  id: number;
  campaign_id: number;
  role: string;
  content: string;
  session_number: number;
  turn_number: number;
  created_at: string | null;
}

export interface CampaignMessagesResponse {
  messages: CampaignMessage[];
  last_id: number;
}

// --- Timeline ---

export interface CampaignTimelineResponse {
  events: CampaignEvent[];
  tasks: CampaignTask[];
}

// --- Star layout (2D sky) ---

/** Star position as fractions of viewport (0..1). */
export interface StarPosition {
  id: number;
  /** x position as fraction of viewport width (0..1). */
  x: number;
  /** y position as fraction of sky height (0..1, 0=top). */
  y: number;
  radius: number;
  library: string;
  sourceFile: string;
  name: string;
  matchPct: number;
  size: number;
  status: string;
  attempts: number;
}

export interface ConstellationEdge {
  source: number;
  target: number;
}

export interface LibraryCentroid {
  name: string;
  x: number;
  y: number;
  functionCount: number;
}

// --- Effects ---

export interface Supernova {
  starId: number;
  startTime: number;
  duration: number;
}
