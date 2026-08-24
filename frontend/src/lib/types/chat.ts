/**
 * Shared chat types used across AI chat panel components.
 */

export interface ParsedContent {
	prompt?: string;
	improvements?: string;
	raw: string;
}

/**
 * Core chat message structure shared by GenerationPanelChat and SegmentAIChatPanel.
 */
export interface ChatMessageData {
	id?: string;
	role: 'user' | 'assistant' | 'system';
	content: string;
	timestamp: number;
	parsed_content?: ParsedContent | null;
	tokens_used?: number | null;
	prompt_tokens?: number | null;
	completion_tokens?: number | null;
}

/**
 * One full-fidelity operation inside a `ToolApprovalPreview.changes` list —
 * richer than the chip-list `items`, since `before`/`after` carry the actual
 * segment/media/settings shape rather than a truncated string. `before: null`
 * marks an add, `after: null` a removal; both set is an update.
 */
export interface ToolApprovalChange {
	op: string;
	summary: string;
	before: Record<string, unknown> | null;
	after: Record<string, unknown> | null;
}

/**
 * A structured, human-facing preview of an action awaiting approval, filled by
 * a `requires_approval` tool. Lets the approval surface state intent — the
 * action verb, what it acts on, and the concrete items — instead of dumping raw
 * arguments. Absent for tools that don't fill it (fallback rendering applies).
 */
export interface ToolApprovalPreview {
	action: string;
	target?: string | null;
	items: string[];
	note?: string | null;
	/** Present only for tools (currently video director) that preview full
	 * before/after operations rather than a flat item list. */
	changes?: ToolApprovalChange[] | null;
}

export interface ToolExecution {
	tool_name: string;
	arguments: Record<string, unknown>;
	result: { success: boolean; data: string; error?: string };
	duration_ms: number;
	status?: 'running' | 'done';
	pending_approval?: boolean;
	rejected?: boolean;
	preview?: ToolApprovalPreview | null;
	/** Assigned by the stream reducers to interleave with trace_steps in chronological order. */
	seq?: number;
}

/** Behavior-trace step names emitted by the "status" SSE event, in pipeline order. */
export type TraceStepName =
	| 'resolving_resources'
	| 'loading_memory'
	| 'running_pre_chat'
	| 'thinking'
	| 'answering';

/**
 * One context step in the assistant's behavior trace (the non-tool steps —
 * resource resolution, memory recall, pre-chat actions, thinking, answering).
 * Tool steps are tracked separately as `ToolExecution`; the two interleave by
 * `seq` for chronological rendering.
 */
export interface TraceStep {
	step: TraceStepName;
	state: 'started' | 'completed';
	detail?: Record<string, any>;
	started_at?: number;
	duration_ms?: number;
	seq?: number;
}

/** Per-component size accounting inside `BehaviorTraceManifest.context_ledger`. */
export interface ContextLedgerEntry {
	chars: number;
	est_tokens: number;
}

/**
 * Per-turn accounting of what actually reached the LLM (`chars`/`est_tokens`
 * heuristic, not a real tokenizer count) — absent on manifests persisted
 * before this field existed.
 */
export interface ContextLedger {
	system_prompt: ContextLedgerEntry;
	tool_schemas: ContextLedgerEntry & { tool_count: number };
	memory: ContextLedgerEntry;
	history: ContextLedgerEntry & { message_count: number };
	total_est_tokens: number;
}

/** Persisted `metadata.behavior_trace` manifest on a completed assistant message. */
export interface BehaviorTraceManifest {
	version: number;
	mode: string | null;
	system_prompt_source: string;
	resources: Array<{ uri: string; type: string }>;
	memory: {
		note_ids: string[];
		by_scope: { global: number; preset: number; model: number };
		/** Notes beyond the per-group injection cap, left out of the block entirely. */
		by_scope_dropped?: { global: number; preset: number; model: number };
		injected_chars?: number;
	};
	pre_chat_actions: string[];
	tools_used: string[];
	token_counts: { prompt: number | null; completion: number | null };
	steps: Array<{ step: TraceStepName; duration_ms: number }>;
	/** Absent on manifests persisted before the ledger existed. */
	context_ledger?: ContextLedger;
	/** Tool name -> failure count for this turn, e.g. `{ write_memory: 2 }`. */
	tool_failures?: Record<string, number> | null;
}

