import type { ToolApprovalChange, ToolApprovalPreview, ToolExecution } from '$lib/types/chat';

/** One field change row for the mono diff block. */
export interface ApprovalDiffRow {
	field: string;
	oldValue: string;
	newValue: string;
	reason?: string;
}

/** One `changes` operation rendered as a labeled group of field diff rows —
 * `rows` reuses the same mono diff idiom as `ApprovalDiffRow` (this module's
 * plain `field_name`/`old_value`/`new_value` shape) so the dock has a single
 * diff renderer for both `proposed_changes` and `changes`. */
export interface DirectorChangeGroup {
	op: string;
	summary: string;
	kind: 'add' | 'remove' | 'update';
	rows: ApprovalDiffRow[];
}

/** Every approval preview parses its execution result the same way: the
 * tool result's `data` field is a JSON string, and a malformed/absent
 * payload should render as an empty object rather than throw. */
function parseExecutionResultData<T = any>(execution: ToolExecution): T {
	try {
		return JSON.parse(execution.result?.data || '{}');
	} catch {
		return {} as T;
	}
}

function formatValue(value: unknown): string {
	if (value === null || value === undefined || value === '') return '(empty)';
	if (typeof value === 'object') return JSON.stringify(value);
	return String(value);
}

/**
 * A shape-based (not tool-name-based) diff: any pending execution whose
 * result carries `proposed_changes: [{field_name, old_value, new_value}]`
 * (the `update_form_settings` result shape) renders as an old -> new field
 * diff instead of a generic chip list. Returns null when the shape isn't
 * present, so callers fall through to the chip/fallback renderers.
 */
export function buildApprovalDiff(execution: ToolExecution): ApprovalDiffRow[] | null {
	const data = parseExecutionResultData<{
		proposed_changes?: Array<{ field_name: string; old_value: unknown; new_value: unknown; reason?: string }>;
	}>(execution);
	if (!data.proposed_changes || data.proposed_changes.length === 0) return null;
	return data.proposed_changes.map((change) => ({
		field: change.field_name,
		oldValue: formatValue(change.old_value),
		newValue: formatValue(change.new_value),
		reason: change.reason
	}));
}

/**
 * Recursively flattens a segment/media/settings object into dot-path leaves
 * (`media.path`, `role`, …) so a generic key-by-key diff surfaces exactly the
 * fields that changed — whatever shape the director wire format hands it —
 * without hardcoding per-op field names. Arrays are left as leaf values
 * (JSON-rendered by `formatValue`); diffing array elements isn't worth the
 * complexity here.
 */
function flattenChangeObject(obj: Record<string, unknown> | null, prefix = ''): Map<string, unknown> {
	const out = new Map<string, unknown>();
	if (!obj) return out;
	for (const [key, value] of Object.entries(obj)) {
		const path = prefix ? `${prefix}.${key}` : key;
		if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
			const nested = flattenChangeObject(value as Record<string, unknown>, path);
			if (nested.size === 0) {
				out.set(path, value);
			} else {
				for (const [nestedPath, nestedValue] of nested) out.set(nestedPath, nestedValue);
			}
		} else {
			out.set(path, value);
		}
	}
	return out;
}

/**
 * Builds the structured rendering for a video-director-style approval preview
 * whose `changes` carry full before/after objects (see docs/video-director.md
 * for the segment/media/settings wire shapes). Returns null when `changes` is
 * absent or empty so older previews (or other tools' `items`/`proposed_changes`
 * shapes) fall through to their existing renderers untouched.
 *
 * Each change becomes one group of field-diff rows: an add (`before: null`)
 * or remove (`after: null`) shows every leaf of the side that exists; an
 * update shows only the leaves that actually differ (e.g. a segment prompt
 * edit surfaces just the `prompt` row, not the whole segment). No value is
 * truncated — `formatValue` renders full text and full JSON alike.
 */
export function buildDirectorChangeGroups(
	preview: ToolApprovalPreview | null | undefined
): DirectorChangeGroup[] | null {
	if (!preview?.changes || preview.changes.length === 0) return null;
	return preview.changes.map((change: ToolApprovalChange) => {
		const kind: DirectorChangeGroup['kind'] =
			change.before == null ? 'add' : change.after == null ? 'remove' : 'update';
		const beforeMap = flattenChangeObject(change.before);
		const afterMap = flattenChangeObject(change.after);
		const fields = new Set([...beforeMap.keys(), ...afterMap.keys()]);
		const rows: ApprovalDiffRow[] = [];
		for (const field of fields) {
			const oldRaw = beforeMap.get(field);
			const newRaw = afterMap.get(field);
			if (kind === 'update' && formatValue(oldRaw) === formatValue(newRaw)) continue;
			rows.push({ field, oldValue: formatValue(oldRaw), newValue: formatValue(newRaw) });
		}
		rows.sort((a, b) => a.field.localeCompare(b.field));
		return { op: change.op, summary: change.summary, kind, rows };
	});
}

/**
 * The compact-row text shown before "Review full details" is expanded, for
 * a preview that didn't supply its own `summary`. Falls through the same
 * shape priority the expanded renderers use — action/target from a legacy
 * preview, an operation count from `changes`, or a tool-label + argument
 * count for the raw fallback — so every approval reads as a real sentence.
 */
