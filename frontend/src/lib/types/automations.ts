/**
 * Automation module types — node-graph editor (triggers → conditions → actions).
 *
 * Mirrors the backend contract documented in the automation module plan (Part A):
 * graph JSON uses snake_case `source_handle`/`target_handle` on edges; the xyflow
 * mapping boundary lives in `$lib/stores/automationEditor.ts` (toFlowNode/toFlowEdge/
 * fromFlowGraph) — these types describe the wire format only, never xyflow shapes.
 */

/** Node "kind" — encodes both the xyflow `node.type` (routes to a custom node
 *  component) and the prefix of the node-type `key` (e.g. "trigger.filesystem"). */
export type NodeKind = 'trigger' | 'condition' | 'action';

export interface GraphPosition {
	x: number;
	y: number;
}

/** A node in the automation graph JSON. `type` is the full node-type key
 *  (e.g. "trigger.filesystem", "condition.compare", "action.add_tag"). */
export interface AutomationNode {
	id: string;
	type: string;
	position: GraphPosition;
	config: Record<string, any>;
}

/** An edge in the automation graph JSON. `source_handle` is "out" for
 *  trigger/action nodes, "true"/"false" for condition nodes (branching).
 *  `target_handle` is "in" for every node that accepts input. */
export interface AutomationEdge {
	id: string;
	source: string;
	source_handle: string;
	target: string;
	target_handle: string;
}

export interface AutomationGraph {
	nodes: AutomationNode[];
	edges: AutomationEdge[];
}

export type RunStatus = 'running' | 'success' | 'failed' | 'cancelled';

/** Status vocabulary for a single `automation_run_nodes` row (per A1). The
 *  WS `automation_run_update` message's `status` field uses this same set
 *  for node-level messages, and `RunStatus` for run-level messages. */
export type NodeRunStatus = 'running' | 'success' | 'failed' | 'skipped' | 'waiting';

export interface Automation {
	id: string;
	name: string;
	description?: string | null;
	enabled: boolean;
	graph: AutomationGraph;
	version: number;
	user_id?: string | null;
	created_at: string;
	updated_at: string;
	last_run_at?: string | null;
	last_run_status?: RunStatus | null;
}

/** Flat `{properties: {field_name: field_config}}` shape returned by
 *  `GET /api/automations/node-types` for `config_schema` — the exact shape
 *  the existing `FormField.svelte` render loop consumes. Each entry uses the
 *  same `{type, name, label, configuration, default}` vocabulary as preset
 *  `form.yml` / `src/core/fields/registry.ts`. */
export interface FieldSchemaEntry {
	type: string;
	name?: string;
	label?: string;
	title?: string;
	configuration?: Record<string, any>;
	default?: any;
	reactions?: any[];
	visible?: boolean;
	disabled?: boolean;
	validation?: Record<string, any>;
	/** This value is run through Jinja `render_template` on the backend, so it
	 *  accepts `{{ event.path }}` / `{{ upstream.<node_id>.model_id }}`. Only
	 *  action fields are templatable. */
	templatable?: boolean;
	/** This value references run data but is NOT Jinja. `"path"` means a bare
	 *  dot-path (`event.path`) resolved with `get_path` — that's how every
	 *  condition's `field` works. `"expression"` is a whole Jinja expression
	 *  (`condition.jinja_expression`). Inserting `{{ }}` into either breaks it. */
	input_ref?: 'path' | 'expression';
	[key: string]: any;
}

/** One field a node emits — i.e. one key of its runtime output. */
export interface NodeOutputDef {
	key: string;
	/** "string" | "number" | "boolean" | "array" | "object" | "any" */
	type: string;
	label?: string;
	description?: string;
	example?: unknown;
}

export interface ConfigSchema {
	properties: Record<string, FieldSchemaEntry>;
}

