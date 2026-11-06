import { describe, it, expect } from 'vitest';
import { render } from 'svelte/server';
import LiveSyncChoice from './LiveSyncChoice.svelte';
import { previewSyncValue } from '$lib/session/sessionMerge';

describe('previewSyncValue', () => {
	it('shows text, structured values and removals', () => {
		expect(previewSyncValue('prompt', 'a red fox')).toBe('a red fox');
		expect(previewSyncValue('seed', 42)).toBe('42');
		expect(previewSyncValue('formData', { a: 1 })).toBe('{"a":1}');
		expect(previewSyncValue('prompt', '  ')).toBe('(empty)');
		expect(previewSyncValue('prompt', undefined)).toBe('(removed)');
	});
});

describe('LiveSyncChoice', () => {
	const props = { label: 'another window', onTheirs: () => {}, onMine: () => {} };

	it('shows the incoming value so the choice is not blind', () => {
		const out = render(LiveSyncChoice, { props: { ...props, preview: 'a red fox' } }).body;
		expect(out).toContain('a red fox');
		expect(out).toContain('Theirs:');
		expect(out).not.toContain('Show more');
	});

	it('offers an expand control for long values', () => {
		const out = render(LiveSyncChoice, { props: { ...props, preview: 'x'.repeat(200) } }).body;
		expect(out).toContain('Show more');
	});
});
