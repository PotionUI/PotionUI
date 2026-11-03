import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { get, writable } from 'svelte/store';
import { AWAITING, GIB, MIB, pendingItem, status } from './helpers/indexingFixtures';

const auth = writable<{ user: { account_type: 'USER' | 'ADMIN' } | null }>({ user: { account_type: 'ADMIN' } });

vi.mock('$lib/stores/auth', () => ({ authStore: auth }));

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
		setIndexingSettings: vi.fn(),
		pauseIndexing: vi.fn(),
		resumeIndexing: vi.fn(),
		queueIndexingDigests: vi.fn(),
		listPendingIndexing: vi.fn()
	}
}));

const { api } = await import('$lib/services/api');
const { page } = await import('./stubs/appStores');
const { indexingStatusStore } = await import('$lib/models-location/indexingStatus.svelte');
const { default: IndexingChoiceModal } = await import('$lib/models-location/IndexingChoiceModal.svelte');
const { toasts } = await import('$lib/stores/toast');
const { resetLayerStackForTests } = await import('$lib/actions/layerStack');
const { mountComponent } = await import('./helpers/mount');
const { settle } = await import('./helpers/settle');
const { setModelTypes, resetModelTypes } = await import('$lib/stores/modelTypes.svelte');
const { coreModelTypeDefinitions } = await import('$lib/stores/modelTypesFixture');

const SAVED = { success: true, data: { state: 'done' } };

function goTo(pathname: string) {
	page.update((current) => ({ ...current, url: new URL(`http://localhost${pathname}`) }));
}

const FILES = [
	pendingItem('a', 'Juggernaut-XL_v9.safetensors', 'checkpoint', 6.6 * GIB),
	pendingItem('b', 'sd_xl_base_1.0.safetensors', 'checkpoint', 6.5 * GIB),
	pendingItem('c', 't5xxl_fp16.safetensors', 'text_encoder', 9.8 * GIB),
	pendingItem('d', 'flux1-dev.safetensors', 'diffusion_model', 16 * GIB),
	pendingItem('e', 'neon_lora.safetensors', 'lora', 200 * MIB)
];

const BY_TYPE = { checkpoint: 2, text_encoder: 1, diffusion_model: 1, lora: 1 };

function seed(next: ReturnType<typeof status> | null, pick = false) {
	if (next) vi.mocked(api.getIndexingStatus).mockResolvedValue({ success: true, data: next });
	indexingStatusStore.status = next;
	indexingStatusStore.pickRequested = pick;
	indexingStatusStore.busy = false;
	indexingStatusStore.actionError = null;
}

function mountModal() {
	return mountComponent(IndexingChoiceModal, {});
}

const dialog = () => document.querySelector<HTMLElement>('[role="dialog"]');
const text = () => dialog()?.textContent?.replace(/\s+/g, ' ') ?? '';
const buttons = () => Array.from(dialog()?.querySelectorAll<HTMLButtonElement>('button') ?? []);
const button = (label: string) => buttons().find((b) => b.textContent?.replace(/\s+/g, ' ').trim().startsWith(label));
const rows = () => Array.from(dialog()?.querySelectorAll<HTMLInputElement>('input[type="checkbox"]') ?? []);
const search = () => dialog()!.querySelector<HTMLInputElement>('input[type="search"]')!;
const summary = () => dialog()?.querySelector('[data-testid="pick-summary"]')?.textContent?.replace(/\s+/g, ' ').trim();
const press = (key: string) => document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));

function mockPending(files = FILES) {
	vi.mocked(api.listPendingIndexing).mockImplementation(async (params = {}) => {
		const query = (params.query ?? '').toLowerCase();
		const matches = files.filter(
			(file) => (!params.model_type || file.model_type === params.model_type) && file.filename.toLowerCase().includes(query)
		);
		const offset = params.offset ?? 0;
		const limit = params.limit ?? 50;
		return {
			success: true,
			data: {
				items: matches.slice(offset, offset + limit),
				total: matches.length,
				total_bytes: matches.reduce((sum, file) => sum + file.size, 0),
				by_type: BY_TYPE
			}
		};
	});
}

beforeEach(() => {
	setModelTypes(coreModelTypeDefinitions());
	auth.set({ user: { account_type: 'ADMIN' } });
	vi.mocked(api.getIndexingStatus).mockResolvedValue({ success: true, data: status(AWAITING) });
	vi.mocked(api.setIndexingSettings).mockResolvedValue(SAVED as never);
	vi.mocked(api.queueIndexingDigests).mockResolvedValue(SAVED as never);
	mockPending();
});

