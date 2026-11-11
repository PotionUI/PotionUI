import type { LibrarySectionMeta } from '$lib/components/library/librarySection';

export type SettingsSectionId = 'account' | 'plan' | 'appearance' | 'content' | 'notifications' | 'keys';

export const SETTINGS_SECTIONS: readonly LibrarySectionMeta<SettingsSectionId>[] = [
	{ id: 'account', label: 'Account', icon: 'user' },
	{ id: 'plan', label: 'Plan & usage', icon: 'gauge' },
	{ id: 'appearance', label: 'Appearance', icon: 'palette' },
	{ id: 'content', label: 'Content', icon: 'eye-off' },
	{ id: 'notifications', label: 'Notifications', icon: 'bell' },
	{ id: 'keys', label: 'API keys', icon: 'key' }
];

export function availableSettingsSections(opts: {
	contentRestricted: boolean;
	notificationsEmpty: boolean;
}): readonly LibrarySectionMeta<SettingsSectionId>[] {
	return SETTINGS_SECTIONS.filter(
		(entry) =>
			!(entry.id === 'content' && opts.contentRestricted) &&
			!(entry.id === 'notifications' && opts.notificationsEmpty)
	);
}

export function resolveSettingsSection(
	url: URL,
	available: readonly LibrarySectionMeta<SettingsSectionId>[]
): SettingsSectionId {
	const requested = url.hash.replace(/^#/, '') || url.searchParams.get('section') || '';
	return available.find((entry) => entry.id === requested)?.id ?? 'account';
}

export function settingsSectionUrl(current: URL, id: SettingsSectionId): URL {
	const next = new URL(current);
	next.hash = '';
	next.searchParams.set('section', id);
	return next;
}
