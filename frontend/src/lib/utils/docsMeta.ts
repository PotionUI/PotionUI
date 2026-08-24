import type {
	DocStatus,
	DocTechniqueRef,
	ModelMeta,
	TechniqueMeta
} from '$lib/types/api';

/** Badge variant for a doc status, matching the Instrument system's
 * state-is-blue/action-is-white split: stable = success, experimental =
 * warning (proceed with awareness), needs-gpu-validation = info (unverified,
 * not a warning about behavior). Unknown/missing status -> neutral. */
export function statusBadgeVariant(
	status: DocStatus | null | undefined
): 'success' | 'warning' | 'info' | 'neutral' {
	switch (status) {
		case 'stable':
			return 'success';
		case 'experimental':
			return 'warning';
		case 'needs-gpu-validation':
			return 'info';
		default:
			return 'neutral';
	}
}

/** Human label for a doc status badge/dot. */
export function statusLabel(status: DocStatus | null | undefined): string {
	switch (status) {
		case 'stable':
			return 'Stable';
		case 'experimental':
			return 'Experimental';
		case 'needs-gpu-validation':
			return 'Needs GPU validation';
		default:
			return 'Unknown';
	}
}

/** A tree/list status dot is only shown for statuses worth flagging before a
 * reader opens the doc -- 'stable' is the unmarked default. */
export function showsStatusDot(status: DocStatus | null | undefined): boolean {
	return status === 'experimental' || status === 'needs-gpu-validation';
}

/** arXiv id ("2410.02416" or "arXiv:2410.02416") -> its abstract page URL. */
export function arxivUrl(arxivId: string): string {
	const id = arxivId.replace(/^arxiv:/i, '').trim();
	return `https://arxiv.org/abs/${id}`;
}

export function isTechniqueMeta(
	meta: TechniqueMeta | ModelMeta | null | undefined
): meta is TechniqueMeta {
	return Boolean(meta) && 'category_group' in (meta as object) && 'knobs' in (meta as object);
}

export function isModelMeta(
	meta: TechniqueMeta | ModelMeta | null | undefined
): meta is ModelMeta {
	return Boolean(meta) && 'family_key' in (meta as object) && 'spec' in (meta as object);
}

/** Category groups that read as "make it faster / fit in less VRAM" vs.
 * "make the output better" -- the two auto-sections a model doc renders
 * above its markdown body (contract from #48/#50). */
const OPTIMIZATION_CATEGORY_GROUPS = new Set(['Performance', 'Memory', 'Sampling']);
const QUALITY_CATEGORY_GROUPS = new Set(['Quality']);

export interface GroupedTechniqueRefs {
	optimizations: DocTechniqueRef[];
	quality: DocTechniqueRef[];
}

/** Split a model doc's `refs.techniques` into the "Optimizations" and
 * "Quality techniques" auto-sections. Techniques in neither group (or with an
 * unrecognized category_group) are omitted rather than guessed into a
 * section -- ModelHeader only renders a section that has at least one item. */
export function groupTechniqueRefs(techniques: DocTechniqueRef[] | undefined): GroupedTechniqueRefs {
	const optimizations: DocTechniqueRef[] = [];
	const quality: DocTechniqueRef[] = [];
	for (const t of techniques ?? []) {
		if (OPTIMIZATION_CATEGORY_GROUPS.has(t.category_group)) {
			optimizations.push(t);
		} else if (QUALITY_CATEGORY_GROUPS.has(t.category_group)) {
			quality.push(t);
		}
	}
	return { optimizations, quality };
}
