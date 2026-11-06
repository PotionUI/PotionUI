// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { flushSync, mount, unmount } from 'svelte';
import LoopToggle from '$lib/components/media/LoopToggle.svelte';
import { loopVideos } from '$lib/stores/loopVideos';

let instance: ReturnType<typeof mount> | undefined;

beforeEach(() => {
	localStorage.clear();
	if (get(loopVideos) === false) loopVideos.toggle();
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

describe('loop toggle', () => {
	it('flips aria-pressed and the shared store on click', () => {
		instance = mount(LoopToggle, { target: document.body });
		flushSync();
		const button = document.body.querySelector<HTMLButtonElement>('button[aria-label="Loop"]')!;
		expect(button.getAttribute('aria-pressed')).toBe('true');
		button.click();
		flushSync();
		expect(button.getAttribute('aria-pressed')).toBe('false');
		button.click();
		flushSync();
		expect(button.getAttribute('aria-pressed')).toBe('true');
	});
});