afterEach(() => {
	resetModelTypes();
	seed(null);
	for (const toast of get(toasts)) toasts.remove(toast.id);
	vi.useRealTimers();
	vi.clearAllMocks();
	resetLayerStackForTests();
	document.body.innerHTML = '';
});

describe('IndexingChoiceModal visibility', () => {
	it('renders nothing and never fetches for a non-admin', async () => {
		auth.set({ user: { account_type: 'USER' } });
		seed(status(AWAITING));
		mountModal();
		await settle();
		expect(dialog()).toBeNull();
		expect(api.getIndexingStatus).not.toHaveBeenCalled();
	});

	it('loads the status for an admin on mount', async () => {
		mountModal();
		await settle();
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(1);
	});

	it('stays closed when no choice is awaited', async () => {
		vi.mocked(api.getIndexingStatus).mockResolvedValue({ success: true, data: status({ ready: 5 }) });
		mountModal();
		await settle();
		expect(dialog()).toBeNull();
	});

	it('opens once the status says a choice is awaited and closes when it is answered elsewhere', async () => {
		seed(status({ ready: 5 }));
		mountModal();
		await settle();
		expect(dialog()).toBeNull();

		seed(status(AWAITING));
		await settle();
		expect(dialog()).not.toBeNull();

		seed(status({ ...AWAITING, policy: 'on_demand', plan: { ...AWAITING.plan, awaiting_choice: false } }));
		await settle();
		expect(dialog()).toBeNull();
	});
});

describe('IndexingChoiceModal during setup', () => {
	it('stays closed on the setup wizard even though a choice is awaited, and leaves the choice pending', async () => {
		goTo('/setup');
		seed(status(AWAITING));
		mountModal();
		await settle();

		expect(dialog()).toBeNull();
		expect(api.setIndexingSettings).not.toHaveBeenCalled();
		expect(api.queueIndexingDigests).not.toHaveBeenCalled();
		expect(indexingStatusStore.status?.digests?.plan.awaiting_choice).toBe(true);
	});

	it('stays closed under every setup route', async () => {
		goTo('/setup/claim');
		seed(status(AWAITING));
		mountModal();
		await settle();

		expect(dialog()).toBeNull();
	});

	it('opens as soon as the user is in the app proper', async () => {
		goTo('/setup');
		seed(status(AWAITING));
		mountModal();
		await settle();
		expect(dialog()).toBeNull();

		goTo('/generate');
		await settle();

		expect(dialog()).not.toBeNull();
		expect(text()).toContain('487 models found');
	});

	it('opens on the generate page without ever having been on setup', async () => {
		goTo('/generate');
		seed(status(AWAITING));
		mountModal();
		await settle();

		expect(dialog()).not.toBeNull();
	});

	it('closes when the user goes back into the wizard', async () => {
		goTo('/generate');
		seed(status(AWAITING));
		mountModal();
		await settle();
		expect(dialog()).not.toBeNull();

		goTo('/setup');
		await settle();

		expect(dialog()).toBeNull();
	});
});

