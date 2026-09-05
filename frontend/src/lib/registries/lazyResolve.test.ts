import { describe, it, expect, vi, beforeEach } from 'vitest';

const revisions = new Map<string, string>();
const loads = new Map<string, { promise: Promise<any>; resolve: (value: any) => void }>();

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: (_pluginId: string, asset: string) => {
		let entry = loads.get(asset);
		if (!entry) {
			let resolve!: (value: any) => void;
			const promise = new Promise<any>((r) => (resolve = r));
			entry = { promise, resolve };
			loads.set(asset, entry);
		}
		return entry.promise;
	},
	getPluginRevision: (pluginId: string) => revisions.get(pluginId) ?? ''
}));

import { artifactRendererRegistry } from './artifactRendererRegistry';
import { registerModelView, unregisterModelView, listModelViewSections } from './modelViewRegistry';
import { registerFieldComponent, unregisterFieldComponent, resolveFieldComponent } from '$lib/fields/registry';
import { pluginOwner } from './registry';

/** Settles the in-flight load for `asset`. */
function completeLoad(asset: string, component: unknown) {
	const entry = loads.get(asset);
	if (!entry) throw new Error(`no pending load for ${asset}`);
	entry.resolve(component);
}

async function settle() {
	for (let i = 0; i < 5; i++) await Promise.resolve();
}

const PLUGIN = 'example';
const OWNER = pluginOwner(PLUGIN);
const ASSET = 'FakeArtifact.js';
const REVISED_ASSET = 'FakeArtifact.v2.js';
const OldComponent = { name: 'old' };
const NewComponent = { name: 'new' };

beforeEach(() => {
	loads.clear();
	revisions.clear();
	revisions.set(PLUGIN, 'rev-1');
	for (const key of artifactRendererRegistry.keys()) artifactRendererRegistry.unregister(key);
});

describe('lazy resolve publication guard', () => {
	it('does not publish a component whose plugin was unregistered mid-load', async () => {
		artifactRendererRegistry.register('fake', { pluginId: PLUGIN, asset: ASSET });
		const inFlight = artifactRendererRegistry.resolve('fake');
		await settle();

		artifactRendererRegistry.unregister('fake', OWNER);
		completeLoad(ASSET, OldComponent);

		expect(await inFlight).toBeNull();
		expect(artifactRendererRegistry.has('fake')).toBe(false);
		expect(await artifactRendererRegistry.resolve('fake')).toBeNull();
	});

	it('resolves the current revision’s component when the entry is revised mid-load', async () => {
		artifactRendererRegistry.register('fake', { pluginId: PLUGIN, asset: ASSET });
		const inFlight = artifactRendererRegistry.resolve('fake');
		await settle();

		revisions.set(PLUGIN, 'rev-2');
		artifactRendererRegistry.register('fake', { pluginId: PLUGIN, asset: REVISED_ASSET });
		completeLoad(ASSET, OldComponent);
		await settle();
		completeLoad(REVISED_ASSET, NewComponent);

		expect(await inFlight).toBe(NewComponent);
		expect(await artifactRendererRegistry.resolve('fake')).toBe(NewComponent);
	});

	it('reveals the core registration a removed plugin had shadowed', async () => {
		const CoreComponent = { name: 'core' };
		artifactRendererRegistry.register('fake', { component: CoreComponent });
		artifactRendererRegistry.register('fake', { pluginId: PLUGIN, asset: ASSET });

		const inFlight = artifactRendererRegistry.resolve('fake');
		await settle();

		artifactRendererRegistry.unregister('fake', OWNER);
		completeLoad(ASSET, OldComponent);

		expect(await inFlight).toBe(CoreComponent);
	});

	it('does not cache a transient null, so the next call retries', async () => {
		artifactRendererRegistry.register('fake', { pluginId: PLUGIN, asset: ASSET });

		const failed = artifactRendererRegistry.resolve('fake');
		await settle();
		completeLoad(ASSET, null);
		expect(await failed).toBeNull();

		loads.delete(ASSET);
		const retried = artifactRendererRegistry.resolve('fake');
		await settle();
		completeLoad(ASSET, NewComponent);

		expect(await retried).toBe(NewComponent);
	});

	it('applies the same guard to plugin field components', async () => {
		registerFieldComponent('stars', { pluginId: PLUGIN, asset: ASSET });
		const inFlight = resolveFieldComponent('stars');
		await settle();

		unregisterFieldComponent('stars', OWNER);
		completeLoad(ASSET, OldComponent);

		expect(await inFlight).toBeNull();
		expect(await resolveFieldComponent('stars')).toBeNull();
	});

	it('applies the same guard to model view sections', async () => {
		registerModelView(PLUGIN, 'usage', ASSET);
		const section = listModelViewSections()[0];
		await settle();

		unregisterModelView(PLUGIN, 'usage');
		completeLoad(ASSET, OldComponent);

		expect(await section.component).toBeNull();
		expect(listModelViewSections()).toHaveLength(0);
	});
});
