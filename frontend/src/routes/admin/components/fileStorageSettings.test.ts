import { describe, expect, it } from 'vitest';
import { parseFileStorageSettings, buildFileStorageSettingsPayload } from './fileStorageSettings';

describe('parseFileStorageSettings', () => {
	it('defaults to the local backend when storage_backend is absent', () => {
		expect(parseFileStorageSettings({}).backend).toBe('local');
	});

	it('defaults to local for any value other than the literal "s3"', () => {
		expect(parseFileStorageSettings({ storage_backend: 'S3' }).backend).toBe('local');
		expect(parseFileStorageSettings({ storage_backend: 'gcs' }).backend).toBe('local');
	});

	it('recognizes the s3 backend', () => {
		expect(parseFileStorageSettings({ storage_backend: 's3' }).backend).toBe('s3');
	});

	it('defaults region to us-east-1 when blank or absent', () => {
		expect(parseFileStorageSettings({}).region).toBe('us-east-1');
		expect(parseFileStorageSettings({ s3_region: '' }).region).toBe('us-east-1');
	});

	it('preserves an explicit region', () => {
		expect(parseFileStorageSettings({ s3_region: 'eu-central-1' }).region).toBe('eu-central-1');
	});

	it('passes through the secret key mask as-is', () => {
		expect(parseFileStorageSettings({ s3_secret_key: '***' }).secretKey).toBe('***');
	});

	it('treats a non-string value as blank rather than throwing', () => {
		const parsed = parseFileStorageSettings({ s3_bucket: 42, s3_path_style: 'true' });
		expect(parsed.bucket).toBe('');
		expect(parsed.pathStyle).toBe(true); // Boolean('true') is truthy
	});

	it('coerces s3_path_style to a real boolean', () => {
		expect(parseFileStorageSettings({ s3_path_style: false }).pathStyle).toBe(false);
		expect(parseFileStorageSettings({ s3_path_style: true }).pathStyle).toBe(true);
		expect(parseFileStorageSettings({}).pathStyle).toBe(false);
	});
});

describe('buildFileStorageSettingsPayload', () => {
	it('round-trips a parsed state back into the settings key shape', () => {
		const raw = {
			storage_backend: 's3',
			s3_bucket: 'my-bucket',
			s3_prefix: 'prod',
			s3_endpoint_url: 'https://minio.local',
			s3_region: 'eu-central-1',
			s3_access_key_id: 'AKIDEXAMPLE',
			s3_secret_key: '***',
			s3_path_style: true
		};

		const payload = buildFileStorageSettingsPayload(parseFileStorageSettings(raw));

		expect(payload).toEqual(raw);
	});

	it('sends an unmodified mask back unchanged, matching the backend round-trip contract', () => {
		const state = parseFileStorageSettings({ s3_secret_key: '***' });
		expect(buildFileStorageSettingsPayload(state).s3_secret_key).toBe('***');
	});
});
