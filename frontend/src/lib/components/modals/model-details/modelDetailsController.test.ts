import { describe, expect, it } from 'vitest';
import {
	resolveModelDetailsCapabilities,
	toModelSummary,
	toAdminModelDetails
} from './modelDetailsController';

const ADMIN_ONLY_FIELDS = [
	'file_path',
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
	file_path: '/models/checkpoint/model.safetensors',
	file_size: 12345,
	sha256: 'deadbeef',
	indexed_at: '2026-01-02T00:00:00Z',
	updated_at: '2026-01-03T00:00:00Z',
	prompting_guidance: 'write it like this',
	is_directory: false
};

describe('resolveModelDetailsCapabilities', () => {
	it('grants operational/edit/availability capabilities only to admin', () => {
		const admin = resolveModelDetailsCapabilities('admin');
		expect(admin.canEditMetadata).toBe(true);
		expect(admin.canEditPromptingGuidance).toBe(true);
		expect(admin.canViewOperationalDetails).toBe(true);
		expect(admin.canViewAvailability).toBe(true);
		expect(admin.canManagePreviewGallery).toBe(true);
		expect(admin.canManageAssignments).toBe(true);
		expect(admin.canManageLibrary).toBe(false);
	});

	it('grants only library actions to the library scope', () => {
		const library = resolveModelDetailsCapabilities('library');
		expect(library.canEditMetadata).toBe(false);
		expect(library.canEditPromptingGuidance).toBe(false);
		expect(library.canViewOperationalDetails).toBe(false);
		expect(library.canViewAvailability).toBe(false);
		expect(library.canManagePreviewGallery).toBe(false);
		expect(library.canManageAssignments).toBe(false);
		expect(library.canManageLibrary).toBe(true);
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
			file_path: '/models/checkpoint/model.safetensors',
			file_size: 12345,
			sha256: 'deadbeef',
			indexed_at: '2026-01-02T00:00:00Z',
			updated_at: '2026-01-03T00:00:00Z',
			prompting_guidance: 'write it like this',
			is_directory: false
		});
	});

	it('defaults missing operational fields to null rather than leaving them undefined', () => {
		const admin = toAdminModelDetails({ id: 'm2', filename: 'x', model_type: 'lora' });
		expect(admin.file_path).toBeNull();
		expect(admin.file_size).toBeNull();
		expect(admin.sha256).toBeNull();
		expect(admin.indexed_at).toBeNull();
		expect(admin.prompting_guidance).toBeNull();
		expect(admin.is_directory).toBe(false);
	});
});
