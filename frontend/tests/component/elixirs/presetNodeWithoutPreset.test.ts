// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { writable } from 'svelte/store';
import NodePanelLiveFixture from '../fixtures/NodePanelLiveFixture.svelte';

let component: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	document.body.innerHTML = '';
});

it('a new preset step without a preset asks for one instead of loading a form forever', () => {
	const node = writable({ id: 'n1', kind: 'preset', title: null, preset_id: null, mode: null, overrides: {}, assets: [] });
	const target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(NodePanelLiveFixture, { target, props: { node, onwriterchange: () => {}, onrename: () => {} } });
	flushSync();

	expect(target.textContent).toContain('Pick a preset and mode to see its settings.');
	expect(target.textContent).not.toContain('Loading Form');
});
