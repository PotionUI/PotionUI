import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { settle } from './helpers/settle';

const apiMock = vi.hoisted(() => ({
	getModelRoots: vi.fn(),
	updateModelRoot: vi.fn(),
	deleteModelRoot: vi.fn(),
	reorderModelRoots: vi.fn(),
	setModelRootWrite: vi.fn(),
	setModelRootBindingScan: vi.fn(),
	probeModelRoot: vi.fn(),
	detectModelRoot: vi.fn(),
	listModelLayouts: vi.fn(),
	browseModelRoot: vi.fn(),
	createModelRoot: vi.fn()
}));

vi.mock('$lib/services/api', () => ({ api: apiMock }));
vi.mock('$lib/stores/confirm', () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock('$lib/model-roots/AddRootModal.svelte', async () => ({
	default: (await import('./stubs/AddRootModalStub.svelte')).default
}));
vi.mock('$lib/models-location/IndexingSettingsSection.svelte', async () => ({
	default: (await import('./stubs/BlankStub.svelte')).default
}));

const { confirmDialog } = await import('$lib/stores/confirm');
const { toasts } = await import('$lib/stores/toast');
const { FoldersController } = await import('$lib/model-folders/controller.svelte');
const { default: FoldersPane } = await import('$lib/model-folders/FoldersPane.svelte');
const { default: FoldersSidebar } = await import('$lib/model-folders/FoldersSidebar.svelte');
const { indexingStatusStore } = await import('$lib/models-location/indexingStatus.svelte');
const { viewportWidth } = await import('$lib/stores/viewport');

type Obj = Record<string, unknown>;

function binding(type: string, subdir: string, over: Obj = {}): Obj {
	return {
		model_type: type,
		scan_headers: false,
		folder: subdir,
		subdir,
		path: `/mnt/models/${subdir}`,
		exists: true,
		position: 0,
		is_write: false,
		indexed_files: 10,
		size_bytes: 1_000_000_000,
		unindexed: 0,
		...over
	};
}

function root(id: string, over: Obj = {}): Obj {
	return {
		id,
		label: id === 'home' ? 'PotionUI' : id === 'depot' ? 'Depot' : id,
		path: id === 'home' ? '/srv/potionui/models' : `/mnt/${id}`,
		kind: id === 'home' ? 'home' : 'library',
		read_only: false,
		case_insensitive: false,
		state: 'online',
		state_reason: null,
		state_checked_at: null,
		bindings: [],
		...over
	};
}

function overview(roots: Obj[], over: Obj = {}): Obj {
	const types = new Map<string, string[]>();
	for (const r of roots) for (const b of r.bindings as Obj[]) types.set(b.model_type as string, [...(types.get(b.model_type as string) ?? []), r.id as string]);
	return {
		roots,
		types: [...types.entries()].map(([model_type, order]) => ({ model_type, folder: model_type, order, write_root_id: order[0] })),
		unplaced: [],
		indexing: { state: 'idle' },
		server_os: 'Linux',
		path_style: 'posix',
		...over
	};
}

const HOME = root('home', {
	bindings: [
		binding('checkpoint', 'checkpoints', { is_write: true, indexed_files: 14, unindexed: 6, size_bytes: 71_400_000_000 }),
		binding('lora', 'loras', { is_write: true, indexed_files: 212, size_bytes: 18_200_000_000 }),
		binding('vae', 'vae', { exists: false, scan_headers: true, indexed_files: 0, size_bytes: 0 })
	]
});
const DEPOT = root('depot', {
	read_only: true,
	bindings: [binding('checkpoint', 'ckpt', { indexed_files: 30, unindexed: 0 }), binding('lora', 'lora', { indexed_files: 5 })]
});

function digests(over: Obj = {}): never {
	return {
		state: 'running',
		digests: {
			policy: 'all',
			level: 'normal',
			levels: [],
			paused: false,
			pause_reason: null,
			busy: true,
			workers: 1,
			pending_total: 50,
			queued: 50,
			deferred: 0,
			ready: 50,
			ready_bytes: 50,
			pending_bytes: 50,
			queued_bytes: 50,
			current: [],
			throughput_bps: null,
			eta_seconds: null,
			duplicate_checks: 0,
			failed: [],
			plan: { awaiting_choice: false, files: 0, bytes: 0, min_files: 0, min_bytes: 0 },
			version: 1,
			...over
		}
	} as never;
}

let errorToast: ReturnType<typeof vi.spyOn>;
let successToast: ReturnType<typeof vi.spyOn>;
const BOOM = { success: false, message: 'boom' };

const mounted: { c: ReturnType<typeof mount>; t: HTMLElement }[] = [];
let ctrl: InstanceType<typeof FoldersController>;
const onBack = vi.fn();

async function load(data: Obj) {
	apiMock.getModelRoots.mockResolvedValue({ success: true, data });
	ctrl = new FoldersController();
	await ctrl.load();
}

async function loadFresh(data: Obj) {
	await load(data);
	apiMock.getModelRoots.mockImplementation(async () => ({ success: true, data: structuredClone(data) }));
}

async function mountPane() {
	const t = document.createElement('div');
	document.body.appendChild(t);
	const c = mount(FoldersPane, { target: t, props: { ctrl, onBack } });
	mounted.push({ c, t });
	await settle();
	flushSync();
	return t;
}

async function mountSidebar() {
	const t = document.createElement('div');
	document.body.appendChild(t);
	const c = mount(FoldersSidebar, { target: t, props: { ctrl } });
	mounted.push({ c, t });
	await settle();
	flushSync();
	return t;
}

const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
const navRows = (t: HTMLElement) => Array.from(t.querySelectorAll<HTMLElement>('[aria-selected]'));
const navRow = (t: HTMLElement, start: string) => navRows(t).find((e) => text(e).startsWith(start));
const rows = (t: HTMLElement) => Array.from(t.querySelectorAll<HTMLElement>('[data-binding-row]'));
const btn = (scope: ParentNode, label: string | RegExp) =>
	Array.from(scope.querySelectorAll<HTMLButtonElement>('button')).find((b) => {
		const name = b.getAttribute('aria-label') ?? text(b);
		return typeof label === 'string' ? name === label : label.test(name);
	});
async function click(el: Element | null | undefined) {
	expect(el, 'element to click').toBeTruthy();
	(el as HTMLElement).click();
	await settle();
	flushSync();
}
async function pick(rowEl: HTMLElement, item: string) {
	await click(rowEl.querySelector('button[aria-haspopup]') ?? rowEl.querySelector('div[role="presentation"] button'));
	await click(Array.from(document.querySelectorAll('[role="menuitem"]')).find((m) => text(m) === item));
}
const panel = () => document.querySelector<HTMLElement>('[data-side-panel]');

beforeEach(() => {
	for (const fn of Object.values(apiMock)) fn.mockReset();
	apiMock.listModelLayouts.mockResolvedValue({ layouts: [] });
	apiMock.updateModelRoot.mockResolvedValue({ success: true, data: HOME });
	apiMock.setModelRootWrite.mockResolvedValue({ success: true, data: HOME });
	apiMock.setModelRootBindingScan.mockResolvedValue({ success: true, data: HOME });
	apiMock.probeModelRoot.mockResolvedValue({ success: true, data: HOME });
	apiMock.deleteModelRoot.mockResolvedValue({ success: true });
	apiMock.reorderModelRoots.mockResolvedValue({ success: true });
	vi.mocked(confirmDialog).mockClear();
	vi.mocked(confirmDialog).mockResolvedValue(true);
	errorToast = vi.spyOn(toasts, 'error');
	successToast = vi.spyOn(toasts, 'success');
	onBack.mockClear();
	indexingStatusStore.status = null;
	viewportWidth.set(1440);
});

afterEach(() => {
	vi.restoreAllMocks();
	for (const { c, t } of mounted.splice(0)) {
		unmount(c);
		t.remove();
	}
	document.body.innerHTML = '';
});

describe('Folders sidebar', () => {
	it('lists every folder with its file count and an Add folder row', async () => {
		await load(overview([HOME, DEPOT]));
		const t = await mountSidebar();
		expect(text(navRow(t, 'PotionUI'))).toMatch(/^PotionUI.*1$/);
		expect(text(navRow(t, 'Depot'))).toMatch(/^Depot.*35$/);
		expect(navRow(t, 'Add folder')).toBeTruthy();
		expect(text(t)).toContain('Folders 2');
	});

	it('shows problem badges instead of the count for offline and missing folders', async () => {
		const offline = root('nas', { state: 'offline', state_reason: 'gone', bindings: [binding('lora', 'loras', { exists: false })] });
		await load(overview([HOME, offline]));
		const t = await mountSidebar();
		expect(text(navRow(t, 'nas'))).toMatch(/^nas.*2$/);
		expect(text(navRow(t, 'PotionUI'))).toMatch(/^PotionUI.*1$/);
	});

	it('shows Found outside folders with its count only when something was found', async () => {
		await load(overview([HOME]));
		let t = await mountSidebar();
		expect(text(t)).not.toContain('Found outside folders');
		await load(overview([HOME], { unplaced: [{ dir: '/x/a', count: 3, types: ['lora'] }, { dir: '/x/b', count: 1, types: ['vae'] }] }));
		t = await mountSidebar();
		expect(text(t)).toMatch(/Found outside folders\s*2/);
	});

	it('shows the live indexing percent', async () => {
		await load(overview([HOME]));
		indexingStatusStore.status = digests();
		const t = await mountSidebar();
		expect(text(t.querySelector('[aria-label="Across folders"]'))).toMatch(/Indexing\s*\d+%/);
	});

	it('selects a folder, Downloads & priority and Indexing through the controller', async () => {
		await load(overview([HOME, DEPOT]));
		const t = await mountSidebar();
		await click(navRow(t, 'Depot'));
		expect(ctrl.effective).toBe('depot');
		await click(navRow(t, 'Downloads & priority'));
		expect(ctrl.effective).toBe('routing');
		await click(navRow(t, 'Indexing'));
		expect(ctrl.effective).toBe('indexing');
	});

	it('Add folder opens the add dialog', async () => {
		await load(overview([HOME]));
		const t = await mountSidebar();
		await click(navRow(t, 'Add folder'));
		expect(ctrl.addOpen).toBe(true);
	});
});

describe('Folders pane - root table', () => {
	it('renders one row per type folder with Files = indexed + unindexed', async () => {
		await load(overview([HOME, DEPOT]));
		const t = await mountPane();
		const r = rows(t);
		expect(r).toHaveLength(3);
		const cells = r.map((row) => ({ type: text(row.querySelector('span')), files: text(row.querySelector('[data-cell="files"]')) }));
		expect(cells).toEqual([
			{ type: 'Checkpoint', files: '20' },
			{ type: 'Lora', files: '212' },
			{ type: 'Vae', files: '0' }
		]);
		expect(text(t.querySelector('[data-root-stats]'))).toMatch(/^232 files\s*[\d.]+ GB\s*6 not indexed\s*checked never$/);
	});

	it('shows Downloads, Missing, pending and Header badges, and Indexing while the ticker runs', async () => {
		await load(overview([HOME]));
		let t: HTMLElement = await mountPane();
		const r = rows(t);
		expect(text(r[0])).toContain('Downloads');
		expect(text(r[0])).toContain('6 pending');
		expect(text(r[2])).toContain('Missing');
		expect(text(r[2])).toContain('Header');
		indexingStatusStore.status = digests({ pending_total: 6, queued: 6 });
		flushSync();
		t = mounted[0].t as HTMLDivElement;
		expect(text(rows(t)[0])).toContain('Indexing');
		expect(text(rows(t)[0])).not.toContain('pending');
	});

	it('marks bindings of a read-only folder Read-only unless they are the downloads target', async () => {
		await load(overview([root('depot', { read_only: true, bindings: [binding('lora', 'lora'), binding('vae', 'vae', { is_write: true })] })]));
		const t = await mountPane();
		expect(text(rows(t)[0])).toContain('Read-only');
		expect(text(rows(t)[1])).not.toContain('Read-only');
	});

	it('header shows name, state, chips, path and the three icon buttons', async () => {
		await load(overview([HOME]));
		const t = await mountPane();
		expect(text(t.querySelector('h2'))).toBe('PotionUI');
		expect(text(t)).toContain('Online');
		expect(text(t)).toContain('Built-in');
		expect(text(t)).toContain('/srv/potionui/models');
		for (const label of ['Folder settings', 'Check PotionUI now', 'Detect again']) expect(btn(t, label), label).toBeTruthy();
	});

	it('offline folder shows an alert with the reason and Check again probes it', async () => {
		await load(overview([root('nas', { state: 'offline', state_reason: 'Mount is gone', bindings: [binding('lora', 'loras')] })]));
		const t = await mountPane();
		expect(text(t)).toContain('nas is offline');
		expect(text(t)).toContain('Mount is gone');
		await click(btn(t, 'Check again'));
		expect(apiMock.probeModelRoot).toHaveBeenCalledWith('nas');
	});

	it('Check now probes the folder and Detect again detects its path', async () => {
		apiMock.detectModelRoot.mockResolvedValue({ success: true, data: { path: '/mnt/depot', effective_path: '/mnt/depot', state: 'online', layout: 'typed', suggestions: [] } });
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Check Depot now'));
		expect(apiMock.probeModelRoot).toHaveBeenCalledWith('depot');
		await click(btn(t, 'Detect again'));
		expect(apiMock.detectModelRoot).toHaveBeenCalled();
		expect(apiMock.detectModelRoot.mock.calls[0][0]).toBe('/mnt/depot');
	});

	it('the built-in folder cannot be detected again', async () => {
		await load(overview([HOME]));
		const t = await mountPane();
		expect(btn(t, 'Detect again')!.disabled).toBe(true);
	});

	it('fresh install with an empty built-in folder still renders its rows with zero counts', async () => {
		const fresh = root('home', { bindings: [binding('checkpoint', 'checkpoints', { is_write: true, indexed_files: 0, size_bytes: 0 })] });
		await load(overview([fresh]));
		const t = await mountPane();
		expect(text(t.querySelector('[data-root-stats]'))).toMatch(/^0 files\s*0 B/);
		expect(rows(t)).toHaveLength(1);
	});

	it('a folder with no type folders explains where to add one', async () => {
		await load(overview([root('empty')]));
		const t = await mountPane();
		expect(text(t)).toContain('No types bound to this folder yet');
	});

	it('shows every type folder in one table without paging', async () => {
		const many = root('big', { bindings: Array.from({ length: 14 }, (_, i) => binding('lora', `lora${i}`)) });
		await load(overview([many]));
		const t = await mountPane();
		expect(rows(t)).toHaveLength(14);
		expect(t.querySelector('[aria-label*="Page 2"], [aria-label*="page 2"]')).toBeNull();
	});
});

describe('Folders pane - row actions', () => {
	it('Details opens the side panel with the full path, counts and Downloads state', async () => {
		await load(overview([DEPOT]));
		const t = await mountPane();
		await pick(rows(t)[0], 'Details');
		const p = panel()!;
		expect(p).toBeTruthy();
		expect(text(p)).toContain('/mnt/models/ckpt');
		expect(text(p)).toContain('Files 30');
		expect(text(p)).toContain('This folder is read-only.');
	});

	it('clicking a row opens details and Close dismisses them', async () => {
		await load(overview([HOME]));
		const t = await mountPane();
		await click(rows(t)[1]);
		expect(text(panel())).toContain('Lora');
		await click(btn(panel()!, 'Close details'));
		expect(panel()).toBeNull();
	});

	it('Use for downloads sets the write target for that type and subfolder', async () => {
		await load(overview([DEPOT, HOME].map((r) => ({ ...r, read_only: false }))));
		const t = await mountPane();
		ctrl.selected = 'depot';
		flushSync();
		await pick(rows(t)[0], 'Use for downloads');
		expect(apiMock.setModelRootWrite).toHaveBeenCalledWith('depot', 'checkpoint', 'ckpt');
	});

	it('Use for downloads is not offered for the current downloads folder or a read-only folder', async () => {
		await load(overview([HOME, DEPOT]));
		const t = await mountPane();
		await click(rows(t)[0].querySelector('div[role="presentation"] button'));
		expect(Array.from(document.querySelectorAll('[role="menuitem"]')).map(text)).not.toContain('Use for downloads');
	});

	it('Detect type from file toggles header detection on the binding', async () => {
		await load(overview([HOME]));
		const t = await mountPane();
		await pick(rows(t)[0], 'Detect type from file');
		expect(apiMock.setModelRootBindingScan).toHaveBeenCalledWith('home', { model_type: 'checkpoint', subdir: 'checkpoints', scan_headers: true });
	});

	it('Stop detecting type from file is offered when header detection is on', async () => {
		await load(overview([root('home', { bindings: [binding('unet', 'unet', { scan_headers: true })] })]));
		const t = await mountPane();
		await pick(rows(t)[0], 'Stop detecting type from file');
		expect(apiMock.setModelRootBindingScan).toHaveBeenCalledWith('home', { model_type: 'unet', subdir: 'unet', scan_headers: false });
	});

	it('Remove asks for confirmation, then removes only that binding', async () => {
		await load(overview([DEPOT]));
		const t = await mountPane();
		await pick(rows(t)[1], 'Remove from folder');
		expect(confirmDialog).toHaveBeenCalledWith(expect.objectContaining({ variant: 'danger' }));
		expect(apiMock.updateModelRoot).toHaveBeenCalledWith('depot', { remove_bindings: [{ model_type: 'lora', subdir: 'lora' }] });
	});

	it('Remove is not offered for the built-in folder and nothing is sent when the confirm is declined', async () => {
		await load(overview([HOME]));
		let t = await mountPane();
		await click(rows(t)[0].querySelector('div[role="presentation"] button'));
		expect(Array.from(document.querySelectorAll('[role="menuitem"]')).map(text)).not.toContain('Remove from folder');
		for (const { c, t: tt } of mounted.splice(0)) {
			unmount(c);
			tt.remove();
		}
		document.body.innerHTML = '';
		vi.mocked(confirmDialog).mockResolvedValueOnce(false);
		await load(overview([DEPOT]));
		t = await mountPane();
		await pick(rows(t)[0], 'Remove from folder');
		expect(apiMock.updateModelRoot).not.toHaveBeenCalled();
	});

	it('the panel Detect type from file switch calls the same API', async () => {
		await load(overview([HOME]));
		const t = await mountPane();
		await click(rows(t)[0]);
		await click(panel()!.querySelector('[role="switch"][aria-label="Detect type from file"]'));
		expect(apiMock.setModelRootBindingScan).toHaveBeenCalledWith('home', { model_type: 'checkpoint', subdir: 'checkpoints', scan_headers: true });
	});

	it('a missing type folder shows the missing alert in its panel', async () => {
		await load(overview([HOME]));
		const t = await mountPane();
		await click(rows(t)[2]);
		expect(text(panel())).toContain('This folder is missing on disk');
	});
});

describe('Folders pane - folder settings panel', () => {
	it('opens from the header icon with name, path, read-only and counts', async () => {
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		const p = panel()!;
		expect(text(p)).toContain('Folder settings');
		expect((p.querySelector('#d-label-depot') as HTMLInputElement).value).toBe('Depot');
		expect((p.querySelector('#d-path-depot') as HTMLInputElement).value).toBe('/mnt/depot');
		expect((p.querySelector('[role="switch"][aria-label="Read-only"]') as HTMLInputElement).checked).toBe(true);
	});

	it('Save appears only after Name or Path changes and sends only the changed fields', async () => {
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		const p = panel()!;
		expect(btn(p, /Save changes/)).toBeUndefined();
		const label = p.querySelector('#d-label-depot') as HTMLInputElement;
		label.value = 'Archive';
		label.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		flushSync();
		await click(btn(p, /Save changes/));
		expect(apiMock.updateModelRoot).toHaveBeenCalledWith('depot', { label: 'Archive' });
	});

	it('toggling Read-only updates the folder', async () => {
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		await click(panel()!.querySelector('[role="switch"][aria-label="Read-only"]'));
		expect(apiMock.updateModelRoot).toHaveBeenCalledWith('depot', { read_only: false });
	});

	it('the built-in folder has a fixed path, no delete and no manual type folder picker', async () => {
		await load(overview([HOME]));
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		const p = panel()!;
		expect((p.querySelector('#d-path-home') as HTMLInputElement).disabled).toBe(true);
		expect(btn(p, /Delete folder/)).toBeUndefined();
	});

	it('Delete folder confirms, deletes the folder and closes the panel', async () => {
		await load(overview([HOME, DEPOT]));
		ctrl.selected = 'depot';
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		await click(btn(panel()!, /Delete folder/));
		expect(confirmDialog).toHaveBeenCalledWith(expect.objectContaining({ title: 'Delete this folder?', variant: 'danger' }));
		expect(apiMock.deleteModelRoot).toHaveBeenCalledWith('depot');
		expect(panel()).toBeNull();
	});

	it('Check now inside the panel probes the folder', async () => {
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		await click(btn(panel()!, 'Check now'));
		expect(apiMock.probeModelRoot).toHaveBeenCalledWith('depot');
	});

	it('Detect again lists missing type folders and Add selected sends them', async () => {
		apiMock.detectModelRoot.mockResolvedValue({
			success: true,
			data: {
				path: '/mnt/depot',
				effective_path: '/mnt/depot',
				state: 'online',
				layout: 'typed',
				suggestions: [
					{ model_type: 'checkpoint', subdir: 'ckpt', matched_by: 'canonical', file_count: 1, file_count_truncated: false },
					{ model_type: 'controlnet', subdir: 'controlnet', matched_by: 'canonical', file_count: 2, file_count_truncated: false }
				]
			}
		});
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Detect again'));
		const found = t.querySelector('[data-again-panel]')!;
		expect(text(found)).toContain('Controlnet');
		expect(text(found)).not.toContain('ckpt');
		await click(btn(found, /Add selected/));
		expect(apiMock.updateModelRoot).toHaveBeenCalledWith('depot', { bindings: [expect.objectContaining({ model_type: 'controlnet', subdir: 'controlnet' })] });
	});
});

describe('Folders pane - across folders', () => {
	it('Downloads & priority lists each type with its folders, the Downloads marker and reorder arrows', async () => {
		await load(overview([HOME, root('depot', { bindings: [binding('checkpoint', 'ckpt')] })]));
		ctrl.selected = 'routing';
		const t = await mountPane();
		const cards = Array.from(t.querySelectorAll('[data-type-card]'));
		expect(text(cards[0])).toContain('Checkpoint');
		expect(text(cards[0])).toContain('2 folders');
		expect(btn(t, 'Move PotionUI earlier for Checkpoint')!.disabled).toBe(true);
		expect(btn(t, 'Move Depot later for Checkpoint')!.disabled).toBe(true);
		expect(text(t)).toContain('Downloads');
	});

	it('reordering sends the new root order for that type', async () => {
		await load(overview([HOME, root('depot', { bindings: [binding('checkpoint', 'ckpt')] })]));
		ctrl.selected = 'routing';
		const t = await mountPane();
		await click(btn(t, 'Move PotionUI later for Checkpoint'));
		expect(apiMock.reorderModelRoots).toHaveBeenCalledWith(['depot', 'home'], 'checkpoint');
	});

	it('Use for downloads on a priority row sets that folder as the target', async () => {
		await load(overview([HOME, root('depot', { bindings: [binding('checkpoint', 'ckpt')] })]));
		ctrl.selected = 'routing';
		const t = await mountPane();
		await click(btn(t, 'Use for downloads'));
		expect(apiMock.setModelRootWrite).toHaveBeenCalledWith('depot', 'checkpoint', 'ckpt');
	});

	it('Found outside folders lists suggestions and Add as folder opens the dialog prefilled', async () => {
		await load(overview([HOME], { unplaced: [{ dir: '/x/models', count: 4, types: ['lora', 'vae'] }] }));
		ctrl.selected = 'suggested';
		const t = await mountPane();
		expect(text(t.querySelector('[data-suggested-row]'))).toContain('/x/models');
		expect(text(t.querySelector('[data-suggested-row]'))).toContain('4 files - Lora, Vae');
		await click(btn(t, 'Add as folder'));
		expect(ctrl.addOpen).toBe(true);
		expect(ctrl.addPath).toBe('/x/models');
	});

	it('Indexing shows the indexing settings under its own header', async () => {
		await load(overview([HOME]));
		ctrl.selected = 'indexing';
		const t = await mountPane();
		expect(text(t.querySelector('h2'))).toBe('Indexing');
		expect(rows(t)).toHaveLength(0);
	});
});

describe('Folders pane - states and phone', () => {
	it('shows a retry state when the roots cannot be loaded', async () => {
		apiMock.getModelRoots.mockResolvedValue({ success: false, message: 'Backend unreachable' });
		ctrl = new FoldersController();
		await ctrl.load();
		const t = await mountPane();
		expect(text(t)).toContain('Backend unreachable');
		apiMock.getModelRoots.mockResolvedValue({ success: true, data: overview([HOME]) });
		await click(btn(t, 'Retry'));
		expect(text(t.querySelector('h2'))).toBe('PotionUI');
	});

	it('falls back to Downloads & priority when there are no folders at all', async () => {
		await load(overview([]));
		const t = await mountPane();
		expect(text(t.querySelector('h2'))).toBe('Downloads & priority');
	});

	it('the phone Folder select lists folders and the across-folders views and switches the pane', async () => {
		await load(overview([HOME, DEPOT], { unplaced: [{ dir: '/x', count: 1, types: ['lora'] }] }));
		const t = await mountPane();
		const select = t.querySelector<HTMLSelectElement>('select[aria-label="Folder"]')!;
		expect(Array.from(select.options).map((o) => o.textContent)).toEqual(['PotionUI', 'Depot', 'Downloads & priority', 'Indexing', 'Found outside your folders']);
		select.value = 'depot';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		flushSync();
		expect(text(t.querySelector('h2'))).toBe('Depot');
	});

	it('the phone header offers Back to Models on every view and Add folder opens the dialog', async () => {
		viewportWidth.set(390);
		await load(overview([HOME], { unplaced: [{ dir: '/x', count: 1, types: ['lora'] }] }));
		const t = await mountPane();
		await click(t.querySelector('button[data-detail-back]'));
		expect(onBack).toHaveBeenCalledTimes(1);
		for (const view of ['routing', 'suggested', 'indexing']) {
			ctrl.selected = view;
			await settle();
			flushSync();
			expect(t.querySelector('button[data-detail-back]'), view).toBeTruthy();
		}
		await click(btn(t, 'Add folder'));
		expect(ctrl.addOpen).toBe(true);
	});

	it('a folder created from the add dialog becomes the selected folder', async () => {
		await load(overview([HOME]));
		const t = await mountPane();
		await click(btn(t, 'Add folder'));
		await click(document.querySelector('[data-add-stub]'));
		expect(ctrl.selected).toBe('fresh');
	});

	it('the add dialog receives the suggested path from Found outside folders', async () => {
		await load(overview([HOME], { unplaced: [{ dir: '/x/models', count: 4, types: ['lora'] }] }));
		ctrl.selected = 'suggested';
		const t = await mountPane();
		await click(btn(t, 'Add as folder'));
		expect(document.querySelector('[data-add-stub]')!.getAttribute('data-initial-path')).toBe('/x/models');
	});

	it('desktop has no Back control', async () => {
		await load(overview([HOME]));
		const t = await mountPane();
		expect(t.querySelector('button[data-detail-back]')).toBeNull();
	});
});

describe('Folders pane - failed mutations', () => {
	const expectFailed = (message = 'boom') => {
		expect(errorToast).toHaveBeenCalledWith(message);
		expect(successToast).not.toHaveBeenCalled();
	};

	it('a failed rename shows the error and keeps the panel, the typed name and the old title', async () => {
		apiMock.updateModelRoot.mockResolvedValue(BOOM);
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		const p = panel()!;
		const label = p.querySelector('#d-label-depot') as HTMLInputElement;
		label.value = 'Archive';
		label.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		flushSync();
		await click(btn(p, /Save changes/));
		expectFailed();
		expect(panel()).toBeTruthy();
		expect((panel()!.querySelector('#d-label-depot') as HTMLInputElement).value).toBe('Archive');
		expect(btn(panel()!, /Save changes/)).toBeTruthy();
		expect(text(t.querySelector('h2'))).toBe('Depot');
		expect(ctrl.overview!.roots[0].label).toBe('Depot');
	});

	it('a rejected rename shows an error toast and no success', async () => {
		apiMock.updateModelRoot.mockRejectedValue(new Error('network down'));
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		const label = panel()!.querySelector('#d-label-depot') as HTMLInputElement;
		label.value = 'Archive';
		label.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		flushSync();
		await click(btn(panel()!, /Save changes/));
		expect(errorToast).toHaveBeenCalledTimes(1);
		expect(successToast).not.toHaveBeenCalled();
		expect(panel()).toBeTruthy();
	});

	it('a failed read-only toggle shows the error and the folder stays as it was', async () => {
		apiMock.updateModelRoot.mockResolvedValue(BOOM);
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		await click(panel()!.querySelector('[role="switch"][aria-label="Read-only"]'));
		expectFailed();
		expect(ctrl.overview!.roots[0].read_only).toBe(true);
		expect(panel()).toBeTruthy();
	});

	it('a failed header detection toggle shows the error and the switch keeps its state', async () => {
		apiMock.setModelRootBindingScan.mockResolvedValue(BOOM);
		await load(overview([HOME]));
		const t = await mountPane();
		await click(rows(t)[0]);
		const sw = panel()!.querySelector('[role="switch"][aria-label="Detect type from file"]') as HTMLInputElement;
		await click(sw);
		expectFailed();
		expect(ctrl.overview!.roots[0].bindings[0].scan_headers).toBe(false);
		expect(panel()).toBeTruthy();
	});

	it('a failed remove keeps the row, shows the error and does not report success', async () => {
		apiMock.updateModelRoot.mockResolvedValue(BOOM);
		await load(overview([DEPOT]));
		const t = await mountPane();
		await pick(rows(t)[1], 'Remove from folder');
		expectFailed();
		expect(rows(t)).toHaveLength(2);
		expect(ctrl.overview!.roots[0].bindings).toHaveLength(2);
	});

	it('a failed remove from the panel keeps the panel open on that type folder', async () => {
		apiMock.updateModelRoot.mockResolvedValue(BOOM);
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(rows(t)[1]);
		await click(btn(panel()!, /Remove from folder/));
		expectFailed();
		expect(text(panel())).toContain('Lora');
		expect(rows(t)).toHaveLength(2);
	});

	it('a failed delete keeps the folder, the selection and the open panel', async () => {
		apiMock.deleteModelRoot.mockResolvedValue(BOOM);
		await load(overview([HOME, DEPOT]));
		ctrl.selected = 'depot';
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		await click(btn(panel()!, /Delete folder/));
		expectFailed();
		expect(ctrl.effective).toBe('depot');
		expect(ctrl.overview!.roots.map((r) => r.id)).toEqual(['home', 'depot']);
		expect(panel()).toBeTruthy();
		expect(text(t.querySelector('h2'))).toBe('Depot');
	});

	it('a rejected delete shows an error toast and keeps the folder', async () => {
		apiMock.deleteModelRoot.mockRejectedValue(new Error('network down'));
		await load(overview([HOME, DEPOT]));
		ctrl.selected = 'depot';
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		await click(btn(panel()!, /Delete folder/));
		expect(errorToast).toHaveBeenCalledTimes(1);
		expect(successToast).not.toHaveBeenCalled();
		expect(ctrl.overview!.roots).toHaveLength(2);
		expect(panel()).toBeTruthy();
	});

	it('a failed reorder shows the error and keeps the order', async () => {
		apiMock.reorderModelRoots.mockResolvedValue(BOOM);
		await load(overview([HOME, root('depot', { bindings: [binding('checkpoint', 'ckpt')] })]));
		ctrl.selected = 'routing';
		const t = await mountPane();
		await click(btn(t, 'Move PotionUI later for Checkpoint'));
		expectFailed();
		expect(ctrl.overview!.types.find((x) => x.model_type === 'checkpoint')!.order).toEqual(['home', 'depot']);
		expect(btn(t, 'Move PotionUI earlier for Checkpoint')!.disabled).toBe(true);
	});

	it('a failed Use for downloads shows the error and the badge stays where it was', async () => {
		apiMock.setModelRootWrite.mockResolvedValue(BOOM);
		await load(overview([DEPOT, HOME].map((r) => ({ ...r, read_only: false }))));
		ctrl.selected = 'depot';
		const t = await mountPane();
		await pick(rows(t)[0], 'Use for downloads');
		expectFailed();
		expect(ctrl.overview!.roots.find((r) => r.id === 'depot')!.bindings[0].is_write).toBe(false);
		expect(text(rows(t)[0])).not.toContain('Downloads');
	});

	it('a rejected Use for downloads shows an error toast', async () => {
		apiMock.setModelRootWrite.mockRejectedValue(new Error('network down'));
		await load(overview([{ ...DEPOT, read_only: false }]));
		const t = await mountPane();
		await pick(rows(t)[0], 'Use for downloads');
		expect(errorToast).toHaveBeenCalledTimes(1);
		expect(successToast).not.toHaveBeenCalled();
	});

	it('a failed Check now shows an error and leaves the folder as it was', async () => {
		apiMock.probeModelRoot.mockResolvedValue(BOOM);
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Check Depot now'));
		expect(errorToast).toHaveBeenCalledWith('Failed to check the folder.');
		expect(successToast).not.toHaveBeenCalled();
		expect(text(t.querySelector('h2'))).toBe('Depot');
		expect(rows(t)).toHaveLength(2);
	});

	it('a rejected Check now shows an error toast', async () => {
		apiMock.probeModelRoot.mockRejectedValue(new Error('network down'));
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Check Depot now'));
		expect(errorToast).toHaveBeenCalledTimes(1);
		expect(successToast).not.toHaveBeenCalled();
	});

	it('a failed Detect again shows the error, adds nothing and keeps the table', async () => {
		apiMock.detectModelRoot.mockResolvedValue(BOOM);
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Detect again'));
		expectFailed();
		expect(text(t.querySelector('[data-again-panel]'))).toContain('boom');
		expect(btn(t, /Add selected/)).toBeUndefined();
		expect(rows(t)).toHaveLength(2);
		expect(apiMock.updateModelRoot).not.toHaveBeenCalled();
	});

	it('a rejected Detect again shows an error toast', async () => {
		apiMock.detectModelRoot.mockRejectedValue(new Error('network down'));
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Detect again'));
		expect(errorToast).toHaveBeenCalledTimes(1);
		expect(successToast).not.toHaveBeenCalled();
		expect(rows(t)).toHaveLength(2);
	});

	it('a failed Add selected keeps the found folders ticked and reports no success', async () => {
		apiMock.detectModelRoot.mockResolvedValue({
			success: true,
			data: { path: '/mnt/depot', effective_path: '/mnt/depot', state: 'online', layout: 'typed', suggestions: [{ model_type: 'controlnet', subdir: 'controlnet', matched_by: 'canonical', file_count: 2, file_count_truncated: false }] }
		});
		apiMock.updateModelRoot.mockResolvedValue(BOOM);
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Detect again'));
		await click(btn(t.querySelector('[data-again-panel]')!, /Add selected/));
		expectFailed();
		expect(t.querySelector('[data-again-panel]')).toBeTruthy();
		expect(btn(t.querySelector('[data-again-panel]')!, /Add selected/)).toBeTruthy();
	});
});

describe('Folders pane - refreshes and reverted switches', () => {
	it('keeps the side panel open when a refresh replaces the folder object', async () => {
		await load(overview([HOME, DEPOT]));
		await loadFresh(overview([root('depot', { bindings: Array.from({ length: 14 }, (_, i) => binding('lora', `lora${i}`)) })]));
		const t = await mountPane();
		await click(rows(t)[13]);
		expect(panel()).toBeTruthy();
		const before = ctrl.overview!.roots[0];
		await click(btn(t, 'Check Depot now'));
		expect(ctrl.overview!.roots[0]).not.toBe(before);
		expect(apiMock.probeModelRoot).toHaveBeenCalled();
		expect(panel()).toBeTruthy();
		expect(rows(t)).toHaveLength(14);
	});

	it('keeps Folder settings open and the typed draft across a Check now refresh', async () => {
		await loadFresh(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		const label = panel()!.querySelector('#d-label-depot') as HTMLInputElement;
		label.value = 'Archive';
		label.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		flushSync();
		await click(btn(panel()!, 'Check now'));
		expect(panel()).toBeTruthy();
		expect((panel()!.querySelector('#d-label-depot') as HTMLInputElement).value).toBe('Archive');
	});

	it('keeps the binding panel open after a successful header detection toggle', async () => {
		await loadFresh(overview([HOME]));
		const t = await mountPane();
		await click(rows(t)[0]);
		await click(panel()!.querySelector('[role="switch"][aria-label="Detect type from file"]'));
		expect(apiMock.setModelRootBindingScan).toHaveBeenCalled();
		expect(panel()).toBeTruthy();
	});

	it('resets the panel and page when another folder is selected', async () => {
		await loadFresh(overview([HOME, DEPOT]));
		const t = await mountPane();
		await click(rows(t)[0]);
		expect(panel()).toBeTruthy();
		ctrl.selected = 'depot';
		await settle();
		flushSync();
		expect(panel()).toBeNull();
	});

	it('puts the Detect type from file switch back after a failed change', async () => {
		apiMock.setModelRootBindingScan.mockResolvedValue(BOOM);
		await load(overview([HOME]));
		const t = await mountPane();
		await click(rows(t)[0]);
		await click(panel()!.querySelector('[role="switch"][aria-label="Detect type from file"]'));
		const sw = panel()!.querySelector('[role="switch"][aria-label="Detect type from file"]') as HTMLInputElement;
		expect(sw.checked).toBe(false);
	});

	it('puts the Read-only switch back after a failed change', async () => {
		apiMock.updateModelRoot.mockResolvedValue(BOOM);
		await load(overview([DEPOT]));
		const t = await mountPane();
		await click(btn(t, 'Folder settings'));
		await click(panel()!.querySelector('[role="switch"][aria-label="Read-only"]'));
		expect((panel()!.querySelector('[role="switch"][aria-label="Read-only"]') as HTMLInputElement).checked).toBe(true);
	});
});
