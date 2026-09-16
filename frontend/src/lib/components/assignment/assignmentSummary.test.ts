import { describe, it, expect } from 'vitest';
import { isModelUnassigned, applyAssignmentSummaryChange } from './assignmentSummary';
import type { AssignmentSummary } from '$lib/services/admin-api';

describe('isModelUnassigned', () => {
	it('is true when a model has no summary entry at all', () => {
		expect(isModelUnassigned(undefined)).toBe(true);
	});

	it('is true when both counts are zero', () => {
		expect(isModelUnassigned({ assignment_count: 0, group_count: 0 })).toBe(true);
	});

	it('is false once a user is assigned', () => {
		expect(isModelUnassigned({ assignment_count: 1, group_count: 0 })).toBe(false);
	});

	it('is false once a group is assigned', () => {
		expect(isModelUnassigned({ assignment_count: 0, group_count: 1 })).toBe(false);
	});
});

describe('applyAssignmentSummaryChange', () => {
	it('clears the unassigned badge for a model after its first assignment', () => {
		const modelId = 'model-1';
		let summary: AssignmentSummary = {};

		expect(isModelUnassigned(summary[modelId])).toBe(true);

		summary = applyAssignmentSummaryChange(summary, modelId, { userCount: 1, groupCount: 0 });

		expect(isModelUnassigned(summary[modelId])).toBe(false);
		expect(summary[modelId]).toEqual({ assignment_count: 1, group_count: 0 });
	});

	it('leaves other models untouched', () => {
		const summary: AssignmentSummary = { 'model-2': { assignment_count: 3, group_count: 0 } };
		const next = applyAssignmentSummaryChange(summary, 'model-1', { userCount: 1, groupCount: 0 });
		expect(next['model-2']).toEqual({ assignment_count: 3, group_count: 0 });
	});

	it('restores the unassigned badge once the last grant is removed', () => {
		let summary: AssignmentSummary = { 'model-1': { assignment_count: 1, group_count: 0 } };
		summary = applyAssignmentSummaryChange(summary, 'model-1', { userCount: 0, groupCount: 0 });
		expect(isModelUnassigned(summary['model-1'])).toBe(true);
	});
});
