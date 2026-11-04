import type { ChipData } from '$lib/types/segments';
import type { VariablesMap } from '$lib/utils/variableDefs';

export type NodeKind = 'media' | 'preset' | 'llm';

export interface PortSpec {
	label: string;
	media_type: string | null;
	target?: string;
	role?: string;
}

export type PortMap = Record<string, PortSpec>;

export interface PresetEntry {
	preset_id: string;
	mode: string;
	preset_name: string;
	mode_label: string;
	inputs?: PortMap;
	outputs?: PortMap;
	prompt?: boolean;
}

export type PresetIndex = Map<string, PresetEntry>;

export interface SessionSettings {
	prompt?: string;
	negativePrompt?: string;
	promptSegments?: Array<{ content?: string; text?: string } | null>;
	negativePromptSegments?: Array<{ content?: string; text?: string } | null>;
	formData?: Record<string, unknown>;
	seed?: number | string | null;
	selectedVariant?: string | null;
	videoDirector?: { mode?: string | null } | null;
	variables?: Record<string, unknown> | null;
	[key: string]: unknown;
}

export interface NodeOverrides extends SessionSettings {}

export interface SessionSource {
	id?: string | null;
	unversioned?: boolean;
	name?: string | null;
	version?: number | string | null;
}

export interface SessionRef {
	id?: string;
	name?: string | null;
	version?: number | string | null;
	deleted?: boolean;
}

export type FilePick = 'all' | 'random';

export interface MediaAsset {
	kind: 'history' | 'library';
	generation_id?: string;
	file_id?: string;
	item_id?: string;
	media_type?: string | null;
	original_filename?: string | null;
	preview_url?: string | null;
}

export interface ElixirNode {
	id: string;
	kind: NodeKind;
	title?: string | null;
	preset_id?: string | null;
	mode?: string | null;
	form_name?: string | null;
	overrides?: NodeOverrides;
	session_source?: SessionSource | null;
	session_snapshot?: SessionSettings | null;
	session_mode?: string | null;
	session_ref?: SessionRef | null;
	session_hash?: string | null;
	media_type?: string | null;
	assets?: MediaAsset[];
	source_revision?: number;
	pick?: FilePick;
	pick_count?: number;
	instruction?: string;
	instruction_chips?: Record<string, ChipData>;
	variables?: VariablesMap;
	target_node_id?: string | null;
	prompts_per_input?: number;
	pinned?: Record<string, string>;
	position?: { x: number; y: number };
}

export type NodeLike = Partial<ElixirNode>;

export interface ElixirEdge {
	id: string;
	source: string;
	source_handle: string;
	target: string;
	target_handle: string;
	iteration?: string;
}

export type RunEdge = Pick<ElixirEdge, 'id' | 'source' | 'target'> & Partial<ElixirEdge>;

export interface ElixirDefinition {
	schema_version?: number;
	nodes: ElixirNode[];
	edges: ElixirEdge[];
	viewport?: unknown;
}

export interface Verdict {
	ok: boolean;
	reason: string | null;
}

export interface Connection {
	source: string;
	sourceHandle: string;
	target: string;
	targetHandle: string;
}

export interface OutputEntry {
	kind?: string;
	text_id?: string;
	text?: string;
	item_id?: string;
	generation_id?: string | null;
	file_id?: string | number | null;
	file_index?: number;
	media_type?: string | null;
	original_filename?: string | null;
	preview_url?: string | null;
	media_url?: string | null;
	for_key?: string;
	slot_key?: string;
	prompt_number?: number;
	prompt_count?: number;
	prompt_index?: number;
	prompt_key?: string;
}

export interface RunCheckpoint {
	eligible?: OutputEntry[];
	confirmed?: string[] | null;
	draft?: string[];
	source?: string;
	linked?: string[];
	revision?: number;
	error?: string | null;
}

export interface FileDraw {
	seed: number;
	count: number;
	of: number;
}

export interface RunNodeState {
	status: string;
	manifest?: Record<string, OutputEntry[]>;
	draw?: FileDraw;
}

export interface Invocation {
	node_id: string;
	status: string;
	edge_id?: string | null;
	input_key: string;
	attempt: number;
	ordinal: number;
	progress?: number | null;
	submitted_at?: string | null;
	finished_at?: string | null;
	outputs?: OutputEntry[];
	effective_seed?: number | null;
	error?: string | null;
}

export interface Run {
	id?: string;
	status: string;
	scope?: string;
	created_at?: string | null;
	finished_at?: string | null;
	cancel_requested_at?: string | null;
	graph_edges?: RunEdge[];
	checkpoints?: Record<string, RunCheckpoint>;
	node_states?: Record<string, RunNodeState>;
	invocations?: Invocation[];
}

export interface ElixirSummary {
	id: string;
	name: string;
	status?: string;
	status_at?: string | null;
	latest_outputs?: OutputEntry[];
	output_count?: number;
	[key: string]: unknown;
}

export interface ValidationIssue {
	code: string;
	message?: string;
	severity?: string;
	node_id?: string | null;
	edge_id?: string | null;
}

export interface ValidationReport {
	issues?: ValidationIssue[];
}

export interface FollowedState {
	status?: string;
	session?: { name?: string; version?: number | string | null } | null;
	settings?: SessionSettings;
	session_hash?: string;
}
