import { describe, expect, it } from 'vitest';
import { buildApprovalDiff, buildDirectorChangeGroups, humanizeApprovalArguments } from './approvalPreview';
import type { ToolApprovalPreview, ToolExecution } from '$lib/types/chat';

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

describe('buildDirectorChangeGroups', () => {
	it('returns null when the preview carries no changes (older/other-tool previews fall back untouched)', () => {
		expect(buildDirectorChangeGroups(null)).toBeNull();
		expect(buildDirectorChangeGroups(undefined)).toBeNull();
		expect(buildDirectorChangeGroups({ action: 'Update segments', items: [] })).toBeNull();
		expect(buildDirectorChangeGroups({ action: 'Update segments', items: [], changes: [] })).toBeNull();
	});

	it('an update shows only the fields that actually differ, e.g. just the prompt', () => {
		const preview: ToolApprovalPreview = {
			action: 'Update Video Director',
			items: [],
			changes: [
				{
					op: 'update_segment_prompt',
					summary: 'Update prompt on segment seg-1',
					before: { id: 'seg-1', prompt: 'A quiet forest at dawn' },
					after: { id: 'seg-1', prompt: 'A quiet forest at dawn, mist rising, warm golden light' }
				}
			]
		};
		expect(buildDirectorChangeGroups(preview)).toEqual([
			{
				op: 'update_segment_prompt',
				summary: 'Update prompt on segment seg-1',
				kind: 'update',
				rows: [
					{
						field: 'prompt',
						oldValue: 'A quiet forest at dawn',
						newValue: 'A quiet forest at dawn, mist rising, warm golden light'
					}
				]
			}
		]);
	});

	it('an add (before: null) shows the full new value, empty -> value for every field', () => {
		const preview: ToolApprovalPreview = {
			action: 'Update Video Director',
			items: [],
			changes: [
				{
					op: 'add_segment',
					summary: 'Add segment seg-3',
					before: null,
					after: { id: 'seg-3', prompt: 'Wide shot of the mountain range' }
				}
			]
		};
		const [group] = buildDirectorChangeGroups(preview)!;
		expect(group.kind).toBe('add');
		expect(group.rows).toEqual(
			expect.arrayContaining([
				{ field: 'id', oldValue: '(empty)', newValue: 'seg-3' },
				{ field: 'prompt', oldValue: '(empty)', newValue: 'Wide shot of the mountain range' }
			])
		);
	});

	it('a media op flattens nested media fields into full, untruncated path/label rows', () => {
		const preview: ToolApprovalPreview = {
			action: 'Update Video Director',
			items: [],
			changes: [
				{
					op: 'attach_media',
					summary: 'Attach first-frame image to segment seg-3',
					before: null,
					after: {
						role: 'first',
						segment_id: 'seg-3',
						media: {
							path: 'outputs/2026-08-24/a-very-long-descriptive-mountain-frame-filename.png',
							relative_path: '2026-08-24/a-very-long-descriptive-mountain-frame-filename.png'
						}
					}
				}
			]
		};
		const [group] = buildDirectorChangeGroups(preview)!;
		expect(group.rows).toEqual(
			expect.arrayContaining([
				{ field: 'role', oldValue: '(empty)', newValue: 'first' },
				{ field: 'segment_id', oldValue: '(empty)', newValue: 'seg-3' },
				{
					field: 'media.path',
					oldValue: '(empty)',
					newValue: 'outputs/2026-08-24/a-very-long-descriptive-mountain-frame-filename.png'
				},
				{
					field: 'media.relative_path',
					oldValue: '(empty)',
					newValue: '2026-08-24/a-very-long-descriptive-mountain-frame-filename.png'
				}
			])
		);
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