describe('IndexingChoiceModal summary', () => {
	beforeEach(() => {
		seed(status(AWAITING, { roots: [
			{ root_id: 'a', label: 'PotionUI models', state: 'online', found: 29, indexed: 29, failed: 0 },
			{ root_id: 'b', label: 'ComfyUI models', state: 'online', found: 458, indexed: 458, failed: 0 }
		] }));
	});

	it('shows the counts, size and time for a large library', async () => {
		mountModal();
		await settle();

		expect(text()).toContain('487 models found');
		expect(text()).toContain('In 2 folders · listed and ready to use now');
		expect(text()).toContain('To index 472 files');
		expect(text()).toContain('Total size 1.75 TB');
		expect(text()).toContain('Time at Low about 5 h 19 m');
		expect(text()).toContain('15 small files were indexed instantly.');
		expect(text()).toContain('Uses at most one core and 96 MB/s. Change this any time in Admin, Models, Folders.');
		expect(text()).toContain('Start with the ones you use. The rest wait.');
		expect(text()).toContain('Nothing is read now. Start whenever you like from Admin, Models, Folders.');
		expect(button('Later')).toBeTruthy();
		expect(button('Pick models')).toBeTruthy();
		expect(button('Index all in the background')).toBeTruthy();
	});

	it('shows the numbers of a library just above the thresholds', async () => {
		seed(
			status({
				...AWAITING,
				ready: 0,
				pending_total: 21,
				plan: { awaiting_choice: true, files: 21, bytes: 5 * GIB, min_files: 20, min_bytes: 64 * GIB }
			})
		);
		mountModal();
		await settle();
		expect(text()).toContain('21 models found');
		expect(text()).toContain('To index 21 files');
		expect(text()).toContain('Total size 5 GB');
		expect(text()).not.toContain('small file');
	});

	it('cannot be dismissed: no close button, Escape and the backdrop do nothing', async () => {
		mountModal();
		await settle();

		expect(dialog()!.querySelector('button[aria-label="Close modal"]')).toBeNull();
		press('Escape');
		await settle();
		expect(dialog()).not.toBeNull();

		document.querySelector<HTMLElement>('[aria-label="Close modal"][role="button"]')?.click();
		await settle();
		expect(dialog()).not.toBeNull();
		expect(api.setIndexingSettings).not.toHaveBeenCalled();
	});

	it('Index all in the background sets the background policy and queues everything', async () => {
		mountModal();
		await settle();

		button('Index all in the background')!.click();
		await settle();

		expect(api.setIndexingSettings).toHaveBeenCalledWith({ policy: 'background' });
		expect(api.queueIndexingDigests).toHaveBeenCalledWith({ mode: 'all' });
		expect(get(toasts).some((toast) => toast.message === 'Indexing started in the background')).toBe(true);
	});

	it('Enter chooses Index all in the background', async () => {
		mountModal();
		await settle();

		press('Enter');
		await settle();

		expect(api.queueIndexingDigests).toHaveBeenCalledWith({ mode: 'all' });
	});

	it('Later keeps the policy on demand without queueing anything', async () => {
		mountModal();
		await settle();

		button('Later')!.click();
		await settle();

		expect(api.setIndexingSettings).toHaveBeenCalledWith({ policy: 'on_demand' });
		expect(api.queueIndexingDigests).not.toHaveBeenCalled();
	});

	it('stays open and reports the failure when saving fails', async () => {
		vi.mocked(api.setIndexingSettings).mockResolvedValue({ success: false, message: 'Nope' } as never);
		mountModal();
		await settle();

		button('Index all in the background')!.click();
		await settle();

		expect(dialog()).not.toBeNull();
		expect(api.queueIndexingDigests).not.toHaveBeenCalled();
		expect(get(toasts).some((toast) => toast.type === 'error' && toast.message === 'Nope')).toBe(true);
	});

	it('follows the level the backend reports for the usage and the time', async () => {
		seed(status({ ...AWAITING, level: 'normal' as const }));
		mountModal();
		await settle();
		expect(text()).toContain('Time at Normal about 80 min');
		expect(text()).toContain('Uses at most two cores and 384 MB/s.');
	});
});

