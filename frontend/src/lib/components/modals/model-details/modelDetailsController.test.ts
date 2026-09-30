import { describe, expect, it, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModelById: vi.fn(),
		getModelAvailability: vi.fn(),
		setModelType: vi.fn(),
		resetModelType: vi.fn()
	}
}));

import { api } from '$lib/services/api/index';
import {
	createAdminModelDetailsController,
	resolveModelDetailsCapabilities,
	toModelSummary,
	toAdminModelDetails
} from './modelDetailsController';

const ADMIN_ONLY_FIELDS = [
	'location',
	'copies',
	'file_size',
	'sha256',
	'indexed_at',
	'updated_at',
	'prompting_guidance',
	'is_directory'
];

const RAW_MODEL = {
	id: 'm1',
	filename: 'model.safetensors',
	name: 'My Model',
	model_type: 'checkpoint',
	created_at: '2026-01-01T00:00:00Z',
	description: 'desc',
	model_metadata: { triggers: ['trg'] },
	user_model_metadata: { strength: 1.5 },
	custom_name: 'custom',
	is_favorite: true,
	preview_media: { url: '/x', type: 'image' },
	files: [{ id: 'f1' }],
	tags: [{ id: 't1', name: 'tag' }],
	location: {
		root_id: 'r1',
		root_label: 'PotionUI models',
		logical_path: 'checkpoint/model.safetensors',
		path: '/models/checkpoint/model.safetensors'
	},
	copies: 1,
	file_size: 12345,
	sha256: 'deadbeef',
	indexed_at: '2026-01-02T00:00:00Z',
	updated_at: '2026-01-03T00:00:00Z',
	prompting_guidance: 'write it like this',
	is_directory: false,
	providers: [{ provider: 'x-provider', provider_model_id: '1', page_url: 'https://x.example/1', provider_label: 'X' }]
};

describe('resolveModelDetailsCapabilities', () => {
	it('grants operational/edit/availability capabilities only to admin', () => {
		const admin = resolveModelDetailsCapabilities('admin');
		expect(admin.canEditMetadata).toBe(true);
		expect(admin.canEditPromptingGuidance).toBe(true);
		expect(admin.canViewOperationalDetails).toBe(true);
		expect(admin.canViewAvailability).toBe(true);
		expect(admin.canManagePreviewGallery).toBe(true);
		expect(admin.canManageLibrary).toBe(false);
		expect(admin.canViewMirrors).toBe(true);
	});

	it('grants only library actions to the library scope', () => {
		const library = resolveModelDetailsCapabilities('library');
		expect(library.canEditMetadata).toBe(false);
		expect(library.canEditPromptingGuidance).toBe(false);
		expect(library.canViewOperationalDetails).toBe(false);
		expect(library.canViewAvailability).toBe(false);
		expect(library.canManagePreviewGallery).toBe(false);
		expect(library.canManageLibrary).toBe(true);
		expect(library.canViewMirrors).toBe(false);
	});
});

describe('toModelSummary', () => {
	it('never carries an admin-only field, even when the raw payload has one', () => {
		const summary = toModelSummary(RAW_MODEL) as unknown as Record<string, unknown>;
		for (const field of ADMIN_ONLY_FIELDS) {
			expect(summary).not.toHaveProperty(field);
		}
	});

	it('keeps every library-safe field', () => {
		const summary = toModelSummary(RAW_MODEL);
		expect(summary).toMatchObject({
			id: 'm1',
			filename: 'model.safetensors',
			name: 'My Model',
			model_type: 'checkpoint',
			description: 'desc',
			model_metadata: { triggers: ['trg'] },
			user_model_metadata: { strength: 1.5 },
			custom_name: 'custom',
			is_favorite: true,
			preview_media: { url: '/x', type: 'image' }
		});
	});
});

describe('toAdminModelDetails', () => {
	it('carries every admin-only field alongside the library-safe ones', () => {
		const admin = toAdminModelDetails(RAW_MODEL);
		expect(admin).toMatchObject({
			id: 'm1',
			filename: 'model.safetensors',
			location: {
				root_id: 'r1',
				root_label: 'PotionUI models',
				logical_path: 'checkpoint/model.safetensors',
				path: '/models/checkpoint/model.safetensors'
			},
			copies: 1,
			file_size: 12345,
			sha256: 'deadbeef',
			indexed_at: '2026-01-02T00:00:00Z',
			updated_at: '2026-01-03T00:00:00Z',
			prompting_guidance: 'write it like this',
			is_directory: false,
			providers: [{ provider: 'x-provider', provider_model_id: '1', page_url: 'https://x.example/1', provider_label: 'X' }]
		});
	});

	it('defaults missing operational fields to null rather than leaving them undefined', () => {
		const admin = toAdminModelDetails({ id: 'm2', filename: 'x', model_type: 'lora' });
		expect(admin.location).toBeNull();
		expect(admin.copies).toBe(0);
		expect(admin.file_size).toBeNull();
		expect(admin.sha256).toBeNull();
		expect(admin.indexed_at).toBeNull();
		expect(admin.prompting_guidance).toBeNull();
		expect(admin.is_directory).toBe(false);
		expect(admin.providers).toEqual([]);
	});
});

describe('saveType', () => {
	async function loaded() {
		vi.mocked(api.getModelById).mockResolvedValue({
			success: true,
			data: { model: { ...RAW_MODEL, model_type: 'undefined', type_info: { source: 'header' } } }
		});
		vi.mocked(api.getModelAvailability).mockResolvedValue({ success: false } as never);
		const controller = createAdminModelDetailsController();
		await controller.load('m1');
		return controller;
	}

	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('set calls setModelType and updates model_type and type_info', async () => {
		const controller = await loaded();
		vi.mocked(api.setModelType).mockResolvedValue({
			success: true,
			data: { model: { model_type: 'diffusion_model', type_info: { source: 'admin' } } }
		});
		await controller.saveType({ kind: 'set', modelType: 'diffusion_model' });
		expect(api.setModelType).toHaveBeenCalledWith('m1', 'diffusion_model');
		const model = get(controller.model);
		expect(model?.model_type).toBe('diffusion_model');
		expect(model?.type_info?.source).toBe('admin');
		expect(model?.description).toBe('desc');
	});

	it('reset calls resetModelType', async () => {
		const controller = await loaded();
		vi.mocked(api.resetModelType).mockResolvedValue({
			success: true,
			data: { model: { model_type: 'checkpoint', type_info: { source: 'folder' } } }
		});
		await controller.saveType({ kind: 'reset' });
		expect(api.resetModelType).toHaveBeenCalledWith('m1');
		expect(api.setModelType).not.toHaveBeenCalled();
		expect(get(controller.model)?.model_type).toBe('checkpoint');
	});

	it('none makes no call', async () => {
		const controller = await loaded();
		await controller.saveType({ kind: 'none' });
		expect(api.setModelType).not.toHaveBeenCalled();
		expect(api.resetModelType).not.toHaveBeenCalled();
		expect(get(controller.model)?.model_type).toBe('undefined');
	});

	it('throws and keeps the model when the response is not successful', async () => {
		const controller = await loaded();
		vi.mocked(api.setModelType).mockResolvedValue({ success: false } as never);
		await expect(controller.saveType({ kind: 'set', modelType: 'lora' })).rejects.toThrow();
		expect(get(controller.model)?.model_type).toBe('undefined');
	});
});
