import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/services/api', () => ({
	api: {
		getModelRoots: vi.fn(),
		detectModelRoot: vi.fn(),
		listModelLayouts: vi.fn(),
		createModelRoot: vi.fn(),
		updateModelRoot: vi.fn(),
		deleteModelRoot: vi.fn(),
		reorderModelRoots: vi.fn(),
		setModelRootWrite: vi.fn(),
		setModelRootBindingScan: vi.fn(),
		probeModelRoot: vi.fn(),
		browseModelRoot: vi.fn()
	}
}));

import { api } from '$lib/services/api';
import {
	ModelRootsState,
	browseModelRoot,
	detectModelRoot,
	listModelLayouts,
	serverPathPlaceholder
} from './state.svelte';
import type { ModelRoot, ModelRootsOverview } from '$lib/services/api/models';

function makeRoot(overrides: Partial<ModelRoot> = {}): ModelRoot {
	return {
		id: 'home',
		label: 'PotionUI models',
		path: 'models',
		kind: 'home',
		read_only: false,
		case_insensitive: false,
		state: 'online',
		state_reason: null,
		state_checked_at: null,
		bindings: [],
		...overrides
	};
}

function overview(overrides: Partial<ModelRootsOverview> = {}): ModelRootsOverview {
	return {
		roots: [makeRoot()],
		types: [],
		unplaced: [],
		indexing: { state: 'idle' },
		server_os: 'Linux',
		path_style: 'posix',
		...overrides
	};
}

describe('ModelRootsState', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('load() populates overview on success', async () => {
		vi.mocked(api.getModelRoots).mockResolvedValue({ success: true, data: overview() });

		const state = new ModelRootsState();
		await state.load();

		expect(state.overview).toEqual(overview());
		expect(state.loading).toBe(false);
		expect(state.error).toBeNull();
	});

	it('load() sets error on a failed response instead of throwing', async () => {
		vi.mocked(api.getModelRoots).mockResolvedValue({ success: false, message: 'nope' });

		const state = new ModelRootsState();
		await state.load();

		expect(state.overview).toBeNull();
		expect(state.error).toBe('nope');
	});

	it('rootById() finds a root from the loaded overview', async () => {
		const lib = makeRoot({ id: 'lib1', kind: 'library', label: 'NAS' });
		vi.mocked(api.getModelRoots).mockResolvedValue({ success: true, data: overview({ roots: [makeRoot(), lib] }) });

		const state = new ModelRootsState();
		await state.load();

		expect(state.rootById('lib1')?.label).toBe('NAS');
		expect(state.rootById('missing')).toBeUndefined();
	});

	it('create() refreshes the overview and returns the new root on success', async () => {
		const created = makeRoot({ id: 'lib1', kind: 'library', label: 'NAS' });
		vi.mocked(api.createModelRoot).mockResolvedValue({ success: true, data: created });
		vi.mocked(api.getModelRoots).mockResolvedValue({ success: true, data: overview({ roots: [makeRoot(), created] }) });

		const state = new ModelRootsState();
		const result = await state.create({ path: '/mnt/nas', bindings: [{ model_type: 'lora', subdir: 'loras' }] });

		expect(result).toEqual(created);
		expect(state.overview?.roots).toHaveLength(2);
		expect(state.mutating).toBe(false);
	});

	it('create() surfaces the error_response detail and rethrows', async () => {
		vi.mocked(api.createModelRoot).mockRejectedValue({
			response: { data: { detail: { error: 'model_roots_duplicate', message: "'x' is already a model root" } } }
		});

		const state = new ModelRootsState();
		await expect(state.create({ path: '/mnt/nas', bindings: [] })).rejects.toBeTruthy();
		expect(state.error).toBe("'x' is already a model root");
		expect(state.mutating).toBe(false);
	});

	it('remove() refreshes on success and returns true', async () => {
		vi.mocked(api.deleteModelRoot).mockResolvedValue({ success: true, data: { id: 'lib1', deleted: true } });
		vi.mocked(api.getModelRoots).mockResolvedValue({ success: true, data: overview() });

		const state = new ModelRootsState();
		const ok = await state.remove('lib1');

		expect(ok).toBe(true);
		expect(api.getModelRoots).toHaveBeenCalled();
	});

	it('reorder() replaces the overview with the response and returns true', async () => {
		const next = overview({ types: [{ model_type: 'lora', folder: 'loras', order: ['b', 'a'], write_root_id: 'b' }] });
		vi.mocked(api.reorderModelRoots).mockResolvedValue({ success: true, data: next });

		const state = new ModelRootsState();
		const ok = await state.reorder(['b', 'a'], 'lora');

		expect(ok).toBe(true);
		expect(api.reorderModelRoots).toHaveBeenCalledWith(['b', 'a'], 'lora');
		expect(state.overview).toEqual(next);
	});

	it('setWrite() rethrows and records the error on a write-probe failure', async () => {
		vi.mocked(api.setModelRootWrite).mockRejectedValue({
			response: { data: { detail: { error: 'model_roots_write_probe_failed', message: 'Permission denied' } } }
		});

		const state = new ModelRootsState();
		await expect(state.setWrite('lib1', 'lora')).rejects.toBeTruthy();
		expect(state.error).toBe('Permission denied');
	});

	it('setBindingScan() sends the binding identity and refreshes', async () => {
		vi.mocked(api.setModelRootBindingScan).mockResolvedValue({ success: true, data: makeRoot({ id: 'lib1' }) });
		vi.mocked(api.getModelRoots).mockResolvedValue({ success: true, data: overview() });

		const state = new ModelRootsState();
		await state.setBindingScan('lib1', { model_type: 'checkpoint', subdir: 'Stable-diffusion' }, true);

		expect(api.setModelRootBindingScan).toHaveBeenCalledWith('lib1', {
			model_type: 'checkpoint',
			subdir: 'Stable-diffusion',
			scan_headers: true
		});
		expect(api.getModelRoots).toHaveBeenCalled();
	});

	it('setBindingScan() returns null and records the message on an unsuccessful response', async () => {
		vi.mocked(api.setModelRootBindingScan).mockResolvedValue({ success: false, message: 'x' });

		const state = new ModelRootsState();
		const result = await state.setBindingScan('lib1', { model_type: 'checkpoint', subdir: 'a' }, true);

		expect(result).toBeNull();
		expect(state.error).toBe('x');
	});

	it('setBindingScan() rethrows and records the server message', async () => {
		vi.mocked(api.setModelRootBindingScan).mockRejectedValue({
			response: { data: { detail: { error: 'model_roots_invalid_binding', message: 'Not supported' } } }
		});

		const state = new ModelRootsState();
		await expect(
			state.setBindingScan('lib1', { model_type: 'lora', subdir: 'loras' }, true)
		).rejects.toBeTruthy();
		expect(state.error).toBe('Not supported');
	});
});

