export type ContentPolicy = 'allowed' | 'blur' | 'blocked';

export const CONTENT_POLICY_OPTIONS: { id: ContentPolicy; label: string }[] = [
	{ id: 'allowed', label: 'Allowed' },
	{ id: 'blur', label: 'Blur' },
	{ id: 'blocked', label: 'Blocked' }
];

export const CONTENT_POLICY_INHERIT = 'inherit';

export function isContentPolicy(value: unknown): value is ContentPolicy {
	return value === 'allowed' || value === 'blur' || value === 'blocked';
}

export interface ContentSafetyStatus {
	policy: ContentPolicy;
	tagger: { present: boolean; device?: string | null; downloading: boolean };
	backfill: { total: number; rated: number; running: boolean };
}

export type TaggerNotice = { tone: 'warning' | 'danger'; text: string } | null;

export function taggerNotice(
	policy: ContentPolicy,
	tagger: { present: boolean; downloading: boolean } | null
): TaggerNotice {
	if (policy === 'allowed' || !tagger || tagger.present) return null;
	if (tagger.downloading) {
		return { tone: 'warning', text: 'The rating model is downloading. Content checks start once it is ready.' };
	}
	if (policy === 'blocked') {
		return {
			tone: 'danger',
			text: 'Blocked refuses every generation until the rating model is downloaded.'
		};
	}
	return {
		tone: 'warning',
		text: 'Until the rating model is downloaded, every output is blurred as a precaution.'
	};
}

export function backfillPercent(backfill: { total: number; rated: number }): number {
	if (backfill.total <= 0) return 100;
	return Math.min(100, Math.floor((backfill.rated / backfill.total) * 100));
}

export function isSystemRestrictedGroup(group: {
	name?: string;
	is_system?: boolean;
	restricted?: boolean;
}): boolean {
	return group.restricted === true || (!!group.is_system && group.name === 'Restricted content');
}
