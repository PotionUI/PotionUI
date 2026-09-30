// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { writable } from 'svelte/store';

const scopeResponse = {
	model_id: 'm1',
	slug: 'openrouter~veo',
	label: 'Veo',
	driver: 'cloud.openrouter',
	scoped: false,
	preset_ids: [] as string[],
	presets: [],
	candidates: [{ id: 'p-vid', title: 'Cloud Video' }]
};

vi.mock('$lib/services/admin-api', () => ({
	getCloudModelScope: vi.fn(),
	setCloudModelScope: vi.fn()
}));
vi.mock('$lib/services/api/index', () => ({ api: new Proxy({}, { get: () => vi.fn().mockResolvedValue({ success: true, data: {} }) }) }));

const current = vi.hoisted(() => ({ model: null as unknown, store: null as null | { set: (value: unknown) => void }, load: null as null | ReturnType<typeof import('vitest').vi.fn> }));

vi.mock('$lib/components/modals/model-details/modelDetailsController', () => ({
	createAdminModelDetailsController: () => {
		const model = writable(current.model);
		current.store = model;
		return {
			model,
			loading: writable(false),
			currentImageIndex: writable(0),
			imageFiles: writable([]),
			displayName: writable('Veo 3.1'),
			selectedTags: writable([]),
			selectedTagIds: writable([]),
			availability: writable(null),
			availabilityLoading: writable(false),
			load: (current.load = vi.fn()),
			saveType: vi.fn(),
			saveDescription: vi.fn().mockResolvedValue(undefined),
			savePromptingGuidance: vi.fn().mockResolvedValue(undefined),
			updateTags: vi.fn(),
			prevImage: vi.fn(),
			nextImage: vi.fn(),
			handlePrimaryPreviewChange: vi.fn()
		};
	}
}));

const stub = async () => ({ default: (await import(/* @vite-ignore */ '../../tests/component/stubs/BlankStub.svelte')).default });
for (const name of [
	'ModelMediaViewer',
	'ModelPreviewGallery',
	'ModelGenerationsCard',
	'ModelAttributesCard',
	'ModelInfoCard',
	'ModelFilesCard',
	'ModelTechnicalDetailsCard',
	'ModelAvailabilityCard',
	'ModelMirrorsCard'
]) {
	vi.doMock(`$lib/components/modals/model-details/${name}.svelte`, stub);
}
vi.doMock('$lib/components/recipes/ModelOtherVariants.svelte', stub);
vi.doMock('$lib/components/assignment/AssignmentCard.svelte', stub);

const adminApi = await import('$lib/services/admin-api');
const { default: ModelDetailHost } = await import('./stubs/ModelDetailHost.svelte');
const { default: ModelDetailPage } = await import('../../src/routes/admin/components/models/ModelDetailPage.svelte');

