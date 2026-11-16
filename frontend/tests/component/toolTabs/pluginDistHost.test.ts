import { afterEach, describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import PluginDistHost from '$lib/components/plugins/PluginDistHost.svelte';
import { shared } from './pluginDistState.svelte';

afterEach(() => {
	shared.mounts = 0;
	document.body.innerHTML = '';
});

class StateTouchingComponent {
	static destroyed = 0;
	constructor({ target }: { target: HTMLElement }) {
		shared.mounts = shared.mounts + 1;
		const root = document.createElement('div');
		root.setAttribute('data-dist-mounted', String(shared.mounts));
		target.appendChild(root);
	}
	$set() {
		shared.mounts = shared.mounts + 1;
	}
	$destroy() {
		StateTouchingComponent.destroyed += 1;
		shared.mounts = shared.mounts + 1;
	}
}

describe('PluginDistHost', () => {
	it('mounts a plugin component that reads and writes reactive state in its constructor exactly once', () => {
		const view = mountComponent(PluginDistHost, { component: StateTouchingComponent });
		flushSync();
		expect(view.target.querySelectorAll('[data-dist-mounted]')).toHaveLength(1);
		expect(view.target.querySelector('[data-dist-mounted]')?.getAttribute('data-dist-mounted')).toBe('1');
	});

	it('forwards prop changes to a plugin whose $set touches reactive state without looping', () => {
		const view = mountComponent(PluginDistHost, { component: StateTouchingComponent, label: 'a' });
		view.rerender({ label: 'b' });
		flushSync();
		expect(view.target.querySelectorAll('[data-dist-mounted]')).toHaveLength(1);
	});

	it('destroys the plugin component when the host goes away, even if destroy touches reactive state', () => {
		const before = StateTouchingComponent.destroyed;
		const view = mountComponent(PluginDistHost, { component: StateTouchingComponent });
		view.destroy();
		flushSync();
		expect(StateTouchingComponent.destroyed).toBe(before + 1);
	});
});
