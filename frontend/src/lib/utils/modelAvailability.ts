// Display mapping for per-backend model availability confidence.
// See docs/models.md "Indexing is per backend" for what each level means:
// - verified: bytes hashed on this host (native engine filesystem scan)
// - reported: a backend claimed a name + size over its own API (e.g. ComfyUI /experiment/models)
// - name_only: a backend claimed only a name, no size (degraded ComfyUI listing)
// - conflict: the backend's own copy was hashed and disagrees with the model's canonical
//   digest - that row is excluded from routing (see docs/models.md "Digest conflicts").
import type { ModelAvailabilityConfidence } from '$lib/types/models';

export interface ConfidenceDisplay {
	label: string;
	/** Badge variant. `name_only` is deliberately non-alarming - low confidence isn't a problem,
	 * just a weaker guarantee - so it does not use `warning`. `conflict` is the one level that
	 * is an actual problem (the backend can't be used for this model), so it alone uses `danger`. */
	variant: 'success' | 'info' | 'neutral' | 'danger';
}

const CONFIDENCE_DISPLAY: Record<ModelAvailabilityConfidence, ConfidenceDisplay> = {
	verified: { label: 'Verified', variant: 'success' },
	reported: { label: 'Reported', variant: 'info' },
	name_only: { label: 'Name only', variant: 'neutral' },
	conflict: { label: 'Conflict', variant: 'danger' }
};

const FALLBACK_DISPLAY: ConfidenceDisplay = { label: 'Unknown', variant: 'neutral' };

/** Maps a confidence level to a badge label/variant. Falls back gracefully for any
 * value not in the known set, since this is a string coming off the network. */
export function confidenceDisplay(
	confidence: ModelAvailabilityConfidence | string | null | undefined
): ConfidenceDisplay {
	if (!confidence) return FALLBACK_DISPLAY;
	return CONFIDENCE_DISPLAY[confidence as ModelAvailabilityConfidence] ?? FALLBACK_DISPLAY;
}

/** Tooltip text for a `conflict` row's badge: names the required action and, when both
 * digests are known, shows truncated expected/found values so the mismatch is verifiable
 * without leaving the card. */
export function digestConflictTooltip(
	foundDigest: string | null | undefined,
	expectedDigest: string | null | undefined
): string {
	const truncate = (digest: string) => `${digest.slice(0, 12)}...`;
	const detail =
		expectedDigest && foundDigest
			? ` Expected ${truncate(expectedDigest)}, found ${truncate(foundDigest)}`
			: '';
	return `This backend's copy does not match the expected file.${detail} Re-sync or replace the file on this backend, then re-index it.`;
}

/**
 * Whether an empty `backend_ids` list should read as "unavailable" (a fact) or
 * "unknown" (nothing has been indexed anywhere yet). See docs/models.md.
 */
export function isAvailabilityKnown(backendIds: unknown, indexed: boolean): boolean {
	return indexed || (Array.isArray(backendIds) && backendIds.length > 0);
}
