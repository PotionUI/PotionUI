// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { CloudModelScope } from '$lib/services/admin-api';

vi.mock('$lib/services/admin-api', () => ({
	getCloudModelScope: vi.fn(),
	setCloudModelScope: vi.fn()
}));

const adminApi = await import('$lib/services/admin-api');
const { default: ModelScopeSection } = await import('../../src/routes/admin/components/models/ModelScopeSection.svelte');

function scope(overrides: Partial<CloudModelScope> = {}): CloudModelScope {
	return {
		model_id: 'm1',
		slug: 'openrouter~veo',
		label: 'Veo',
		driver: 'cloud.openrouter',
		scoped: false,
		preset_ids: [],
		presets: [],
		candidates: [
			{ id: 'p-img', title: 'Cloud Image' },
			{ id: 'p-edit', title: 'Cloud Edit' },
			{ id: 'p-vid', title: 'Cloud Video' }
		],
		...overrides
	};
}

function ok<T>(data: T) {
	return { success: true, data };
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

const state = { dirty: false };

async function mountSection() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	state.dirty = false;
	const props = {
		modelId: 'm1',
		get dirty() {
			return state.dirty;
		},
		set dirty(value: boolean) {
			state.dirty = value;
		}
	};
	const instance = mount(ModelScopeSection, { target, props }) as { commit: () => Promise<boolean>; discard: () => void };
	await settle();
	return {
		target,
		instance,
		text: () => target.textContent ?? '',
		rows: () => Array.from(target.querySelectorAll<HTMLElement>('[data-scope-row]')),
		removeButton: (id: string) =>
			target.querySelector<HTMLElement>(`[data-scope-row="${id}"] button[aria-label^="Remove"]`),
		input: () => target.querySelector<HTMLInputElement>('input[type="text"]'),
		destroy: () => {
			unmount(instance as never);
			target.remove();
		}
	};
}

async function pick(label: string, view: Awaited<ReturnType<typeof mountSection>>) {
	view.input()!.click();
	await settle();
	const option = Array.from(document.body.querySelectorAll<HTMLButtonElement>('[role="option"]')).find((b) =>
		b.textContent?.includes(label)
	);
	expect(option, `option ${label}`).toBeTruthy();
	option!.click();
	await settle();
}

let view: Awaited<ReturnType<typeof mountSection>> | undefined;

afterEach(() => {
	view?.destroy();
	view = undefined;
	document.body.innerHTML = '';
	vi.resetAllMocks();
});

describe('ModelScopeSection', () => {
	it('explains that an empty scope means every compatible preset and starts clean', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValue(ok(scope()));
		view = await mountSection();
		expect(view.text()).toContain('offered in every compatible preset');
		expect(view.target.querySelector('[data-scope-empty]')).toBeTruthy();
		expect(view.rows()).toHaveLength(0);
		expect(state.dirty).toBe(false);
	});

	it('lists chosen presets and flags a preset whose file is gone', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValue(
			ok(
				scope({
					scoped: true,
					preset_ids: ['p-img', 'p-gone'],
					presets: [
						{ id: 'p-img', title: 'Cloud Image', engine: 'cloud', driver: 'cloud.openrouter', missing: false, compatible: true },
						{ id: 'p-gone', title: null, engine: null, driver: null, missing: true, compatible: false }
					]
				})
			)
		);
		view = await mountSection();
		expect(view.rows()).toHaveLength(2);
		expect(view.text()).toContain('Cloud Image');
		expect(view.text()).toContain('missing');
		expect(state.dirty).toBe(false);
	});

	it('adds a preset from the candidates, marks dirty, and saves the new list', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValue(ok(scope()));
		const saved = scope({ scoped: true, preset_ids: ['p-edit'], presets: [{ id: 'p-edit', title: 'Cloud Edit', engine: 'cloud', driver: 'cloud.openrouter', missing: false, compatible: true }] });
		vi.mocked(adminApi.setCloudModelScope).mockResolvedValue(ok(saved));
		view = await mountSection();
		await pick('Cloud Edit', view);
		expect(view.rows().map((r) => r.dataset.scopeRow)).toEqual(['p-edit']);
		expect(state.dirty).toBe(true);

		await pick('Cloud Image', view);
		const optionsLeft = () => Array.from(document.body.querySelectorAll('[role="option"]')).map((o) => o.textContent);
		view.input()!.click();
		await settle();
		expect(optionsLeft().join(' ')).not.toContain('Cloud Edit');
		expect(optionsLeft().join(' ')).not.toContain('Cloud Image');

		view.removeButton('p-img')!.click();
		await settle();
		expect(await view.instance.commit()).toBe(true);
		expect(adminApi.setCloudModelScope).toHaveBeenCalledWith('m1', ['p-edit']);
		await settle();
		expect(state.dirty).toBe(false);
	});

	it('removes a chosen preset and discard restores the saved scope', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValue(
			ok(
				scope({
					scoped: true,
					preset_ids: ['p-img'],
					presets: [{ id: 'p-img', title: 'Cloud Image', engine: 'cloud', driver: 'cloud.openrouter', missing: false, compatible: true }]
				})
			)
		);
		view = await mountSection();
		view.removeButton('p-img')!.click();
		await settle();
		expect(view.rows()).toHaveLength(0);
		expect(state.dirty).toBe(true);
		view.instance.discard();
		await settle();
		expect(view.rows()).toHaveLength(1);
		expect(state.dirty).toBe(false);
	});

	it('never sends a missing preset back to the server', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValue(
			ok(
				scope({
					scoped: true,
					preset_ids: ['p-img', 'p-gone'],
					presets: [
						{ id: 'p-img', title: 'Cloud Image', engine: 'cloud', driver: 'cloud.openrouter', missing: false, compatible: true },
						{ id: 'p-gone', title: null, engine: null, driver: null, missing: true, compatible: false }
					]
				})
			)
		);
		vi.mocked(adminApi.setCloudModelScope).mockResolvedValue(ok(scope()));
		view = await mountSection();
		await pick('Cloud Video', view);
		await view.instance.commit();
		expect(adminApi.setCloudModelScope).toHaveBeenCalledWith('m1', ['p-img', 'p-vid']);
	});

	it('shows a 422 message inline and stays dirty', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValue(ok(scope()));
		vi.mocked(adminApi.setCloudModelScope).mockRejectedValue({
			response: { status: 422, data: { error: 'cloud_scope_invalid', message: "Preset 'Cloud Edit' cannot use this model." } }
		});
		view = await mountSection();
		await pick('Cloud Edit', view);
		expect(await view.instance.commit()).toBe(false);
		await settle();
		expect(view.target.querySelector('[role="alert"]')?.textContent).toContain("Preset 'Cloud Edit' cannot use this model.");
		expect(state.dirty).toBe(true);
		expect(view.rows()).toHaveLength(1);
	});

	it('shows an error with a retry when the scope cannot be loaded', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockRejectedValueOnce(new Error('network down'));
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValueOnce(ok(scope()));
		view = await mountSection();
		expect(view.text()).toContain('network down');
		Array.from(view.target.querySelectorAll('button')).find((b) => b.textContent?.includes('Retry'))!.click();
		await settle();
		expect(view.text()).toContain('offered in every compatible preset');
	});

	it('says so when no cloud preset uses the provider', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValue(ok(scope({ candidates: [] })));
		view = await mountSection();
		expect(view.text()).toContain('No cloud presets use this');
		expect(view.input()).toBeNull();
	});
});
