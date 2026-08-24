/**
 * Pure normalize/build helpers for `FileStorageCard.svelte`, split out so
 * they're testable without mounting the component.
 */

export interface FileStorageSettingsState {
	backend: 'local' | 's3';
	bucket: string;
	prefix: string;
	endpointUrl: string;
	region: string;
	accessKeyId: string;
	/** May be the server's mask ("***") when a key is already configured -
	 * left untouched by the caller, it round-trips back and the stored
	 * secret is unchanged (`_is_stored_secret_mask` on the backend). */
	secretKey: string;
	pathStyle: boolean;
}

const DEFAULT_REGION = 'us-east-1';

function asString(value: unknown): string {
	return typeof value === 'string' ? value : '';
}

/** `GET /api/settings` returns the whole flat settings map - this picks out
 * and types only the file-storage-relevant keys. */
export function parseFileStorageSettings(raw: Record<string, unknown>): FileStorageSettingsState {
	return {
		backend: raw.storage_backend === 's3' ? 's3' : 'local',
		bucket: asString(raw.s3_bucket),
		prefix: asString(raw.s3_prefix),
		endpointUrl: asString(raw.s3_endpoint_url),
		region: asString(raw.s3_region) || DEFAULT_REGION,
		accessKeyId: asString(raw.s3_access_key_id),
		secretKey: asString(raw.s3_secret_key),
		pathStyle: Boolean(raw.s3_path_style)
	};
}

/** The `PUT /api/settings` batch body for the current form state. */
export function buildFileStorageSettingsPayload(
	state: FileStorageSettingsState
): Record<string, unknown> {
	return {
		storage_backend: state.backend,
		s3_bucket: state.bucket,
		s3_prefix: state.prefix,
		s3_endpoint_url: state.endpointUrl,
		s3_region: state.region,
		s3_access_key_id: state.accessKeyId,
		s3_secret_key: state.secretKey,
		s3_path_style: state.pathStyle
	};
}
