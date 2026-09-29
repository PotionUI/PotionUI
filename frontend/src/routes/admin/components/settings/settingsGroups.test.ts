import { describe, it, expect } from 'vitest';
import { settingsGroupHasFooter, computeDirtyGroups, resolveSettingsGroupId, SETTINGS_GROUPS } from './settingsGroups';

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
		const dirty = computeDirtyGroups(['file_storage_directory', 'content_policy_nsfw']);
		expect(dirty).toEqual(new Set(['media_storage', 'content_safety']));
	});

	it('de-duplicates groups shared by multiple dirty keys', () => {
		const dirty = computeDirtyGroups(['thumbnail_sizes', 'thumbnail_video_fps']);
		expect(dirty).toEqual(new Set(['media_storage']));
	});

	it('maps every S3 backend key to media storage, so their edits ride the shared save bar', () => {
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
			expect(computeDirtyGroups([key])).toEqual(new Set(['media_storage']));
		}
	});

	it('maps housekeeping retention keys to the housekeeping group', () => {
		const housekeepingKeys = ['tmp_retention_days', 'run_report_retention_days', 'llm_trace_retention_days'];
		for (const key of housekeepingKeys) {
			expect(computeDirtyGroups([key])).toEqual(new Set(['housekeeping']));
		}
	});

	it('maps backup keys to the backups group', () => {
		const backupKeys = ['backup_destination', 'backup_retention', 'backup_default_tier'];
		for (const key of backupKeys) {
			expect(computeDirtyGroups([key])).toEqual(new Set(['backups']));
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

describe('resolveSettingsGroupId', () => {
	it('resolves a known group id as-is', () => {
		expect(resolveSettingsGroupId('backups')).toBe('backups');
	});

	it('maps the retired storage deep link to media storage', () => {
		expect(resolveSettingsGroupId('storage')).toBe('media_storage');
	});

	it('falls back to access for an unknown or missing group', () => {
		expect(resolveSettingsGroupId('not_a_real_group')).toBe('access');
		expect(resolveSettingsGroupId(null)).toBe('access');
	});
});
