// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import PromptWeightsNotice from '../../src/routes/generate/components/PromptWeightsNotice.svelte';

let instance: Record<string, unknown> | undefined;

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

function render(props: { prompts: { prompt?: string }[]; supported?: boolean }) {
	instance = mount(PromptWeightsNotice, { target: document.body, props });
	flushSync();
	return document.body.textContent ?? '';
}

describe('PromptWeightsNotice', () => {
	it('tells the user weights are ignored when the model cannot honour them', () => {
		const text = render({ prompts: [{ prompt: 'a (red:1.3) fox' }], supported: false });
		expect(text).toContain("aren't supported by this model");
	});

	it('stays hidden without weight syntax', () => {
		expect(render({ prompts: [{ prompt: 'a red fox' }], supported: false })).toBe('');
	});

	it('stays hidden when the model honours weights', () => {
		expect(render({ prompts: [{ prompt: 'a (red:1.3) fox' }], supported: true })).toBe('');
	});

	it('looks at every prompt in a batch', () => {
		const text = render({ prompts: [{ prompt: 'plain' }, { prompt: 'x (y:0.8)' }], supported: false });
		expect(text).toContain('ignored');
	});
});
