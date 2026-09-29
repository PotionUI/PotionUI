// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import type { SetupConsentVariant } from '$lib/services/api/setup';
import type { RecipeSlotVariants } from '$lib/services/api/recipes';

const authState = vi.hoisted(() => ({ user: null as null | { account_type: string } }));

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModels: vi.fn(),
		getPresetModels: vi.fn(),
		getTags: vi.fn(),
		getModelDownloadStatus: vi.fn(),
		startModelDownload: vi.fn(),
		getUnindexedModelsCount: vi.fn(),
		listPresets: vi.fn(),
		getPresetSlotVariants: vi.fn(),
		downloadSlotVariant: vi.fn()
	}
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: { subscribe: (fn: (v: unknown) => void) => (fn(authState), () => {}) }
}));

vi.mock('$lib/utils/logger', () => ({
	logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
	getErrorMessage: (error: unknown) => String(error)
}));

const { api } = await import('$lib/services/api/index');
const { invalidatePresets } = await import('$lib/stores/presetsCatalog');
const { default: ModelBrowserPanel } = await import('../../src/lib/components/form-fields/ModelBrowserPanel.svelte');
const { createClassComponent } = await import('svelte/legacy');

function variant(id: string, precision: string, overrides: Partial<SetupConsentVariant> = {}): SetupConsentVariant {
	return {
		id,
		label: id,
		precision,
		filename: `${id}.safetensors`,
		size_bytes: 13_140_000_000,
		installed: false,
		gated: false,
		license_url: null,
		uploader: 'Comfy-Org',
		source: 'huggingface',
		repo_id: 'Comfy-Org/Krea-2',
		source_url: 'https://huggingface.co/Comfy-Org/Krea-2',
		is_recipe_default: false,
		fast: true,
		recommended: false,
		note: null,
		...overrides
	};
}

function slot(variants: SetupConsentVariant[]): RecipeSlotVariants {
	return {
		id: 'krea2-diffusion-model',
		label: 'Krea-2 Turbo (DiT)',
		kind: 'diffusion_model',
		model_type: 'diffusion_model',
		required: true,
		variants,
		recommended_variant_id: 'fp8',
		reason: 'Fits your 24 GB',
		recipe_id: 'krea2-starter',
		recipe_name: 'Krea-2 Starter',
		suggested_variant_id: 'fp8',
		suggested_reason: 'Fits your 24 GB'
	};
}

function mountPanel(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: ModelBrowserPanel as never,
		target,
		props: { modelType: 'diffusion_model', presetId: 'krea2', onSelect: vi.fn(), ...props }
	});
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function setup(slots: RecipeSlotVariants[], installed = true) {
	const models = installed ? [{ id: 'm1', name: 'Installed', filename: 'installed.safetensors' }] : [];
	vi.mocked(api.getPresetModels).mockResolvedValue({ success: true, data: { models, total: models.length } } as never);
	vi.mocked(api.getUnindexedModelsCount).mockResolvedValue({ success: true, data: { by_type: {} } } as never);
	vi.mocked(api.getPresetSlotVariants).mockResolvedValue({ gpu: {} as never, slots });
}

let mounted: ReturnType<typeof mountPanel> | undefined;

beforeEach(() => {
	localStorage.clear();
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	invalidatePresets();
	authState.user = null;
	localStorage.clear();
});

const KEY = 'potionui:modelPicker:suggestedOpen';

function toggle(): HTMLButtonElement | null {
	return mounted!.target.querySelector('[data-picker-suggested-toggle]');
}

function badgeText(): string {
	return toggle()!.textContent!.replace('Suggested', '').trim();
}

function panelBody(): HTMLElement {
	return mounted!.target.querySelector('#model-picker-suggested') as HTMLElement;
}

