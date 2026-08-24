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

const MAX_HUMANIZED_ARGS = 4;
const MAX_ARG_VALUE_LENGTH = 40;

/**
 * Terse, human-readable argument summary for a pending approval with no
 * `preview` and no `proposed_changes` diff — used instead of a raw JSON
 * dump so the dock never shows internal argument shapes verbatim.
 */
export function humanizeApprovalArguments(execution: ToolExecution): string {
	const args = execution.arguments || {};
	const keys = Object.keys(args);
	if (keys.length === 0) return 'No arguments provided';

	const shown = keys.slice(0, MAX_HUMANIZED_ARGS).map((key) => {
		const value = args[key];
		let text: string;
		if (Array.isArray(value)) {
			text = `${value.length} item${value.length === 1 ? '' : 's'}`;
		} else if (value !== null && typeof value === 'object') {
			text = 'object';
		} else {
			text = formatValue(value);
		}
		if (text.length > MAX_ARG_VALUE_LENGTH) {
			text = `${text.slice(0, MAX_ARG_VALUE_LENGTH - 1)}…`;
		}
		return `${key}: ${text}`;
	});

	const remaining = keys.length - shown.length;
	return remaining > 0 ? `${shown.join(' · ')} · +${remaining} more` : shown.join(' · ');
}
