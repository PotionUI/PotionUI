import { describe, it, expect } from 'vitest';
import { settingsGroupHasFooter, computeDirtyGroups, SETTINGS_GROUPS } from './settingsGroups';

describe('settingsGroupHasFooter', () => {
	it('hides the footer for the read-only Logs group', () => {
		expect(settingsGroupHasFooter('logs')).toBe(false);
	});

	it('shows the footer for every other group', () => {
		for (const group of SETTINGS_GROUPS) {
			if (group.id === 'logs') continue;
			expect(settingsGroupHasFooter(group.id)).toBe(true);
		}
	});
});

describe('computeDirtyGroups', () => {
	it('maps dirty settings keys to their owning groups', () => {
		const dirty = computeDirtyGroups(['file_storage_directory', 'nsfw']);
		expect(dirty).toEqual(new Set(['storage', 'content_safety']));
	});

	it('de-duplicates groups shared by multiple dirty keys', () => {
		const dirty = computeDirtyGroups(['thumbnail_sizes', 'tmp_retention_days']);
		expect(dirty).toEqual(new Set(['storage']));
	});

	it('maps every S3 backend key to storage, so their edits ride the shared save bar', () => {
		const s3Keys = [
			'storage_backend',
			's3_bucket',
			's3_prefix',
			's3_endpoint_url',
			's3_region',
			's3_access_key_id',
			's3_secret_key',
			's3_path_style'
		];
		for (const key of s3Keys) {
			expect(computeDirtyGroups([key])).toEqual(new Set(['storage']));
		}
	});

	it('ignores keys with no owning group', () => {
		const dirty = computeDirtyGroups(['not_a_real_setting']);
		expect(dirty).toEqual(new Set());
	});

	it('returns an empty set for no dirty keys', () => {
		expect(computeDirtyGroups([])).toEqual(new Set());
	});
});