describe('model picker Suggested fold', () => {
	it('is collapsed by default and shows the suggestion count', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8'), variant('bf16', 'bf16')])]);
		mounted = mountPanel();
		await settle();

		expect(toggle()!.getAttribute('aria-expanded')).toBe('false');
		expect(toggle()!.getAttribute('aria-controls')).toBe('model-picker-suggested');
		expect(toggle()!.textContent).toContain('Suggested');
		expect(badgeText()).toBe('2');
		expect(panelBody().hidden).toBe(true);
	});

	it('toggles open and closed and remembers the choice', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8'), variant('bf16', 'bf16')])]);
		mounted = mountPanel();
		await settle();

		toggle()!.click();
		await settle();
		expect(toggle()!.getAttribute('aria-expanded')).toBe('true');
		expect(panelBody().hidden).toBe(false);
		expect(localStorage.getItem(KEY)).toBe('1');

		toggle()!.click();
		await settle();
		expect(panelBody().hidden).toBe(true);
		expect(localStorage.getItem(KEY)).toBe('0');
	});

	it('starts expanded when the stored choice is open', async () => {
		localStorage.setItem(KEY, '1');
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8'), variant('bf16', 'bf16')])]);
		mounted = mountPanel();
		await settle();

		expect(toggle()!.getAttribute('aria-expanded')).toBe('true');
		expect(panelBody().hidden).toBe(false);
	});

	it('still renders when storage throws', async () => {
		const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
			throw new Error('blocked');
		});
		const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new Error('blocked');
		});
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8'), variant('bf16', 'bf16')])]);
		mounted = mountPanel();
		await settle();

		expect(toggle()!.getAttribute('aria-expanded')).toBe('false');
		toggle()!.click();
		await settle();
		expect(toggle()!.getAttribute('aria-expanded')).toBe('true');
		get.mockRestore();
		set.mockRestore();
	});

	it('is hidden entirely when there are no suggestions', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8', { installed: true })])]);
		mounted = mountPanel();
		await settle();

		expect(toggle()).toBeNull();
		expect(panelBody().hidden).toBe(true);
	});

	it('counts recommended rows into the section and hides them while collapsed', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8'), variant('bf16', 'bf16')])]);
		const recommendations = [
			{ name: 'a.safetensors', link: 'https://example.com/a', sha256: 'x' },
			{ name: 'b.safetensors', link: 'https://example.com/b', sha256: 'y' }
		];
		mounted = mountPanel({ recommendations });
		await settle();

		expect(badgeText()).toBe('4');
		expect(panelBody().hidden).toBe(true);

		toggle()!.click();
		await settle();
		expect(panelBody().hidden).toBe(false);
		expect(panelBody().textContent).toContain('a.safetensors');
		expect(panelBody().textContent).toContain('b.safetensors');
	});

	it('stays collapsed by default even when no model is installed and still shows the empty text', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8'), variant('bf16', 'bf16')])], false);
		mounted = mountPanel();
		await settle();

		expect(toggle()!.getAttribute('aria-expanded')).toBe('false');
		expect(panelBody().hidden).toBe(true);
		expect(mounted.target.textContent).toContain('No models found');
	});

	it('renders variant rows and suggested downloads as one list with attribution last', async () => {
		localStorage.setItem(KEY, '1');
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8'), variant('bf16', 'bf16')])]);
		mounted = mountPanel({
			recommendations: [{ name: 'a.safetensors', link: 'https://example.com/a', sha256: 'x' }]
		});
		await settle();

		const body = panelBody();
		const items = Array.from(
			body.querySelectorAll('[data-slot-variant], [data-recommended-download], [data-variant-attribution]')
		).map((el) =>
			el.hasAttribute('data-variant-attribution')
				? 'attribution'
				: el.hasAttribute('data-recommended-download')
					? 'download'
					: 'variant'
		);
		expect(items).toEqual(['variant', 'variant', 'download', 'attribution']);
	});

	it('count equals the number of variant rows', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8'), variant('bf16', 'bf16'), variant('int8', 'int8')])]);
		mounted = mountPanel();
		await settle();

		const rows = mounted.target.querySelectorAll('[data-slot-variant]').length;
		expect(rows).toBe(3);
		expect(badgeText()).toBe(String(rows));
	});

	it('does not count recommendations until the first model fetch has finished', async () => {
		authState.user = { account_type: 'USER' };
		let release: (value: unknown) => void = () => {};
		vi.mocked(api.getPresetModels).mockReturnValue(new Promise((resolve) => (release = resolve)) as never);
		vi.mocked(api.getUnindexedModelsCount).mockResolvedValue({ success: true, data: { by_type: {} } } as never);
		mounted = mountPanel({
			recommendations: [{ name: 'a.safetensors', link: 'https://example.com/a', sha256: 'x' }]
		});
		await settle();
		expect(toggle()).toBeNull();

		release({ success: true, data: { models: [], total: 0 } });
		await settle();
		expect(badgeText()).toBe('1');
	});

	it('hides the offers while a search is active', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8')])]);
		mounted = mountPanel({ searchQuery: 'krea' });
		await settle();

		expect(toggle()).toBeNull();
		expect(mounted.target.querySelector('[data-slot-variant]')).toBeNull();
	});

	it('hides the offers on the Collections tab', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8')])]);
		mounted = mountPanel();
		await settle();
		expect(toggle()).not.toBeNull();

		const collections = Array.from(mounted.target.querySelectorAll('button')).find((b) =>
			b.textContent?.includes('Collections')
		) as HTMLButtonElement;
		collections.click();
		await settle();
		expect(toggle()).toBeNull();
	});

	it('keeps installed models listed while the fold is collapsed', async () => {
		localStorage.setItem(KEY, '0');
		authState.user = { account_type: 'USER' };
		vi.mocked(api.getPresetModels).mockResolvedValue({
			success: true,
			data: { models: [{ id: 'm1', name: 'Installed', filename: 'installed.safetensors' }], total: 1 }
		} as never);
		vi.mocked(api.getUnindexedModelsCount).mockResolvedValue({ success: true, data: { by_type: {} } } as never);
		mounted = mountPanel({
			recommendations: [{ name: 'a.safetensors', link: 'https://example.com/a', sha256: 'x' }]
		});
		await settle();
		expect(mounted.target.textContent).toContain('Installed');
	});

	it('marks the admin-only parts for an admin', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8')])], false);
		vi.mocked(api.listPresets).mockResolvedValue({
			success: true,
			data: [{ id: 'krea2', name: 'Krea-2', version: '1', tags: [], recipes: [{ id: 'r1', name: 'Krea-2 Starter', readiness: 'available', total_download_bytes: null }] }]
		} as never);
		mounted = mountPanel({ required: true });
		await settle();

		expect(toggle()!.querySelector('[data-admin-only-mark]')).not.toBeNull();
		expect(mounted.target.querySelectorAll('[data-admin-only-mark]').length).toBeGreaterThanOrEqual(1);
		expect(
			mounted.target.querySelector('[data-admin-only-mark][aria-label="Only admins see this"]')
		).not.toBeNull();
	});

	it('renders no admin-only mark for a regular user', async () => {
		authState.user = { account_type: 'USER' };
		setup([slot([variant('fp8', 'fp8')])], false);
		mounted = mountPanel({
			required: true,
			recommendations: [{ name: 'a.safetensors', link: 'https://example.com/a', sha256: 'x' }]
		});
		await settle();

		expect(toggle()).not.toBeNull();
		expect(mounted.target.querySelector('[data-admin-only-mark]')).toBeNull();
	});
});