/**
 * A chat mode as served by GET /api/chat/modes. The mode decides the system
 * prompt and tool set of a conversation; it is resolved from the current
 * route and immutable once the conversation has messages.
 */
export interface ChatMode {
	id: string;
	name: string;
	description: string;
	icon?: string | null;
	default_route_prefixes: string[];
	tools: string[];
	resource_namespaces?: string[] | null;
	source: string;
}

/** Tool metadata from GET /api/chat/tools (mode-scoped; null mode = global). */
export interface ChatToolInfo {
	name: string;
	description: string;
	hint: string;
	requires_approval?: boolean;
	mode?: string | null;
	icon?: string | null;
	label?: string | null;
	group?: string | null;
	user_description?: string | null;
}

/** One @resource suggestion from GET /api/chat/resources/suggest. */
export interface ResourceSuggestion {
	uri: string;
	label: string;
	kind: string;
	description?: string | null;
	has_children: boolean;
	icon?: string | null;
	/**
	 * Only meaningful when has_children is true: this navigable node is ALSO
	 * directly resolvable at its own uri, so it should offer both "attach"
	 * and "browse into" instead of forcing navigation.
	 */
	attachable?: boolean;
}

/**
 * A resource reference attached to a chat message. Outgoing messages send
 * only `{uri}`; messages echoed by the backend carry the resolved snapshot
 * (`kind`, `title`, `metadata`, `content`) in message metadata.resources.
 */
export interface ResourceRef {
	uri: string;
	kind?: string;
	title?: string;
	label?: string;
	metadata?: Record<string, unknown>;
	content?: string;
}

/** An @resource chip held by the chat input while composing a message. */
export interface ResourceChipData {
	uri: string;
	label: string;
}

/** Scope of a persistent memory note stored by the assistant. */
export type MemoryScope = 'global' | 'preset' | 'model';

/**
 * A persistent memory note the assistant keeps about the user, a preset, or a
 * model. Served by the /api/chat/memory endpoints. `scope_ref` holds the
 * preset/model ULID for scoped notes and is null for global notes.
 */
export interface MemoryNote {
	id: string;
	user_id: string;
	key: string;
	content: string;
	scope: MemoryScope;
	scope_ref: string | null;
	created_at: string | null;
	updated_at: string | null;
}

export interface MessageSource {
	source_type: string;
	title: string;
	subtitle?: string;
	description?: string;
	url?: string;
	icon?: string;
	metadata?: Record<string, unknown>;
}

/** One question docked above the composer, offered alongside an assistant reply. */
export interface ReplyContractQuestion {
	text: string;
	options: string[];
}

/**
 * Structured content the backend parses out of an assistant reply's `##
 * improved` / `## questions` sections (the reply's prose `content` is
 * already cleaned of the section markup by the time this is populated). Key
 * absent entirely when the reply had no parseable sections.
 */
export interface ReplyContract {
	improved: string[];
	questions: ReplyContractQuestion[];
}

/**
 * Extended chat message used by UnifiedAIChat with streaming, tool, and source support.
 */
export interface UnifiedChatMessageData {
	id?: string;
	role: 'user' | 'assistant' | 'system';
	content: string;
	timestamp: number;
	imageUrl?: string | null;
	tokens_used?: number | null;
	prompt_tokens?: number | null;
	completion_tokens?: number | null;
	tool_executions?: ToolExecution[];
	trace_steps?: TraceStep[];
	sources?: MessageSource[];
	isStreaming?: boolean;
	isSystem?: boolean;
	metadata?: Record<string, any>;
	parsed_content?: { reply_contract?: ReplyContract } & Record<string, unknown>;
}