/** Palette catalog entry for a single registered node type. */
export interface NodeTypeDef {
	key: string;
	kind: NodeKind;
	title: string;
	description?: string;
	icon?: string;
	category?: string;
	config_schema: ConfigSchema;
	/** Output handle ids. Default `["out"]`; conditions expose `["true","false"]`.
	 *  Ignored (falls back to a dynamic per-node derivation) when
	 *  `dynamic_ports_config_key` is set — see below. */
	output_ports: string[];
	/** When present, this node type's output handles are NOT the static
	 *  `output_ports` list — they're derived per-instance from the node's own
	 *  config: `config[dynamic_ports_config_key]` is a comma-separated string
	 *  (e.g. "loras, checkpoints, vae"), parsed into one output handle per
	 *  trimmed entry (handle id === the trimmed case string), plus an always-
	 *  present trailing `"default"` handle. Used by `condition.switch`. */
	dynamic_ports_config_key?: string;
	/** The DATA contract: what downstream nodes can read from this node as
	 *  `upstream.<node_id>.<key>`. Distinct from `output_ports`, which are the
	 *  control-flow edge handles. For a TRIGGER these describe the event payload
	 *  it fires, which downstream nodes read as `event.<key>`. */
	outputs?: NodeOutputDef[];
	/** `outputs` is empty because the payload shape isn't statically knowable
	 *  (`trigger.manual` fires the caller's payload; `trigger.hook_event` fires
	 *  the selected hook's data) — not because the node emits nothing. Render
	 *  "runtime-defined" rather than an empty list. */
	dynamic_outputs?: boolean;
	source?: string;
}

/** Portable single-automation export produced by `GET /api/automations/{id}/export`. */
export interface AutomationExportEnvelope {
	schema: 'potionui.automation';
	schema_version: number;
	kind: 'automation';
	exported_at: string;
	automation: { name: string; description?: string | null; graph: AutomationGraph };
	/** Node types the graph uses, so an importer can name what it's missing. */
	node_types: string[];
}

/** A validation issue that didn't block an import but that this machine can't satisfy yet. */
export interface AutomationImportWarning {
	node_id: string | null;
	message: string;
	severity: string;
	category: 'structural' | 'environment';
}

export interface AutomationImportResult {
	automation: Automation;
	warnings: AutomationImportWarning[];
}

/** Immutable core/plugin template metadata returned by the runtime catalog. */
export interface AutomationTemplate {
	key: string;
	id: string;
	source: string;
	source_name: string;
	title: string;
	description: string;
	category: string;
	icon: string;
	tags: string[];
	node_types: string[];
	missing_node_types: string[];
	available: boolean;
}

/** Parses a `dynamic_ports_config_key` config value ("loras, checkpoints, vae")
 *  into ordered output handle ids, always appending the "default" fallback
 *  handle. Blank/duplicate entries are dropped (a blank or duplicate case
 *  would otherwise produce an unusable or ambiguous handle id). */
export function parseDynamicPorts(rawCases: unknown): string[] {
	const seen = new Set<string>();
	const ports: string[] = [];
	if (typeof rawCases === 'string') {
		for (const entry of rawCases.split(',')) {
			const trimmed = entry.trim();
			if (trimmed && !seen.has(trimmed)) {
				seen.add(trimmed);
				ports.push(trimmed);
			}
		}
	}
	ports.push('default');
	return ports;
}

export interface AutomationRun {
	id: string;
	automation_id: string;
	trigger_node_id?: string | null;
	trigger_type?: string | null;
	status: RunStatus;
	event_payload?: Record<string, any> | null;
	error?: string | null;
	started_at: string;
	finished_at?: string | null;
	duration_ms?: number | null;
}

export interface AutomationRunNode {
	id: string;
	run_id: string;
	node_id: string;
	node_type: string;
	status: NodeRunStatus;
	input?: any;
	output?: any;
	error?: string | null;
	started_at?: string | null;
	finished_at?: string | null;
	duration_ms?: number | null;
}

/** `GET /{id}/runs/{run_id}` response: the run row plus its per-node rows. */
export interface AutomationRunDetail extends AutomationRun {
	nodes: AutomationRunNode[];
}

export interface ValidationIssue {
	node_id: string | null;
	message: string;
	severity: 'error' | 'warning';
}

/** `/ws/automations` message. Node-level when `node_id` is present,
 *  run-level otherwise. `status` is a `RunStatus` for run-level messages
 *  and a `NodeRunStatus` for node-level messages. */
export interface AutomationRunUpdateMessage {
	type: 'automation_run_update';
	run_id: string;
	automation_id: string;
	node_id?: string;
	status: string;
	error?: string;
}

export interface CreateAutomationInput {
	name: string;
	description?: string;
	graph?: AutomationGraph;
	enabled?: boolean;
}

export interface UpdateAutomationInput {
	name?: string;
	description?: string;
	graph?: AutomationGraph;
}

export interface RunAutomationInput {
	node_id?: string;
	payload?: Record<string, any>;
}

export interface ListRunsOptions {
	limit?: number;
	before?: string;
}
