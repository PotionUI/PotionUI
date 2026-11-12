import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { AWAITING, status } from './helpers/indexingFixtures';

vi.mock('$lib/services/adminWebsocket', () => ({
	adminWebSocket: {
		onIndexingStatus: () => () => {},
		onConnected: () => () => {},
		isConnected: () => true,
		connectAsync: async () => {}
	}
}));

vi.mock('$lib/services/api', () => ({
	api: {
		getIndexingStatus: vi.fn(),
		getModelRoots: vi.fn(),
		listModelLayouts: vi.fn()
	}
}));

const { api } = await import('$lib/services/api');
const { indexingStatusStore } = await import('$lib/models-location/indexingStatus.svelte');
const { default: ModelsLocationStep } = await import('../../src/routes/setup/components/ModelsLocationStep.svelte');
const { mountComponent } = await import('./helpers/mount');
const { settle } = await import('./helpers/settle');

const DISMISSED_KEY = 'potionui:setup:modelsLocationDismissed';

const overview = {
	roots: [],
	types: [],
	unplaced: [],
	indexing: { state: 'idle' },
	server_os: 'Linux',
	path_style: 'posix'
};

function seed(next: ReturnType<typeof status>) {
	vi.mocked(api.getIndexingStatus).mockResolvedValue({ success: true, data: next });
	indexingStatusStore.status = next;
}

const text = () => document.body.textContent?.replace(/\s+/g, ' ') ?? '';

beforeEach(() => {
	vi.mocked(api.getModelRoots).mockResolvedValue({ success: true, data: overview } as never);
	vi.mocked(api.listModelLayouts).mockResolvedValue({ layouts: [] } as never);
});

afterEach(() => {
	indexingStatusStore.status = null;
	localStorage.removeItem(DISMISSED_KEY);
	vi.clearAllMocks();
});

describe('setup models step', () => {
	it('tells the admin the models are usable now and the choice comes after setup', async () => {
		seed(status({ ...AWAITING, ready: 15, pending_total: 472 }));
		mountComponent(ModelsLocationStep, {});
		await settle();

		expect(text()).toContain("487 models found. They're usable now; you'll choose how to index them after setup.");
	});

	it('shows the same line once the location step is collapsed', async () => {
		localStorage.setItem(DISMISSED_KEY, '1');
		seed(status({ ...AWAITING, ready: 15, pending_total: 472 }));
		mountComponent(ModelsLocationStep, {});
		await settle();

		expect(text()).toContain('Models location:');
		expect(text()).toContain("487 models found. They're usable now; you'll choose how to index them after setup.");
	});

	it('no longer says indexing has to carry on while a scan runs', async () => {
		localStorage.setItem(DISMISSED_KEY, '1');
		seed(status({ ...AWAITING, ready: 15, pending_total: 472 }, { state: 'scanning' }));
		mountComponent(ModelsLocationStep, {});
		await settle();

		expect(text()).not.toContain('Model indexing continues in the background');
	});

	it('shows no line while there are no models', async () => {
		seed(status());
		mountComponent(ModelsLocationStep, {});
		await settle();

		expect(text()).not.toContain('models found');
		expect(text()).not.toContain('usable now');
	});

	it('does not promise a choice once it has been answered', async () => {
		seed(status({ ready: 487, ready_bytes: 1 }));
		mountComponent(ModelsLocationStep, {});
		await settle();

		expect(text()).toContain("487 models found. They're usable now.");
		expect(text()).not.toContain("you'll choose");
	});
});

