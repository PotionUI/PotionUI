export const ORGANIZE_PATH = '/auto-organize';

const AREA_BY_SLUG: Record<string, string> = {
	generations: '/history',
	uploads: '/library',
	models: '/models'
};

export function isOrganizePath(pathname: string): boolean {
	return pathname === ORGANIZE_PATH || pathname.startsWith(`${ORGANIZE_PATH}/`);
}

export function navAreaPath(pathname: string, search: string | URLSearchParams = ''): string {
	if (!isOrganizePath(pathname)) return pathname;
	const params = typeof search === 'string' ? new URLSearchParams(search) : search;
	return AREA_BY_SLUG[params.get('subject') ?? ''] ?? '/history';
}

export function pathMatchesNavItem(itemPath: string, pathname: string, search: string | URLSearchParams = ''): boolean {
	const effective = navAreaPath(pathname, search);
	return effective === itemPath || effective.startsWith(`${itemPath}/`);
}

export function notificationLinkFor(type: unknown, metadata: Record<string, unknown> | null | undefined): string | null {
	if (type === 'organize.rule_paused') {
		const ruleId = metadata?.rule_id;
		return typeof ruleId === 'string' && ruleId
			? `${ORGANIZE_PATH}?rule=${encodeURIComponent(ruleId)}`
			: ORGANIZE_PATH;
	}
	if (type === 'organize.job_finished') return `${ORGANIZE_PATH}?view=activity`;
	return null;
}
