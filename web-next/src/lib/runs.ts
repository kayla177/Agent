export type Run = {
  id: number;
  agent_key: string;
  status: string;
  send: number;
  started_at: string;
  finished_at: string | null;
  output_message: string | null;
  error: string | null;
};
