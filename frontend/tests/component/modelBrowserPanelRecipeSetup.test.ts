// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

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
		getPresetSlotVariants: vi.fn().mockResolvedValue({ gpu: {}, slots: [] })
	}
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: { subscribe: (fn: (v: unknown) => void) => (fn(authState), () => {}) }
}));

vi.mock('$lib/utils/logger', () => ({
	logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { invalidatePresets } = await import('$lib/stores/presetsCatalog');
const { default: ModelBrowserPanel } = await import('../../src/lib/components/form-fields/ModelBrowserPanel.svelte');
const { createClassComponent } = await import('svelte/legacy');

const EMPTY = { success: true, data: { models: [], total: 0, availability_indexed: true } };

function mountPanel(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: ModelBrowserPanel as never,
		target,
		props: { modelType: 'diffusion_model', presetId: 'krea2', required: true, onSelect: vi.fn(), ...props }
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

function setup(recipes: Array<{ id: string; name: string }>) {
	vi.mocked(api.getPresetModels).mockResolvedValue(EMPTY as never);
	vi.mocked(api.getUnindexedModelsCount).mockResolvedValue({ success: true, data: { by_type: {} } } as never);
	vi.mocked(api.listPresets).mockResolvedValue({
		success: true,
		data: [
			{
				id: 'krea2',
				name: 'Krea-2',
				version: '1',
				tags: [],
				recipes: recipes.map((r) => ({ ...r, readiness: 'available', total_download_bytes: null }))
			}
		]
	} as never);
}

let mounted: ReturnType<typeof mountPanel> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	invalidatePresets();
	authState.user = null;
});

describe('ModelBrowserPanel empty state for a preset with no installed model', () => {
	it('offers an admin "Set up with recipe" linking to the recipe', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([{ id: 'krea2-starter', name: 'Krea-2 Starter' }]);
		mounted = mountPanel({});
		await settle();

		const hint = mounted.target.querySelector('[data-model-setup-hint]') as HTMLElement;
		const link = hint.querySelector('a') as HTMLAnchorElement;
		expect(link.textContent?.trim()).toBe('Set up with recipe');
		expect(link.getAttribute('href')).toBe('/admin?tab=recipes&id=krea2-starter');
	});

	it('tells a non-admin an admin can set it up', async () => {
		authState.user = { account_type: 'USER' };
		setup([]);
		mounted = mountPanel({});
		await settle();

		const hint = mounted.target.querySelector('[data-model-setup-hint]') as HTMLElement;
		expect(hint.textContent).toContain('An admin can set it up');
		expect(hint.querySelector('a')).toBeNull();
		expect(api.listPresets).not.toHaveBeenCalled();
	});

	it('shows nothing extra for an admin when no recipe covers the preset', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([]);
		mounted = mountPanel({});
		await settle();

		expect(mounted.target.querySelector('[data-model-setup-hint]')).toBeNull();
	});

	it('shows nothing for an optional model field', async () => {
		authState.user = { account_type: 'USER' };
		setup([]);
		mounted = mountPanel({ required: false });
		await settle();

		expect(mounted.target.querySelector('[data-model-setup-hint]')).toBeNull();
	});
});
