export type Scope = { start: number; end: number; kind: 'full' | 'partial' | 'unknown' };
export type Ref = { index: number; start: number; end: number; text: string; playable: boolean };
export type BlockType = 'summary' | 'chapter' | 'followup' | 'decision' | 'quote';
export type Block = {
  id: string;
  type: BlockType;
  title: string;
  text: string;
  evidence: Ref[];
  parent_id?: string;
  interpretation?: string;
};
export type Revision = { revision: number; author: 'agent' | 'user'; created_at: string };
export type Minutes = Revision & {
  recording_id: string;
  source_run_id: string;
  verified: boolean;
  media_url: string | null;
  media_offset: number;
  warning?: string;
  scope: Scope;
  blocks: Block[];
  overview?: { title: string; groups: { title: string; block_ids: string[] }[] };
  comparison?: { title: string; block_ids: string[] };
};
export type Edit = { id: string; title: string; text: string; interpretation?: string };
export type Recording = {
  id: string;
  title: string;
  date: string;
  duration: number | null;
  latest_revision: number;
};
export type Run = {
  id: string;
  state: string;
  scope: Scope;
  created_at: string;
  model?: string;
  verified?: boolean;
};
export type Job = { id?: string; state: string; error?: string };
export type Detail = {
  recording: Recording;
  runs: Run[];
  document: Minutes | null;
  history: Revision[];
  job?: Job | null;
};
export type ReaderProps = {
  recording: Recording;
  document: Minutes;
  history: Revision[];
  onLibrary: () => void;
  onGenerate: () => void;
  onImport: () => void;
  onSources: () => void;
  onHistory: (revision: number) => Promise<void>;
  onSave: (expectedRevision: number, edits: Edit[]) => Promise<void>;
};

export type ImportResult = { document: Minutes; history: Revision[] } | { run: Run };
