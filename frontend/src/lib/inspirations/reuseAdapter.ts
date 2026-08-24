/**
 * Adapts an inspiration's `/params` response into the `ImportBundleReuse`
 * shape `buildImportBundleTabData` (historyReuse.ts) already knows how to
 * turn into a generate-tab payload. An inspiration's params carry no
 * `form_name` (unlike an imported bundle) - `mode` comes through from the
 * snapshot; a blank/missing one falls through `buildReuseTabData`'s own
 * `source.mode || 'txt2img'` default, the same fallback a bundle with a
 * blank mode would hit.
 */

import type { InspirationParamsResult } from '$lib/services/api/inspirations';
import type { ImportBundleReuse } from '$lib/types/history';

export function buildInspirationReuseSource(params: InspirationParamsResult): ImportBundleReuse {
	return {
		preset_id: params.preset_id ?? '',
		mode: params.mode ?? '',
		form_name: null,
		form_data: params.form_data ?? {}
	};
}

/** Friendly phrasing for the common media-carrying field names the
 * allowlist snapshot omits, so the reuse hint reads like a sentence instead
 * of a raw key. Falls back to a humanized version of the field name for
 * anything not in this table - the snapshot's `omitted_fields` names every
 * field the preset submitted, not just media ones. */
const FRIENDLY_FIELD_NAMES: Record<string, string> = {
	image: 'input image',
	init_image: 'input image',
	input_image: 'input image',
	video: 'input video',
	init_video: 'input video',
	input_video: 'input video',
	audio: 'input audio',
	media: 'input media',
	mask: 'mask'
};

function describeOmittedField(name: string): string {
	return FRIENDLY_FIELD_NAMES[name] ?? name.replace(/_/g, ' ');
}

/** One quiet line for the reuse flow when the snapshot dropped fields the
 * viewer will need to re-provide themselves - empty for a snapshot that
 * omitted nothing. */
export function formatOmittedFieldsHint(omittedFields: string[]): string {
	if (!omittedFields.length) return '';
	return `Not included: ${omittedFields.map(describeOmittedField).join(', ')} — provide your own.`;
}
