// @vitest-environment jsdom
//
// `PluginSlot` starts a component load per relevant hook and publishes the
// result when the last one settles. Nothing tied that result to the hook set
// it was started for, so a load still in flight when its plugin is disabled
// (or when `hookName`/`position` moves the slot elsewhere) restored the stale
// component on arrival. These tests drive the real component with a deferred
// resolver so the completion order is controllable.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { PluginHook } from '$lib/stores/plugins';

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: vi.fn()
}));

const { resolvePluginComponent } = await import('$lib/plugin-api/componentResolver');
const { frontendHooks } = await import('$lib/stores/plugins');
const { default: PluginSlot } = await import('$lib/components/plugins/PluginSlot.svelte');
const { default: StubComponent } = await import('./fixtures/PluginSlotStubComponent.svelte');
const { createClassComponent } = await import('svelte/legacy');

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((r) => (resolve = r));
	return { promise, resolve };
}

function hook(pluginId: string, sortOrder = 0, hookName = 'slot.a'): PluginHook {
	return {
		id: 1,
		plugin_id: pluginId,
		hook_name: hookName,
		hook_type: 'frontend',
		component_path: `${pluginId}.js`,
		sort_order: sortOrder
	};
}

const pending = new Map<string, ReturnType<typeof deferred<unknown>>>();

/** Resolves the load `PluginSlot` started for `pluginId` with a mountable stub. */
function completeLoad(pluginId: string) {
	const entry = pending.get(pluginId);
	if (!entry) throw new Error(`no pending load for ${pluginId}`);
	entry.resolve(StubComponent);
}

async function settle() {
	await new Promise((r) => setTimeout(r, 0));
	flushSync();
}

let component: ReturnType<typeof createClassComponent> | null = null;
let target: HTMLDivElement;
const rejections: unknown[] = [];
const onRejection = (reason: unknown) => rejections.push(reason);

function mountSlot(props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({ component: PluginSlot as never, target, props });
	flushSync();
	return component;
}

/** The `data-plugin` attribute of every component the slot currently renders, in DOM order. */
function rendered(): string[] {
	return Array.from(target.querySelectorAll('.plugin-component')).map(
		(el) => el.getAttribute('data-plugin') ?? ''
	);
}

beforeEach(() => {
	pending.clear();
	rejections.length = 0;
	process.on('unhandledRejection', onRejection);
	vi.mocked(resolvePluginComponent).mockImplementation((pluginId: string) => {
		let entry = pending.get(pluginId);
		if (!entry) {
			entry = deferred<unknown>();
			pending.set(pluginId, entry);
		}
		return entry.promise as Promise<unknown>;
	});
});

afterEach(() => {
	process.off('unhandledRejection', onRejection);
	component?.$destroy();
	component = null;
	target?.remove();
	frontendHooks.set({});
	vi.mocked(resolvePluginComponent).mockReset();
});

describe('PluginSlot load ownership', () => {
	it('drops a load whose contribution was removed while it was in flight', async () => {
		frontendHooks.set({ 'slot.a': [hook('alpha')] });
		mountSlot({ hookName: 'slot.a' });

		expect(pending.has('alpha')).toBe(true);

		frontendHooks.set({});
		flushSync();
		expect(rendered()).toEqual([]);

		completeLoad('alpha');
		await settle();

		expect(rendered()).toEqual([]);
	});

	it('keeps the current contribution set when an older load completes last', async () => {
		frontendHooks.set({ 'slot.a': [hook('alpha')] });
		mountSlot({ hookName: 'slot.a' });

		frontendHooks.set({ 'slot.a': [hook('beta')] });
		flushSync();

		completeLoad('beta');
		await settle();
		expect(rendered()).toEqual(['beta']);

		completeLoad('alpha');
		await settle();
		expect(rendered()).toEqual(['beta']);
	});

	it('drops the previous slot load when hookName changes mid-load', async () => {
		frontendHooks.set({
			'slot.a': [hook('alpha', 0, 'slot.a')],
			'slot.b': [hook('beta', 0, 'slot.b')]
		});
		mountSlot({ hookName: 'slot.a' });
		expect(pending.has('alpha')).toBe(true);

		component!.$set({ hookName: 'slot.b' });
		flushSync();

		completeLoad('alpha');
		await settle();
		expect(rendered()).toEqual([]);

		completeLoad('beta');
		await settle();
		expect(rendered()).toEqual(['beta']);
	});

	it('drops the previous slot load when position changes mid-load', async () => {
		frontendHooks.set({
			'slot.a': [
				{ ...hook('alpha'), position: 'left' },
				{ ...hook('beta'), position: 'right' }
			]
		});
		mountSlot({ hookName: 'slot.a', position: 'left' });
		expect(pending.has('alpha')).toBe(true);

		component!.$set({ position: 'right' });
		flushSync();

		completeLoad('alpha');
		await settle();
		expect(rendered()).toEqual([]);

		completeLoad('beta');
		await settle();
		expect(rendered()).toEqual(['beta']);
	});

	it('publishes nothing and raises nothing when destroyed before completion', async () => {
		frontendHooks.set({ 'slot.a': [hook('alpha')] });
		mountSlot({ hookName: 'slot.a' });

		component!.$destroy();
		component = null;

		completeLoad('alpha');
		await settle();

		expect(document.querySelectorAll('.plugin-component')).toHaveLength(0);
		expect(rejections).toEqual([]);
	});

	it('renders a completed load in sort order', async () => {
		frontendHooks.set({ 'slot.a': [hook('alpha', 2), hook('beta', 1)] });
		mountSlot({ hookName: 'slot.a' });

		// Loads run in sort order, one await at a time - `alpha`'s resolver call
		// only happens once `beta`'s has settled.
		completeLoad('beta');
		await settle();
		completeLoad('alpha');
		await settle();

		expect(rendered()).toEqual(['beta', 'alpha']);
		expect(target.querySelectorAll('.stub-plugin-component')).toHaveLength(2);
	});
});
