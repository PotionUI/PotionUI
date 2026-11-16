import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const state = vi.hoisted(() => ({ fail: true }));

vi.mock('$lib/utils/logger', () => ({ logger: { error: vi.fn(), warn: vi.fn(), debug: vi.fn() }, getErrorMessage: () => 'x' }));
vi.mock('$lib/services/api/mediaTools', () => ({
	listMediaTools: async () => {
		if (state.fail) throw new Error('500');
		return [{ id: 'acme.grain', label: 'Grain', icon: 'x', scopes: ['editor'], accepts: ['image'], cardinality: 'one_to_one', produces: 'same', runnable: true, params: [] }];
	}
}));

async function load() {
	vi.resetModules();
	const [catalog, ready, image, stack] = await Promise.all([
		import('$lib/media-tools/catalog'),
		import('./ready'),
		import('$lib/tool-tabs/image/stepKinds'),
		import('$lib/tool-tabs/frame/editStack')
	]);
	const step = stack.appliedSteps(
		stack.apply(stack.beginEdit(stack.createStack(), { tool: 'acme.grain', icon: 'x', label: 'Grain', summary: '', params: { values: {} } }))
	)[0];
	return { catalog, ready, image, step };
}

describe('Loading ends when a source fails', () => {
	beforeEach(() => {
		state.fail = true;
	});

	it('turns an unknown step into Not available when the catalog fails, and upgrades it after a later success', async () => {
		const m = await load();
		m.ready.markEditorExtensionsReady();
		expect(m.image.imageRowInfo(m.step)?.state).toEqual({ kind: 'busy', label: 'Loading' });
		await m.catalog.loadMediaToolCatalog();
		expect(get(m.catalog.mediaToolCatalogRevision)).toBeGreaterThan(0);
		expect(m.image.imageRowInfo(m.step)?.state).toEqual({ kind: 'skipped', tone: 'muted', label: 'Not available' });
		state.fail = false;
		await m.catalog.refreshMediaToolCatalog();
		expect(m.image.imageRowInfo(m.step)).toBeNull();
	});

	it('stays Loading until the first extension refresh has finished, successful or not', async () => {
		const m = await load();
		m.catalog.mediaToolCatalogRevision.set(1);
		expect(m.image.imageRowInfo(m.step)?.state).toEqual({ kind: 'busy', label: 'Loading' });
		m.ready.markEditorExtensionsReady();
		expect(m.image.imageRowInfo(m.step)?.state).toMatchObject({ kind: 'skipped', label: 'Not available' });
	});
});
