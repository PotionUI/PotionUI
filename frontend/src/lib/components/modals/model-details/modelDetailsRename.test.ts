import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const setModelLibraryName = vi.fn();
const getModelById = vi.fn();
const listModelPreviews = vi.fn();
const getModelAvailability = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		setModelLibraryName: (...args: unknown[]) => setModelLibraryName(...args),
		getModelById: (...args: unknown[]) => getModelById(...args),
		listModelPreviews: (...args: unknown[]) => listModelPreviews(...args),
		getModelAvailability: (...args: unknown[]) => getModelAvailability(...args)
	}
}));

vi.mock('$lib/stores/modelLibrary', () => ({
	modelLibraryStore: { updateModel: vi.fn(), setFavorite: vi.fn() }
}));

import {
	createAdminModelDetailsController,
	createLibraryModelDetailsController
} from './modelDetailsController';

const OLD_NAME = 'FE102-alpha';
const NEW_NAME = 'Custom Library Name';

function rawModel(name: string, customName: string | null) {
	return {
		id: 'm1',
		filename: 'model.safetensors',
		name,
		custom_name: customName,
		model_type: 'checkpoint',
		created_at: '2026-01-01T00:00:00Z',
		files: [],
		tags: [],
		is_favorite: false
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	listModelPreviews.mockResolvedValue({ success: true, data: { previews: [] } });
	getModelAvailability.mockResolvedValue({ success: true, data: {} });
	setModelLibraryName.mockResolvedValue({ success: true });
	getModelById
		.mockResolvedValueOnce({ success: true, data: { model: rawModel(OLD_NAME, null) } })
		.mockResolvedValue({ success: true, data: { model: rawModel(NEW_NAME, NEW_NAME) } });
});

describe.each([
	['library', createLibraryModelDetailsController],
	['admin', createAdminModelDetailsController]
])('%s controller rename', (_scope, createController) => {
	it('reflects the new display name without a reload', async () => {
		const controller = createController() as ReturnType<
			typeof createLibraryModelDetailsController
		>;
		await controller.load('m1');
		expect(get(controller.displayName)).toBe(OLD_NAME);

		await controller.rename(NEW_NAME);

		expect(setModelLibraryName).toHaveBeenCalledWith('m1', NEW_NAME);
		expect(get(controller.displayName)).toBe(NEW_NAME);
	});

	it('does not raise the loading flag while refreshing after a rename', async () => {
		const controller = createController() as ReturnType<
			typeof createLibraryModelDetailsController
		>;
		await controller.load('m1');

		const seen: boolean[] = [];
		const stop = controller.loading.subscribe((v) => seen.push(v));
		await controller.rename(NEW_NAME);
		stop();

		expect(seen).not.toContain(true);
	});

	it('leaves the display name alone when the request fails', async () => {
		setModelLibraryName.mockResolvedValue({ success: false });
		const controller = createController() as ReturnType<
			typeof createLibraryModelDetailsController
		>;
		await controller.load('m1');

		await controller.rename(NEW_NAME);

		expect(get(controller.displayName)).toBe(OLD_NAME);
	});
});
