import { describe, expect, it } from 'vitest';
import { buildApprovalDiff, buildArgumentTree, buildDirectorChangeGroups, deriveCompactSummary } from './approvalPreview';
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

describe('buildArgumentTree', () => {
	// Bite check: the old `humanizeApprovalArguments` fallback rendered a
	// nested object as the literal word "object" and an array as "3 items"
	// with no way to see what was inside — a dead end. This proves the
	// replacement always carries real content instead.
	it('never stands in "object" or a bare item count for nested values', () => {
		const [, values, options] = buildArgumentTree(
			execution(undefined, {
				path: 'camera.angles',
				values: [1, 2, 3],
				options: { nested: true }
			})
		);
		expect(values.kind).toBe('array');
		expect(values.preview).not.toBe('3 items');
		expect(values.preview).toBe('1, 2, 3');
		expect(options.kind).toBe('object');
		expect(options.preview).not.toBe('object');
		expect(options.preview).toContain('nested: true');
	});

	it('builds a typed leaf per scalar argument: quoted strings, tabular numbers, bare booleans', () => {
		const [path, steps, enabled] = buildArgumentTree(
			execution(undefined, { path: 'camera.angles', steps: 28, enabled: true })
		);
		expect(path).toEqual({ key: 'path', kind: 'string', display: '"camera.angles"', open: false });
		expect(steps).toEqual({ key: 'steps', kind: 'number', display: '28', open: false });
		expect(enabled).toEqual({ key: 'enabled', kind: 'boolean', display: 'true', open: false });
	});

	it('renders null/undefined arguments as an explicit null leaf, not empty', () => {
		const [missing] = buildArgumentTree(execution(undefined, { missing: null }));
		expect(missing).toEqual({ key: 'missing', kind: 'null', display: 'null', open: false });
	});

	it('pre-opens only the root level: a nested object inside an object starts closed', () => {
		const [outer] = buildArgumentTree(execution(undefined, { outer: { inner: { deep: 1 } } }));
		expect(outer.open).toBe(true);
		const [inner] = outer.children!;
		expect(inner.key).toBe('inner');
		expect(inner.open).toBe(false);
	});

	it('previews an array of objects by the first item\'s own fields', () => {
		const [segments] = buildArgumentTree(
			execution(undefined, {
				segments: [
					{ start: '0:00', end: '0:04.5', text: 'Wide establishing shot' },
					{ start: '0:04.5', end: '0:09.0', text: 'Close-up' }
				]
			})
		);
		expect(segments.kind).toBe('array');
		expect(segments.count).toBe(2);
		expect(segments.preview).toBe('start: "0:00" · end: "0:04.5" · text: "Wide establishing shot"');
	});

	it('returns no nodes for an execution with no arguments', () => {
		expect(buildArgumentTree(execution(undefined, {}))).toEqual([]);
	});
});

describe('deriveCompactSummary', () => {
	it('prefers the preview\'s own summary when present', () => {
		expect(
			deriveCompactSummary(execution(undefined), 'Start Generation', {
				action: 'ignored',
				items: [],
				summary: 'Krea-2 Photoreal XL · 1216×832'
			})
		).toBe('Krea-2 Photoreal XL · 1216×832');
	});

	it('falls back to action + target for a legacy preview with no summary', () => {
		expect(
			deriveCompactSummary(execution(undefined), 'Update Prompt', {
				action: 'Update prompt',
				target: '"Lighthouse Keeper — Dawn"',
				items: []
			})
		).toBe('Update prompt — "Lighthouse Keeper — Dawn"');
	});

	it('falls back to a change count when only `changes` is present', () => {
		const preview: ToolApprovalPreview = {
			action: '',
			items: [],
			changes: [
				{ op: 'add_segment', summary: 'Add segment', before: null, after: { id: '1' } },
				{ op: 'update_segment', summary: 'Update segment', before: { id: '1' }, after: { id: '1', prompt: 'x' } }
			]
		};
		expect(deriveCompactSummary(execution(undefined), 'Update Video Director', preview)).toBe(
			'Update Video Director · 2 changes'
		);
	});

	it('falls back to a tool label + argument count with no preview at all', () => {
		expect(
			deriveCompactSummary(execution(undefined, { a: 1, b: 2 }), 'Update Segment Template', null)
		).toBe('Update Segment Template · 2 arguments');
		expect(deriveCompactSummary(execution(undefined, {}), 'Update Segment Template', null)).toBe(
			'Update Segment Template'
		);
	});
});
