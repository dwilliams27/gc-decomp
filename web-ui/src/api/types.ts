/** TypeScript types matching API responses. */

export interface FunctionEntry {
  id: number;
  name: string;
  address: number;
  size: number;
  source_file: string;
  library: string;
  initial_match_pct: number;
  current_match_pct: number;
  status: "pending" | "in_progress" | "matched" | "failed" | "skipped";
  attempts: number;
  matched_at: string | null;
  updated_at: string;
  created_at?: string;
}

export interface FunctionListResponse {
  total: number;
  page: number;
  per_page: number;
  functions: FunctionEntry[];
}

export interface TreemapLeaf {
  name: string;
  id: number;
  size: number;
  match_pct: number;
  status: string;
}

export interface TreemapNode {
  name: string;
  children: (TreemapNode | TreemapLeaf)[];
}

export interface AttemptEntry {
  id: number;
  started_at: string;
  completed_at: string | null;
  matched: boolean;
  best_match_pct: number;
  iterations: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  elapsed_seconds: number;
  termination_reason: string;
  final_code: string | null;
  error: string | null;
  model: string;
  reasoning_effort: string;
  match_history: [number, number][];
  tool_counts: Record<string, number>;
  cost: number;
}

export interface AttemptsResponse {
  function_name: string;
  attempts: AttemptEntry[];
}

export interface OverviewStats {
  total_functions: number;
  status_counts: Record<string, number>;
  total_tokens: number;
  total_cost: number;
  total_attempts: number;
  total_bytes: number;
  matched_bytes: number;
  match_histogram: { range: string; count: number }[];
}

export interface LibraryStats {
  library: string;
  count: number;
  matched: number;
  avg_match_pct: number;
  total_size: number;
  cost: number;
  tokens: number;
}

export interface BatchParams {
  limit: number;
  max_size: number | null;
  budget: number | null;
  workers: number;
  strategy: string;
  library: string | null;
  min_match: number | null;
  max_match: number | null;
  max_tokens: number | null;
}

export interface BatchStatus {
  running: boolean;
  cancelled?: boolean;
  started_at?: number;
  elapsed?: number;
  params?: BatchParams;
  attempted?: number;
  matched?: number;
  failed?: number;
  total_cost?: number;
  total_tokens?: number;
  current_functions?: string[];
  recent_completed?: Record<string, unknown>[];
}

export interface ConfigResponse {
  agent: { model: string; max_iterations: number; max_tokens_per_attempt: number };
  orchestration: {
    db_path: string;
    batch_size: number;
    default_workers: number;
    default_budget: number | null;
    max_function_size: number | null;
  };
  docker: { enabled: boolean };
  ghidra: { enabled: boolean };
}

export interface AgentEvent {
  type: string;
  ts: number;
  event?: string;
  level?: string;
  [key: string]: unknown;
}