describe('detectModelRoot', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('returns the detection on success', async () => {
		const detection = {
			path: '/mnt/nas',
			effective_path: '/mnt/nas',
			state: 'online' as const,
			writable_hint: true,
			case_insensitive: false,
			layout: 'typed' as const,
			suggestions: [],
			single_type_guess: null,
			conflicts: [],
			warnings: []
		};
		vi.mocked(api.detectModelRoot).mockResolvedValue({ success: true, data: detection });

		const result = await detectModelRoot('/mnt/nas');

		expect(result).toEqual({ detection, error: null });
	});

	it('surfaces the error message when the request throws', async () => {
		vi.mocked(api.detectModelRoot).mockRejectedValue({ message: 'Network Error' });

		const result = await detectModelRoot('/mnt/nas');

		expect(result.detection).toBeNull();
		expect(result.error).toBe('Network Error');
	});
});

describe('serverPathPlaceholder', () => {
	it('shows a drive-letter example on a Windows server', () => {
		expect(serverPathPlaceholder('windows')).toBe('D:\\ComfyUI\\models');
	});

	it('shows a posix example on a Linux/macOS server', () => {
		expect(serverPathPlaceholder('posix')).toBe('/mnt/storage/ComfyUI/models');
	});

	it('defaults to posix when the server has not reported yet', () => {
		expect(serverPathPlaceholder(undefined)).toBe('/mnt/storage/ComfyUI/models');
	});
});

describe('detect with a profile and layouts', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('passes the chosen profile to the API', async () => {
		vi.mocked(api.detectModelRoot).mockResolvedValue({ success: false, message: 'nope' });
		await detectModelRoot('/mnt/nas', 'comfyui');
		expect(api.detectModelRoot).toHaveBeenCalledWith('/mnt/nas', 'comfyui');
	});

	it('lists layouts and falls back to an empty list on failure', async () => {
		vi.mocked(api.listModelLayouts).mockResolvedValue({
			layouts: [{ id: 'comfyui', label: 'ComfyUI', source: 'marketplace' }]
		});
		expect((await listModelLayouts()).map((l) => l.id)).toEqual(['comfyui']);
		vi.mocked(api.listModelLayouts).mockRejectedValue(new Error('down'));
		expect(await listModelLayouts()).toEqual([]);
	});

	it('setWrite() forwards the subdir', async () => {
		vi.mocked(api.setModelRootWrite).mockResolvedValue({ success: true, data: makeRoot() });
		vi.mocked(api.getModelRoots).mockResolvedValue({ success: true, data: overview() });
		await new ModelRootsState().setWrite('lib1', 'lora', 'LyCORIS');
		expect(api.setModelRootWrite).toHaveBeenCalledWith('lib1', 'lora', 'LyCORIS');
	});
});


describe('browseModelRoot', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	const listing = {
		path: '/srv/models',
		sub: 'models',
		parent: '',
		folders: [{ name: 'loras', subdir: 'models/loras', has_models: true, linked: false }],
		has_models: true,
		truncated: false
	};

	it('returns the listing and leaves out an empty sub', async () => {
		vi.mocked(api.browseModelRoot).mockResolvedValue({ success: true, data: listing } as never);
		const result = await browseModelRoot('/srv/models');
		expect(api.browseModelRoot).toHaveBeenCalledWith('/srv/models', undefined);
		expect(result).toEqual({ listing, error: null });
	});

	it('passes the subfolder through', async () => {
		vi.mocked(api.browseModelRoot).mockResolvedValue({ success: true, data: listing } as never);
		await browseModelRoot('/srv/models', 'models');
		expect(api.browseModelRoot).toHaveBeenCalledWith('/srv/models', 'models');
	});

	it('reports an unsuccessful response', async () => {
		vi.mocked(api.browseModelRoot).mockResolvedValue({ success: false, message: 'nope' } as never);
		expect(await browseModelRoot('/srv/models')).toEqual({ listing: null, error: 'nope' });
	});

	it('turns a request failure into the server message', async () => {
		vi.mocked(api.browseModelRoot).mockRejectedValue({
			response: { data: { detail: { error: 'model_roots_browse_failed', message: 'That subfolder doesn\'t exist.' } } }
		});
		expect(await browseModelRoot('/srv/models', 'gone')).toEqual({
			listing: null,
			error: "That subfolder doesn't exist."
		});
	});
});
