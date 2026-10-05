import type { OrganizeCollectionScope, OrganizeSubject } from '$lib/types/organize';

export type OrganizeSlug = 'generations' | 'uploads' | 'models';

export const ORGANIZE_SUBJECTS: readonly {
	subject: OrganizeSubject;
	slug: OrganizeSlug;
	label: string;
	icon: string;
	scope: OrganizeCollectionScope;
}[] = [
	{ subject: 'generation', slug: 'generations', label: 'Generations', icon: 'clock', scope: 'history' },
	{ subject: 'upload', slug: 'uploads', label: 'Uploads', icon: 'image', scope: 'library' },
	{ subject: 'model', slug: 'models', label: 'Models', icon: 'cube', scope: 'models' }
];

export function subjectFromSlug(slug: string | null | undefined): OrganizeSubject {
	return ORGANIZE_SUBJECTS.find((s) => s.slug === slug)?.subject ?? 'generation';
}

export function slugFromSubject(subject: OrganizeSubject): OrganizeSlug {
	return ORGANIZE_SUBJECTS.find((s) => s.subject === subject)?.slug ?? 'generations';
}

export function scopeFromSubject(subject: OrganizeSubject): OrganizeCollectionScope {
	return ORGANIZE_SUBJECTS.find((s) => s.subject === subject)?.scope ?? 'history';
}

export function organizeHref(
	subject: OrganizeSubject,
	extra: Record<string, string | null | undefined> = {}
): string {
	const params = new URLSearchParams();
	params.set('subject', slugFromSubject(subject));
	for (const [key, value] of Object.entries(extra)) {
		if (value) params.set(key, value);
	}
	return `/auto-organize?${params.toString()}`;
}
