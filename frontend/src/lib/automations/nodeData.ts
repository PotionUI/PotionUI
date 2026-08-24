/**
 * The data contract of a node, as the canvas needs to show it.
 *
 * Two things the raw catalog entry doesn't answer directly:
 *
 * 1. **What can I collect from this node?** Knowing a field is called `path` is
 *    useless on its own — what you type downstream is `{{ upstream.<id>.path }}`,
 *    or `event.path` when the producer is the trigger (the engine seeds
 *    `upstream[trigger_node_id] = event_payload`, so a trigger's outputs are
 *    reachable under `event`). `outputPrefix`/`outputRef` build that reference.
 *
 * 2. **What does this node take from upstream?** NOT its config form — most
 *    config is just settings you type (`debounce_ms`, `pattern`). The real input
 *    contract is the subset of fields that read run data, which the backend marks
 *    with `templatable` (Jinja `{{ }}`) or `input_ref` (a bare dot-path, or a
 *    whole expression). `getDataInputs` extracts exactly those. A node with none
 *    — e.g. `action.wait_for_gpu` — consumes nothing from upstream, and saying so
 *    is more useful than echoing its form.
 */
import type { NodeKind, NodeTypeDef } from '$lib/types/automations';

/** How a data-bound field expects to be written. */
export type AcceptsKind = 'template' | 'path' | 'expression';

export interface DataInput {
	name: string;
	title: string;
	accepts: AcceptsKind;
	/** Short hint shown when the field has no value yet. */
	hint: string;
	/** The current config value, when it's a non-empty string. */
	value: string | null;
	/** Whether `value` actually references run data (rather than a literal). */
	bound: boolean;
}

const HINTS: Record<AcceptsKind, string> = {
	template: '{{ … }}',
	path: 'dot-path',
	expression: 'expression'
};

/** True when a config value reads from the run, rather than being a literal. */
export function referencesRunData(value: unknown): boolean {
	if (typeof value !== 'string' || value.length === 0) return false;
	return /\{\{/.test(value) || /(^|[^\w.])(event|upstream)\./.test(value);
}

function acceptsKind(field: Record<string, unknown>): AcceptsKind | null {
	if (field.templatable) return 'template';
	if (field.input_ref === 'path') return 'path';
	if (field.input_ref === 'expression') return 'expression';
	return null;
}

/**
 * The fields this node reads run data through — its true input contract.
 * Plain settings are deliberately excluded; they're already in the config form.
 */
export function getDataInputs(
	def: NodeTypeDef | undefined,
	config: Record<string, unknown> | undefined
): DataInput[] {
	const properties = def?.config_schema?.properties ?? {};
	const inputs: DataInput[] = [];

	for (const [name, field] of Object.entries(properties)) {
		const accepts = acceptsKind(field as Record<string, unknown>);
		if (!accepts) continue;

		const raw = config?.[name] ?? field.default;
		const value = typeof raw === 'string' && raw.trim() !== '' ? raw : null;

		inputs.push({
			name,
			title: field.title || field.label || name,
			accepts,
			hint: HINTS[accepts],
			value,
			bound: referencesRunData(value)
		});
	}

	return inputs;
}

/**
 * The namespace a node's outputs are reachable under, downstream.
 * A trigger's payload is `event`; everything else is `upstream.<node_id>`.
 */
export function outputPrefix(kind: NodeKind, nodeId: string): string {
	return kind === 'trigger' ? 'event' : `upstream.${nodeId}`;
}

/** The full reference for one output field — what you paste into a config field. */
export function outputRef(kind: NodeKind, nodeId: string, key: string): string {
	return `${outputPrefix(kind, nodeId)}.${key}`;
}

/** The same reference, wrapped for a Jinja-templated (action) field. */
export function outputTemplate(kind: NodeKind, nodeId: string, key: string): string {
	return `{{ ${outputRef(kind, nodeId, key)} }}`;
}
