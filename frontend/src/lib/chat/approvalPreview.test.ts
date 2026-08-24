import { describe, expect, it } from 'vitest';
import { buildApprovalDiff, humanizeApprovalArguments } from './approvalPreview';
import type { ToolExecution } from '$lib/types/chat';

function execution(data: string | undefined, args: Record<string, unknown> = {}): ToolExecution {
	return {
		tool_name: 'update_form_settings',
		arguments: args,
		result: { success: true, data: data ?? '' },
		duration_ms: 0
	};
}

describe('buildApprovalDiff', () => {
	it('parses proposed_changes into diff rows with old -> new values', () => {
		const data = JSON.stringify({
			proposed_changes: [
				{ field_name: 'steps', old_value: 20, new_value: 30, reason: 'sharper detail' },
				{ field_name: 'seed', old_value: null, new_value: 12345 }
			]
		});
		expect(buildApprovalDiff(execution(data))).toEqual([
			{ field: 'steps', oldValue: '20', newValue: '30', reason: 'sharper detail' },
			{ field: 'seed', oldValue: '(empty)', newValue: '12345', reason: undefined }
		]);
	});

	it('returns null when there is no proposed_changes shape', () => {
		expect(buildApprovalDiff(execution(JSON.stringify({ action: 'Create category' })))).toBeNull();
		expect(buildApprovalDiff(execution(undefined))).toBeNull();
		expect(buildApprovalDiff(execution('{not json'))).toBeNull();
	});

	it('returns null for an empty proposed_changes array', () => {
		expect(buildApprovalDiff(execution(JSON.stringify({ proposed_changes: [] })))).toBeNull();
	});
});

describe('humanizeApprovalArguments', () => {
	it('summarizes scalar, array, and object arguments without dumping raw JSON', () => {
		const summary = humanizeApprovalArguments(
			execution(undefined, {
				path: 'camera.angles',
				values: [1, 2, 3],
				options: { nested: true }
			})
		);
		expect(summary).toBe('path: camera.angles · values: 3 items · options: object');
		expect(summary).not.toContain('{');
		expect(summary).not.toContain('nested');
	});

	it('truncates long scalar values and reports overflow count', () => {
		const summary = humanizeApprovalArguments(
			execution(undefined, {
				a: 'x'.repeat(60),
				b: 1,
				c: 2,
				d: 3,
				e: 4
			})
		);
		expect(summary).toContain('…');
		expect(summary).toContain('+1 more');
	});

	it('reports no arguments explicitly rather than an empty string', () => {
		expect(humanizeApprovalArguments(execution(undefined, {}))).toBe('No arguments provided');
	});
});