function model(overrides: Record<string, unknown> = {}) {
	return {
		id: 'm1',
		filename: 'openrouter~veo',
		model_type: 'cloud',
		sha256: null,
		is_directory: false,
		providers: [{ provider: 'cloud.openrouter', tags: [] }],
		files: [],
		description: '',
		prompting_guidance: '',
		model_metadata: {},
		...overrides
	};
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let instance: object | undefined;
let target: HTMLElement | undefined;

async function mountPage(m: unknown) {
	current.model = m;
	target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(ModelDetailPage, {
		target,
		props: { modelId: 'm1', onBack: () => {}, onDeleted: () => {}, onAssignChanged: () => {} }
	});
	await settle();
	return target;
}

afterEach(() => {
	if (instance) unmount(instance as never);
	target?.remove();
	instance = undefined;
	document.body.innerHTML = '';
	vi.resetAllMocks();
});

describe('admin model detail for cloud models', () => {
	it('shows Allowed in presets, drops file-only sections, and saves through the footer', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValue({ success: true, data: scopeResponse });
		vi.mocked(adminApi.setCloudModelScope).mockResolvedValue({
			success: true,
			data: { ...scopeResponse, scoped: true, preset_ids: ['p-vid'], presets: [{ id: 'p-vid', title: 'Cloud Video', engine: 'cloud', driver: 'cloud.openrouter', missing: false, compatible: true }] }
		});
		const root = await mountPage(model());
		const text = () => root.textContent ?? '';
		expect(text()).toContain('Allowed in presets');
		expect(text()).not.toContain('Model type');
		expect(text()).not.toContain('Availability');
		expect(adminApi.getCloudModelScope).toHaveBeenCalledWith('m1');

		root.querySelector<HTMLInputElement>('input[type="text"]')!.click();
		await settle();
		Array.from(document.body.querySelectorAll<HTMLButtonElement>('[role="option"]')).find((b) => b.textContent?.includes('Cloud Video'))!.click();
		await settle();
		expect(root.querySelector('[data-detail-footer]')?.textContent).toContain('unsaved');

		const save = Array.from(root.querySelectorAll<HTMLButtonElement>('[data-detail-footer] button')).find((b) => b.textContent?.includes('Save'))!;
		save.click();
		await settle();
		expect(adminApi.setCloudModelScope).toHaveBeenCalledWith('m1', ['p-vid']);
	});

	async function addPreset(root: HTMLElement, title: string) {
		root.querySelector<HTMLInputElement>('input[type="text"]')!.click();
		await settle();
		Array.from(document.body.querySelectorAll<HTMLButtonElement>('[role="option"]')).find((b) => b.textContent?.includes(title))!.click();
		await settle();
	}

	const footerButton = (root: HTMLElement, label: string) =>
		Array.from(root.querySelectorAll<HTMLButtonElement>('[data-detail-footer] button')).find((b) => b.textContent?.includes(label))!;

	it('discard removes an added preset and leaves the footer clean', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValue({ success: true, data: scopeResponse });
		const root = await mountPage(model());
		await addPreset(root, 'Cloud Video');
		expect(root.querySelectorAll('[data-scope-row]')).toHaveLength(1);
		expect(root.querySelector('[data-detail-footer]')?.textContent).toContain('unsaved');
		footerButton(root, 'Discard').click();
		await settle();
		expect(root.querySelectorAll('[data-scope-row]')).toHaveLength(0);
		expect(root.querySelector('[data-detail-footer]')?.textContent).not.toContain('unsaved');
	});

	it('a rejected save keeps the footer unsaved and shows the alert', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValue({ success: true, data: scopeResponse });
		vi.mocked(adminApi.setCloudModelScope).mockRejectedValue({
			response: { status: 422, data: { message: "Preset 'Cloud Video' cannot use this model." } }
		});
		const root = await mountPage(model());
		await addPreset(root, 'Cloud Video');
		footerButton(root, 'Save').click();
		await settle();
		expect(root.querySelector('[role="alert"]')?.textContent).toContain('cannot use this model');
		expect(root.querySelector('[data-detail-footer]')?.textContent).toContain('unsaved');
		expect(root.querySelectorAll('[data-scope-row]')).toHaveLength(1);
	});

	it('does not reload the model or the scope when the selected model object is replaced with the same id', async () => {
		vi.mocked(adminApi.getCloudModelScope).mockResolvedValue({ success: true, data: scopeResponse });
		current.model = model();
		target = document.createElement('div');
		document.body.appendChild(target);
		const host = mount(ModelDetailHost, { target, props: { id: 'm1' } }) as { replaceSelected: () => void };
		instance = host;
		await settle();
		await addPreset(target, 'Cloud Video');
		host.replaceSelected();
		await settle();
		expect(current.load).toHaveBeenCalledTimes(1);
		expect(adminApi.getCloudModelScope).toHaveBeenCalledTimes(1);
		expect(target.querySelectorAll('[data-scope-row]')).toHaveLength(1);
	});

	it('does not show the aside or fetch the scope for a file model', async () => {
		const root = await mountPage(model({ model_type: 'checkpoint', filename: 'a.safetensors', sha256: 'abc', providers: [] }));
		expect(root.textContent).not.toContain('Allowed in presets');
		expect(root.textContent).toContain('Model type');
		expect(root.textContent).toContain('Availability');
		expect(adminApi.getCloudModelScope).not.toHaveBeenCalled();
	});
});
