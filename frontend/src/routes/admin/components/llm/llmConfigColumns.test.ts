import { describe, expect, it } from 'vitest';
import { assignmentCountLabel } from './llmConfigColumns';

describe('assignmentCountLabel', () => {
	it('reports Unassigned when there is no entry for the config', () => {
		expect(assignmentCountLabel({}, 'cfg-1')).toBe('Unassigned');
	});

	it('reports Unassigned when the entry has zero users and groups', () => {
		expect(assignmentCountLabel({ 'cfg-1': { assignment_count: 0, group_count: 0 } }, 'cfg-1')).toBe('Unassigned');
	});

	it('formats a single user with singular wording', () => {
		expect(assignmentCountLabel({ 'cfg-1': { assignment_count: 1, group_count: 0 } }, 'cfg-1')).toBe('1 user');
	});

	it('formats multiple users and groups together', () => {
		expect(assignmentCountLabel({ 'cfg-1': { assignment_count: 3, group_count: 2 } }, 'cfg-1')).toBe('3 users · 2 groups');
	});

	it('formats groups only', () => {
		expect(assignmentCountLabel({ 'cfg-1': { assignment_count: 0, group_count: 1 } }, 'cfg-1')).toBe('1 group');
	});
});
