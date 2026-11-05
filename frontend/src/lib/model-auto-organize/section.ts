export const MODELS_AUTO_ORGANIZE_SECTION = 'auto-organize';

export function autoOrganizeHref(pathname: string, extra: Record<string, string | null | undefined> = {}): string {
	const params = new URLSearchParams({ tab: 'models', view: MODELS_AUTO_ORGANIZE_SECTION });
	for (const [key, value] of Object.entries(extra)) if (value) params.set(key, value);
	return `${pathname}?${params.toString()}`;
}
