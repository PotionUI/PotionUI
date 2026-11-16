import { describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { callKind, callKindAsync, type StepKind } from '$lib/editors/stepKinds';
import { railFor, railGroups, railTools } from '$lib/editors/railTools';
import { pluginOwner } from '$lib/editors/registry';

const PLUGIN_KIND = { id: 'acme.k', editor: 'image', pluginId: 'acme' } as unknown as StepKind<any>;

function watch(body: () => void) {
	let runs = 0;
	const stop = $effect.root(() => {
		$effect(() => {
			runs += 1;
			body();
		});
	});
	flushSync();
	return { runs: () => runs, stop };
}

describe('read side plugin hooks called from reactive code', () => {
	it('keeps the caller subscribed to state a plugin step kind reads', () => {
		const shared = $state({ value: 0 });
		const run = watch(() => void callKind(PLUGIN_KIND, 'state', () => shared.value, 0));
		shared.value = 1;
		flushSync();
		expect(run.runs()).toBe(2);
		run.stop();
	});

	it('keeps the caller subscribed to state an async plugin step kind reads before its first await', () => {
		const shared = $state({ value: 0 });
		const run = watch(() => void callKindAsync(PLUGIN_KIND, 'preview', async () => shared.value, 0));
		shared.value = 1;
		flushSync();
		expect(run.runs()).toBe(2);
		run.stop();
	});

	it('keeps the caller subscribed to state a plugin tool reads in available', () => {
		const shared = $state({ value: 0 });
		const dropGroup = railGroups.register({ id: 'acme.g', editor: 'image', label: 'G', order: 1 } as any, pluginOwner('acme'));
		const drop = railTools.register(
			{ id: 'acme.t', editor: 'image', group: 'acme.g', label: 'T', icon: 'extension', available: () => ({ ok: shared.value >= 0 }) } as any,
			pluginOwner('acme')
		);
		const run = watch(() => void railFor('image', {} as any));
		shared.value = 1;
		flushSync();
		expect(run.runs()).toBe(2);
		run.stop();
		drop();
		dropGroup();
	});
});
