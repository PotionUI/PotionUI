import type { ToolExecution } from '$lib/types/chat';

/** One field change row for the mono diff block. */
export interface ApprovalDiffRow {
	field: string;
	oldValue: string;
	newValue: string;
	reason?: string;
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
