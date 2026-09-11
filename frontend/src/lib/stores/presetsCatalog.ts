/**
 * Shared, TTL-bounded cache for the installed-presets catalogue
 * (`GET /api/presets`, `includeUninstalled=false`). The layout starts a load
 * at boot so callers that need it soon after (the Generate page, chat
 * context lookups) find it already in flight or resolved instead of each
 * firing their own request.
 */
import { api } from '$lib/services/api/index';
import type { APIResponse, PresetInfo } from '$lib/services/api/index';

/** How long a resolved catalogue stays fresh before a call refetches it - presets also change via admin actions and on-disk edits this module never observes directly. */
const FRESHNESS_MS = 60_000;

let cached: Promise<APIResponse<PresetInfo[]>> | null = null;
let cachedAt = 0;
let pending = false;

/**
 * Resolves with the installed-presets catalogue, reusing an in-flight fetch
 * or a resolved one still inside the freshness window unless `force` is set.
 * A failed fetch clears the cache so the next call retries instead of
 * replaying the rejection.
 */
export function loadPresets(options: { force?: boolean } = {}): Promise<APIResponse<PresetInfo[]>> {
	const isFresh = pending || Date.now() - cachedAt < FRESHNESS_MS;
	if (cached && !options.force && isFresh) {
		return cached;
	}

	pending = true;
	cachedAt = Date.now();
	const request = api
		.listPresets()
		.then((response) => {
			pending = false;
			return response;
		})
		.catch((err) => {
			pending = false;
			cached = null;
			throw err;
		});
	cached = request;
	return request;
}

/** Drops the cached catalogue so the next `loadPresets()` call refetches. */
export function invalidatePresets(): void {
	cached = null;
	pending = false;
}
