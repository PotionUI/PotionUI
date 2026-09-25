import type { SetupConsentVariant } from '$lib/services/api/setup';

export interface VariantAttribution {
	uploader: string;
	source_url: string | null;
	repo_id: string | null;
}

export function collectAttributions(variants: Iterable<SetupConsentVariant>): VariantAttribution[] {
	const seen = new Map<string, VariantAttribution>();
	for (const variant of variants) {
		if (!variant.uploader) continue;
		const key = `${variant.uploader}|${variant.source_url ?? variant.repo_id ?? ''}`;
		if (!seen.has(key)) {
			seen.set(key, {
				uploader: variant.uploader,
				source_url: variant.source_url,
				repo_id: variant.repo_id
			});
		}
	}
	return Array.from(seen.values());
}
