// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from './helpers/mount';
import { emulateSyncFocusOutOnRemove } from './helpers/syncFocusOut';
import { keyboardFocus } from './helpers/keyboardFocus';

const { default: TooltipRemovalFixture } = await import('./fixtures/TooltipRemovalFixture.svelte');

let restore: (() => void) | undefined;
let uncaught: unknown[] = [];
const collect = (event: ErrorEvent) => {
	uncaught.push(event.error ?? event.message);
	event.preventDefault();
};

beforeEach(() => {
	uncaught = [];
	window.addEventListener('error', collect);
	restore = emulateSyncFocusOutOnRemove();
});

afterEach(() => {
	restore?.();
	window.removeEventListener('error', collect);
	document.body.innerHTML = '';
});

describe('a focused Tooltip-wrapped element that is removed during an update', () => {
	it('closes its tooltip without writing state mid-flush', async () => {
		const view = mountComponent(TooltipRemovalFixture, { show: true });
		const button = document.body.querySelector<HTMLButtonElement>('[data-removable]')!;
		keyboardFocus(button);
		await Promise.resolve();
		flushSync();
		expect(document.body.querySelector('[role="tooltip"]')).not.toBeNull();

		expect(() => view.rerender({ show: false })).not.toThrow();
		await Promise.resolve();
		flushSync();
		await new Promise((resolve) => setTimeout(resolve, 0));

		expect(uncaught).toEqual([]);
		expect(document.body.querySelector('[data-removable]')).toBeNull();
		expect(document.body.querySelector('[role="tooltip"]')).toBeNull();
	});
});
