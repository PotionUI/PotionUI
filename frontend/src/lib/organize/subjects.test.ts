import { describe, it, expect } from 'vitest';
import { ORGANIZE_SUBJECTS, slugFromSubject, subjectFromSlug } from './subjects';

describe('organize subjects', () => {
	it('names the library area Library in the menu and keeps the uploads slug', () => {
		const entry = ORGANIZE_SUBJECTS.find((s) => s.subject === 'upload')!;
		expect(entry.label).toBe('Library');
		expect(entry.slug).toBe('uploads');
		expect(slugFromSubject('upload')).toBe('uploads');
		expect(subjectFromSlug('uploads')).toBe('upload');
	});
});
