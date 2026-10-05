import type { AutoOrganizeSubject } from '$lib/stores/autoOrganizeCounts';

export const LAST_SUBJECT_KEY = 'potionui.autoOrganize.lastSubject';

const SUBJECTS: readonly AutoOrganizeSubject[] = ['generations', 'uploads', 'models'];

export function readLastSubject(): AutoOrganizeSubject {
	try {
		const value = localStorage.getItem(LAST_SUBJECT_KEY);
		return SUBJECTS.find((s) => s === value) ?? 'generations';
	} catch {
		return 'generations';
	}
}

export function writeLastSubject(subject: AutoOrganizeSubject): void {
	try {
		localStorage.setItem(LAST_SUBJECT_KEY, subject);
	} catch {
		return;
	}
}