describe('IndexingChoiceModal pick', () => {
	beforeEach(() => {
		seed(status(AWAITING));
	});

	async function openPick() {
		mountModal();
		await settle();
		button('Pick models')!.click();
		await settle();
	}

	it('lists the first page with type chips and counts', async () => {
		await openPick();

		expect(api.listPendingIndexing).toHaveBeenCalledWith({ query: undefined, model_type: undefined, offset: 0, limit: 50 });
		expect(text()).toContain('Pick models to index first');
		expect(text()).toContain('472 files to index · 1.8 TB');
		expect(rows()).toHaveLength(5);
		expect(text()).toContain('All 5');
		expect(text()).toContain('Base model 2');
		expect(text()).toContain('Text encoder 1');
		expect(text()).toContain('5 files');
		expect(summary()).toBe('Nothing selected yet');
		expect(button('Index selected')!.disabled).toBe(true);
	});

	it('searches by name after a pause and keeps the counts', async () => {
		vi.useFakeTimers();
		await openPick();

		const input = search();
		input.value = 'xl';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.advanceTimersByTimeAsync(100);
		expect(api.listPendingIndexing).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(200);
		await settle();

		expect(api.listPendingIndexing).toHaveBeenLastCalledWith({ query: 'xl', model_type: undefined, offset: 0, limit: 50 });
		expect(rows()).toHaveLength(3);
		expect(text()).toContain('3 of 5 match');
		expect(text()).toContain('All 5');
	});

	it('filters by type through the chips', async () => {
		await openPick();

		button('Text encoder')!.click();
		await settle();

		expect(api.listPendingIndexing).toHaveBeenLastCalledWith({ query: undefined, model_type: 'text_encoder', offset: 0, limit: 50 });
		expect(rows()).toHaveLength(1);
		expect(button('Text encoder')!.getAttribute('aria-pressed')).toBe('true');
	});

	it('ticks files and summarizes the selection at the current level', async () => {
		await openPick();

		rows()[0].click();
		rows()[1].click();
		await settle();

		expect(summary()).toBe('2 selected · 13.1 GB · about 2 min at Low');
		expect(button('Index selected')!.disabled).toBe(false);
	});

	it('Index selected moves an unanswered policy to on demand and queues the ticked files', async () => {
		await openPick();
		rows()[0].click();
		rows()[2].click();
		await settle();

		button('Index selected')!.click();
		await settle();

		expect(api.setIndexingSettings).toHaveBeenCalledWith({ policy: 'on_demand' });
		expect(api.queueIndexingDigests).toHaveBeenCalledWith({ mode: 'selected', model_ids: ['a', 'c'] });
		expect(get(toasts).some((toast) => toast.message === '2 models queued first')).toBe(true);
	});

	it('Select all covers every match, not just the loaded rows, and untick excludes one', async () => {
		await openPick();

		button('Select all 5')!.click();
		await settle();
		expect(summary()).toContain('5 selected');
		expect(button('All 5 selected')!.disabled).toBe(true);

		rows()[1].click();
		await settle();
		expect(summary()).toContain('4 selected');

		button('Index selected')!.click();
		await settle();

		expect(api.queueIndexingDigests).toHaveBeenCalledWith({ mode: 'matching', exclude_model_ids: ['b'] });
	});

	it('Select all under a search sends the search as the filter', async () => {
		vi.useFakeTimers();
		await openPick();
		const input = search();
		input.value = 'xl';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.advanceTimersByTimeAsync(300);
		await settle();

		button('Select all 3')!.click();
		await settle();
		button('Index selected')!.click();
		await settle();

		expect(api.queueIndexingDigests).toHaveBeenCalledWith({ mode: 'matching', query: 'xl' });
	});

	it('Select all under two different types keeps both and queues each scope', async () => {
		await openPick();

		button('Base model')!.click();
		await settle();
		button('Select all 2')!.click();
		await settle();
		button('LoRA')!.click();
		await settle();
		button('Select all 1')!.click();
		await settle();

		expect(summary()).toContain('3 selected');
		button('Index selected')!.click();
		await settle();

		expect(api.queueIndexingDigests).toHaveBeenNthCalledWith(1, { mode: 'matching', model_type: 'checkpoint' });
		expect(api.queueIndexingDigests).toHaveBeenNthCalledWith(2, { mode: 'matching', model_type: 'lora' });
	});

	it('says up to when two selections may overlap', async () => {
		vi.useFakeTimers();
		await openPick();
		const input = search();
		input.value = 'xl';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.advanceTimersByTimeAsync(300);
		await settle();
		button('Select all 3')!.click();
		await settle();
		button('LoRA')!.click();
		await settle();
		input.value = '';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.advanceTimersByTimeAsync(300);
		await settle();
		button('Select all 1')!.click();
		await settle();

		expect(summary()).toContain('Up to 4 selected');
	});

	it('cannot select all while a search is waiting to run or its list is loading', async () => {
		vi.useFakeTimers();
		await openPick();
		expect(button('Select all 5')!.disabled).toBe(false);

		const input = search();
		input.value = 'xl';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(button('Select all 5')!.disabled).toBe(true);

		let release: (value: unknown) => void = () => {};
		vi.mocked(api.listPendingIndexing).mockImplementationOnce(
			() => new Promise((resolve) => (release = resolve)) as never
		);
		await vi.advanceTimersByTimeAsync(300);
		expect(button('Select all 5')!.disabled).toBe(true);

		release({
			success: true,
			data: { items: FILES.slice(0, 3), total: 3, total_bytes: 20 * GIB, by_type: BY_TYPE }
		});
		await settle();
		expect(button('Select all 3')!.disabled).toBe(false);
	});

	it('follows the latest request when list responses arrive out of order', async () => {
		vi.useFakeTimers();
		let releaseFirst: (value: unknown) => void = () => {};
		vi.mocked(api.listPendingIndexing).mockImplementationOnce(
			() => new Promise((resolve) => (releaseFirst = resolve)) as never
		);
		mountModal();
		await settle();
		button('Pick models')!.click();
		await settle();

		const input = search();
		input.value = 'xl';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.advanceTimersByTimeAsync(300);
		await settle();
		expect(rows()).toHaveLength(3);

		releaseFirst({ success: true, data: { items: FILES, total: 5, total_bytes: 40 * GIB, by_type: BY_TYPE } });
		await settle();

		expect(rows()).toHaveLength(3);
		expect(text()).toContain('3 of 5 match');
	});

	it('keeps the dialog open with the selection and reports it when queueing fails', async () => {
		vi.mocked(api.queueIndexingDigests).mockResolvedValue({ success: false, message: 'Queue is full' } as never);
		vi.mocked(api.setIndexingSettings).mockImplementation(async () => {
			const answered = { ...AWAITING, policy: 'on_demand' as const, plan: { ...AWAITING.plan, awaiting_choice: false } };
			vi.mocked(api.getIndexingStatus).mockResolvedValue({ success: true, data: status(answered) });
			return SAVED as never;
		});
		await openPick();
		rows()[0].click();
		rows()[2].click();
		await settle();

		button('Index selected')!.click();
		await settle();

		expect(dialog()).not.toBeNull();
		expect(summary()).toContain('2 selected');
		expect(api.setIndexingSettings).not.toHaveBeenCalled();
		expect(get(toasts).some((toast) => toast.type === 'error' && toast.message === 'Queue is full')).toBe(true);
		expect(rows().filter((row) => row.checked)).toHaveLength(2);
	});

	it('Clear selection empties the selection', async () => {
		await openPick();
		rows()[0].click();
		await settle();

		button('Clear selection')!.click();
		await settle();

		expect(summary()).toBe('Nothing selected yet');
		expect(button('Clear selection')).toBeUndefined();
	});

	it('Back returns to the summary and keeps the dialog open', async () => {
		await openPick();

		button('Back')!.click();
		await settle();

		expect(text()).toContain('487 models found');
		expect(dialog()).not.toBeNull();
	});

	it('shows more rows a page at a time', async () => {
		const many = Array.from({ length: 120 }, (_, i) => pendingItem(`m${i}`, `file-${i}.safetensors`, 'lora', MIB));
		mockPending(many);
		await openPick();

		expect(rows()).toHaveLength(50);
		expect(text()).toContain('Showing 50 of 120');
		button('Show 50 more')!.click();
		await settle();

		expect(api.listPendingIndexing).toHaveBeenLastCalledWith({ query: undefined, model_type: undefined, offset: 50, limit: 50 });
		expect(rows()).toHaveLength(100);
		expect(button('Show 20 more')).toBeTruthy();
	});

	it('says when nothing matches the search', async () => {
		vi.useFakeTimers();
		await openPick();
		const input = search();
		input.value = 'zzz';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.advanceTimersByTimeAsync(300);
		await settle();

		expect(text()).toContain('No files match "zzz".');
	});

	it('shows a failed list load', async () => {
		vi.mocked(api.listPendingIndexing).mockResolvedValue({ success: false, message: 'Boom' } as never);
		await openPick();
		expect(text()).toContain('Boom');
	});
});