export function deriveCompactSummary(
	execution: ToolExecution,
	toolLabel: string,
	preview: ToolApprovalPreview | null | undefined
): string {
	if (preview?.summary) return preview.summary;
	if (preview?.action) {
		return preview.target ? `${preview.action} — ${preview.target}` : preview.action;
	}
	if (preview?.changes?.length) {
		const count = preview.changes.length;
		return `${toolLabel} · ${count} change${count === 1 ? '' : 's'}`;
	}
	const argCount = Object.keys(execution.arguments || {}).length;
	return argCount > 0 ? `${toolLabel} · ${argCount} argument${argCount === 1 ? '' : 's'}` : toolLabel;
}

/** Discriminates an {@link ArgTreeNode}'s rendering: scalars carry a typed
 * `display` string, `object`/`array` carry `children` + a content `preview`. */
export type ArgTreeValueKind = 'string' | 'number' | 'boolean' | 'null' | 'object' | 'array';

/**
 * One key -> value entry in the generic argument tree fallback (used when a
 * pending approval has no `preview` at all). Nested objects/arrays are never
 * a dead end: `preview` always holds real content, not a placeholder like
 * "object" or "N items".
 */
export interface ArgTreeNode {
	key: string;
	kind: ArgTreeValueKind;
	/** Typed display text for scalar kinds (quoted strings, tabular numbers, etc). */
	display?: string;
	/** Nested entries for `object`/`array` kinds. */
	children?: ArgTreeNode[];
	/** One-line content preview for a collapsed `object`/`array`. */
	preview?: string;
	/** Item count, `array` kind only. */
	count?: number;
	/** Mutable UI state: root-level objects/arrays start open ("one level
	 * pre-opened"); anything nested starts closed. Toggled by the tree view. */
	open: boolean;
	/** Mutable UI state, `array` kind only: whether all children render vs
	 * just the first item's preview line + a "+N more" expander. */
	itemsExpanded?: boolean;
}

const PREVIEW_STRING_LENGTH = 40;
const PREVIEW_OBJECT_FIELDS = 3;
const PREVIEW_ARRAY_SCALARS = 4;

function previewScalar(value: unknown): string {
	if (value === null || value === undefined) return 'null';
	if (typeof value === 'string') {
		const quoted = JSON.stringify(value);
		return quoted.length > PREVIEW_STRING_LENGTH
			? `${quoted.slice(0, PREVIEW_STRING_LENGTH - 1)}…"`
			: quoted;
	}
	if (typeof value === 'boolean' || typeof value === 'number') return String(value);
	if (Array.isArray(value)) return previewArray(value);
	if (typeof value === 'object') return previewObject(value as Record<string, unknown>);
	return String(value);
}

/** A "key: value · key: value" preview of an object's own fields — used as
 * a collapsed disclosure's preview line and as the per-item preview under a
 * collapsed array of objects. */
function previewObject(obj: Record<string, unknown>): string {
	const entries = Object.entries(obj);
	if (entries.length === 0) return '(empty)';
	const parts = entries.slice(0, PREVIEW_OBJECT_FIELDS).map(([k, v]) => `${k}: ${previewScalar(v)}`);
	const remaining = entries.length - PREVIEW_OBJECT_FIELDS;
	return remaining > 0 ? `${parts.join(' · ')} · +${remaining} more` : parts.join(' · ');
}

function previewArray(arr: unknown[]): string {
	if (arr.length === 0) return 'empty';
	const [first] = arr;
	if (first !== null && typeof first === 'object' && !Array.isArray(first)) {
		return previewObject(first as Record<string, unknown>);
	}
	return arr.slice(0, PREVIEW_ARRAY_SCALARS).map(previewScalar).join(', ');
}

function buildTreeNode(key: string, value: unknown, topLevel: boolean): ArgTreeNode {
	if (Array.isArray(value)) {
		return {
			key,
			kind: 'array',
			count: value.length,
			children: value.map((item, index) => buildTreeNode(String(index), item, false)),
			preview: previewArray(value),
			open: topLevel,
			itemsExpanded: false
		};
	}
	if (value !== null && typeof value === 'object') {
		const entries = Object.entries(value as Record<string, unknown>);
		return {
			key,
			kind: 'object',
			children: entries.map(([k, v]) => buildTreeNode(k, v, false)),
			preview: previewObject(value as Record<string, unknown>),
			open: topLevel
		};
	}
	if (value === null || value === undefined) return { key, kind: 'null', display: 'null', open: false };
	if (typeof value === 'boolean') return { key, kind: 'boolean', display: String(value), open: false };
	if (typeof value === 'number') return { key, kind: 'number', display: String(value), open: false };
	return { key, kind: 'string', display: JSON.stringify(String(value)), open: false };
}

/**
 * Builds the generic key -> value tree shown when a pending approval has no
 * typed `preview` at all — replaces the old humanized-sentence fallback,
 * which stood in "object" or "N items" for anything nested instead of
 * rendering it. Every value gets a typed leaf (quoted strings, tabular
 * numbers, colored booleans); every object/array becomes a disclosure with
 * a genuine content preview, one level pre-opened.
 */
export function buildArgumentTree(execution: ToolExecution): ArgTreeNode[] {
	const args = execution.arguments || {};
	return Object.entries(args).map(([key, value]) => buildTreeNode(key, value, true));
}
