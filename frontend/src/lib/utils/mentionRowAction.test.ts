import { describe, it, expect } from 'vitest';
import { resolveMentionRowAction } from './mentionRowAction';

describe('resolveMentionRowAction', () => {
	it('a leaf (hasChildren: false) always attaches as a value, regardless of attachable', () => {
		expect(resolveMentionRowAction({ hasChildren: false })).toBe('attach-value');
		expect(resolveMentionRowAction({ hasChildren: false, attachable: false })).toBe('attach-value');
		expect(resolveMentionRowAction({ hasChildren: false, attachable: true })).toBe('attach-value');
	});

	it('a navigable node marked attachable attaches as a category', () => {
		expect(resolveMentionRowAction({ hasChildren: true, attachable: true })).toBe('attach-category');
	});

	it('a navigable node NOT marked attachable browses instead of attaching', () => {
		expect(resolveMentionRowAction({ hasChildren: true, attachable: false })).toBe('browse');
	});

	it('a navigable node with attachable omitted defaults to browse', () => {
		expect(resolveMentionRowAction({ hasChildren: true })).toBe('browse');
	});

	it('never returns attach-category for a leaf, even if attachable is (incorrectly) set true', () => {
		// This is the exact shape of the regression this helper exists to prevent:
		// a value must never be routed through the category-attach branch.
		const result = resolveMentionRowAction({ hasChildren: false, attachable: true });
		expect(result).not.toBe('attach-category');
		expect(result).toBe('attach-value');
	});
});
