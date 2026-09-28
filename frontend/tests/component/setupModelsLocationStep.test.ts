// @vitest-environment jsdom
//
// Onboarding's models-location step: shows the currently resolved
// path, applies a new one through the same /api/models/location endpoints
// the admin panel uses, and stays skippable - both a successful Apply and a
// plain Skip must collapse the step without nagging again, while "Change"
// reopens it.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import type { IndexingStatus, ModelsLocationConfig } from '$lib/services/api/models';

vi.mock('$lib/services/api', () => ({
	api: {
		getModelsLocation: vi.fn(),
		applyModelsLocation: vi.fn(),
		getIndexingStatus: vi.fn()
	}
}));

const api = (await import('$lib/services/api')).api;
const { default: ModelsLocationStep } = await import(
	'../../src/routes/setup/components/ModelsLocationStep.svelte'
);
const { indexingStatusStore } = await import('$lib/models-location/indexingStatus.svelte');
const { createClassComponent } = await import('svelte/legacy');

function indexing(overrides: Partial<IndexingStatus> = {}): IndexingStatus {
	return { state: 'idle', ...overrides };
}

function config(overrides: Partial<ModelsLocationConfig> = {}): ModelsLocationConfig {
	return {
		external_path: null,
		overrides: {},
		directories: [
			{
				directory: 'checkpoints',
				target: null,
				linked: false,
				resolved_target: null,
				has_real_files: true
			}
		],
		windows_unsupported: false,
		...overrides
	};
}

function mountStep() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: ModelsLocationStep as never, target, props: {} });
	return {
		target,
		button: (text: string) =>
			Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
				b.textContent?.includes(text)
			),
		input: () => target.querySelector<HTMLInputElement>('#setup-models-external-path'),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountStep> | undefined;

beforeEach(() => {
	vi.mocked(api.getIndexingStatus).mockResolvedValue({ success: true, data: indexing() });
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	localStorage.clear();
	indexingStatusStore.status = null;
});

describe('ModelsLocationStep', () => {
	it('renders the currently resolved path once loaded', async () => {
		vi.mocked(api.getModelsLocation).mockResolvedValue({ success: true, data: config() });

		mounted = mountStep();
		await settle();

		expect(mounted.target.textContent).toContain('models/ (default location in this install)');
		expect(mounted.input()?.value).toBe('');
	});

	it('shows a custom external path when one is already set', async () => {
		vi.mocked(api.getModelsLocation).mockResolvedValue({
			success: true,
			data: config({ external_path: '/mnt/storage/models' })
		});

		mounted = mountStep();
		await settle();

		expect(mounted.target.textContent).toContain('/mnt/storage/models');
		expect(mounted.input()?.value).toBe('/mnt/storage/models');
	});

	it('applies the typed path with no per-type overrides and collapses on success', async () => {
		vi.mocked(api.getModelsLocation).mockResolvedValue({ success: true, data: config() });
		vi.mocked(api.applyModelsLocation).mockResolvedValue({
			success: true,
			data: config({ external_path: '/data/models' })
		});

		mounted = mountStep();
		await settle();

		const input = mounted.input();
		expect(input).toBeTruthy();
		input!.value = '/data/models';
		input!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		mounted.button('Apply')?.click();
		await settle();

		expect(api.applyModelsLocation).toHaveBeenCalledWith('/data/models', undefined);
		// Collapsed: the Apply/Skip buttons are gone, replaced by the compact
		// "Change" readout showing the newly applied path.
		expect(mounted.button('Apply')).toBeFalsy();
		expect(mounted.button('Skip')).toBeFalsy();
		expect(mounted.target.textContent).toContain('/data/models');
		expect(mounted.button('Change')).toBeTruthy();
	});

	it('skip collapses the step without calling apply, keeping the default', async () => {
		vi.mocked(api.getModelsLocation).mockResolvedValue({ success: true, data: config() });

		mounted = mountStep();
		await settle();

		mounted.button('Skip')?.click();
		await settle();

		expect(api.applyModelsLocation).not.toHaveBeenCalled();
		expect(mounted.button('Apply')).toBeFalsy();
		expect(mounted.target.textContent).toContain('models/ (default location in this install)');
		expect(mounted.button('Change')).toBeTruthy();
	});

	it('"Change" reopens the step after a skip', async () => {
		vi.mocked(api.getModelsLocation).mockResolvedValue({ success: true, data: config() });

		mounted = mountStep();
		await settle();

		mounted.button('Skip')?.click();
		await settle();
		mounted.button('Change')?.click();
		await settle();

		expect(mounted.button('Apply')).toBeTruthy();
		expect(mounted.button('Skip')).toBeTruthy();
	});

	it('shows an in-progress run on load, e.g. resumed from a startup index', async () => {
		vi.mocked(api.getModelsLocation).mockResolvedValue({ success: true, data: config() });
		vi.mocked(api.getIndexingStatus).mockResolvedValue({
			success: true,
			data: indexing({ state: 'indexing', processed: 47, total: 128 })
		});

		mounted = mountStep();
		await settle();

		expect(mounted.target.textContent).toContain('47 / 128');
	});

	it('shows scanning as an indeterminate state', async () => {
		vi.mocked(api.getModelsLocation).mockResolvedValue({ success: true, data: config() });
		vi.mocked(api.getIndexingStatus).mockResolvedValue({
			success: true,
			data: indexing({ state: 'scanning', scanned_roots: ['/mnt/storage/models'] })
		});

		mounted = mountStep();
		await settle();

		expect(mounted.target.textContent).toContain('Scanning /mnt/storage/models');
	});

	it('after applying, tells the user indexing continues in the background rather than looking finished', async () => {
		vi.mocked(api.getModelsLocation).mockResolvedValue({ success: true, data: config() });
		vi.mocked(api.applyModelsLocation).mockResolvedValue({
			success: true,
			data: config({ external_path: '/data/models', indexing: indexing({ state: 'scanning' }) })
		});

		mounted = mountStep();
		await settle();

		const input = mounted.input();
		input!.value = '/data/models';
		input!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		mounted.button('Apply')?.click();
		await settle();

		expect(mounted.target.textContent).toContain('continues in the background');
	});

	it('shows a "no models found" summary once a run completes empty', async () => {
		vi.mocked(api.getModelsLocation).mockResolvedValue({
			success: true,
			data: config({ directories: [{ directory: 'checkpoints', target: null, linked: false, resolved_target: null, has_real_files: false }] })
		});
		vi.mocked(api.getIndexingStatus).mockResolvedValue({
			success: true,
			data: indexing({ state: 'done', found_on_disk: 0, scanned_roots: ['/mnt/storage/models'] })
		});

		mounted = mountStep();
		await settle();

		expect(mounted.target.textContent).toContain('No model files found in');
		expect(mounted.target.textContent).toContain('checkpoints');
	});
});