describe('setup models step choice', () => {
	const radio = (title: string) =>
		Array.from(document.body.querySelectorAll<HTMLElement>('[role="radio"]')).find((r) =>
			r.textContent?.includes(title)
		)!;
	const button = (label: string) =>
		Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.trim() === label);

	it('starts on Start fresh with the path box and a Continue button, no folder form', async () => {
		seed(status());
		mountComponent(ModelsLocationStep, {});
		await settle();

		expect(radio('Start fresh').getAttribute('aria-checked')).toBe('true');
		expect(radio('Use an existing folder').getAttribute('aria-checked')).toBe('false');
		expect(text()).toContain('Models will be saved to');
		expect(document.body.querySelector('#model-root-path')).toBeNull();
		expect(button('Continue')).toBeDefined();
	});

	it('shows the free disk line when it is known', async () => {
		seed(status());
		mountComponent(ModelsLocationStep, { freeDiskBytes: 412 * 1024 ** 3 });
		await settle();

		expect(text()).toContain('412 GB free on that drive.');
	});

	it('reveals the folder form under Use an existing folder and can go back', async () => {
		seed(status());
		mountComponent(ModelsLocationStep, {});
		await settle();

		radio('Use an existing folder').click();
		await settle();
		expect(document.body.querySelector('#model-root-path')).not.toBeNull();
		expect(text()).toContain('It does not copy or move your files.');
		expect(button('Continue')).toBeUndefined();

		button('Back to start fresh')!.click();
		await settle();
		expect(document.body.querySelector('#model-root-path')).toBeNull();
		expect(radio('Start fresh').getAttribute('aria-checked')).toBe('true');
	});

	it('Continue stores the choice and tells the guide to move on', async () => {
		seed(status());
		const onContinue = vi.fn();
		mountComponent(ModelsLocationStep, { onContinue });
		await settle();

		button('Continue')!.click();
		await settle();
		expect(localStorage.getItem(DISMISSED_KEY)).toBe('1');
		expect(onContinue).toHaveBeenCalledTimes(1);
	});

	it('Change on the summary returns to the choice', async () => {
		localStorage.setItem(DISMISSED_KEY, '1');
		seed(status());
		mountComponent(ModelsLocationStep, {});
		await settle();

		button('Change')!.click();
		await settle();
		expect(radio('Start fresh')).toBeDefined();
		expect(localStorage.getItem(DISMISSED_KEY)).toBeNull();
	});
});

describe('setup models step embedded in the starter step', () => {
	const row = () => document.body.querySelector('[data-models-row]');
	const button = (label: string) =>
		Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.trim() === label);
	const radio = (title: string) =>
		Array.from(document.body.querySelectorAll<HTMLElement>('[role="radio"]')).find((r) =>
			r.textContent?.includes(title)
		)!;

	it('starts as one row with the path, Change and a link for folders people already have', async () => {
		seed(status());
		mountComponent(ModelsLocationStep, { embedded: true, initialPath: '/data/models' });
		await settle();

		expect(row()?.textContent).toContain('Saved to');
		expect(row()?.textContent).toContain('/data/models');
		expect(button('Change')).toBeDefined();
		expect(button('Use a folder you already have')).toBeDefined();
		expect(document.body.querySelector('[role="radiogroup"]')).toBeNull();
		expect(button('Continue')).toBeUndefined();
		expect(button('Back')).toBeUndefined();
	});

	it('has no facts card', async () => {
		seed(status());
		mountComponent(ModelsLocationStep, { embedded: true, initialPath: '/data/models' });
		await settle();

		expect(text()).not.toContain('Why a folder');
		expect(text()).not.toContain('Need ComfyUI?');
	});

	it('Change opens the chooser in place on Start fresh', async () => {
		seed(status());
		mountComponent(ModelsLocationStep, { embedded: true, initialPath: '/data/models' });
		await settle();

		button('Change')!.click();
		await settle();
		expect(row()).toBeNull();
		expect(radio('Start fresh').getAttribute('aria-checked')).toBe('true');
		expect(button('Continue')).toBeUndefined();
		expect(button('Back')).toBeUndefined();
	});

	it('the folder link opens the chooser on the existing-folder form', async () => {
		seed(status());
		mountComponent(ModelsLocationStep, { embedded: true, initialPath: '/data/models' });
		await settle();

		button('Use a folder you already have')!.click();
		await settle();
		expect(radio('Use an existing folder').getAttribute('aria-checked')).toBe('true');
		expect(document.body.querySelector('#model-root-path')).not.toBeNull();
	});

	it('keeping the folder closes the chooser and tells the step to reload the machine facts', async () => {
		seed(status());
		const onContinue = vi.fn();
		mountComponent(ModelsLocationStep, { embedded: true, initialPath: '/data/models', onContinue });
		await settle();

		button('Change')!.click();
		await settle();
		button('Keep this folder')!.click();
		await settle();
		expect(row()).not.toBeNull();
		expect(onContinue).toHaveBeenCalledTimes(1);
	});

	it('Close leaves without reloading anything', async () => {
		seed(status());
		const onContinue = vi.fn();
		mountComponent(ModelsLocationStep, { embedded: true, initialPath: '/data/models', onContinue });
		await settle();

		button('Change')!.click();
		await settle();
		button('Close')!.click();
		await settle();
		expect(row()).not.toBeNull();
		expect(onContinue).not.toHaveBeenCalled();
	});
});