describe('IndexingChoiceModal reopened from the settings section', () => {
	beforeEach(() => {
		seed(status({ policy: 'on_demand', ready: 15, pending_total: 472, deferred: 472, pending_bytes: 100 * GIB }), true);
	});

	it('opens straight at the pick step with a close button', async () => {
		mountModal();
		await settle();

		expect(text()).toContain('Pick models to index first');
		expect(dialog()!.querySelector('button[aria-label="Close modal"]')).not.toBeNull();
	});

	it('Back closes the dialog', async () => {
		mountModal();
		await settle();

		button('Back')!.click();
		await settle();

		expect(dialog()).toBeNull();
		expect(indexingStatusStore.pickRequested).toBe(false);
	});

	it('Escape closes it', async () => {
		mountModal();
		await settle();

		press('Escape');
		await settle();

		expect(dialog()).toBeNull();
	});

	it('leaves an answered policy alone and closes after queueing', async () => {
		mountModal();
		await settle();
		rows()[0].click();
		await settle();

		button('Index selected')!.click();
		await settle();

		expect(api.setIndexingSettings).not.toHaveBeenCalled();
		expect(api.queueIndexingDigests).toHaveBeenCalledWith({ mode: 'selected', model_ids: ['a'] });
		expect(dialog()).toBeNull();
	});
});
