import { describe, it, expect } from 'vitest';
import { sectionFromStatus, statusFromSection, GENERATION_LIBRARY_SECTIONS } from './generationsSections';

describe('sectionFromStatus / statusFromSection', () => {
	it('maps every sidebar-backed status straight through', () => {
		expect(sectionFromStatus('completed')).toBe('completed');
		expect(sectionFromStatus('running')).toBe('running');
		expect(sectionFromStatus('failed')).toBe('failed');
		expect(sectionFromStatus('cancelled')).toBe('cancelled');
	});

	it('falls back to the "all" section for statuses with no dedicated row (pending, cleared)', () => {
		expect(sectionFromStatus('pending')).toBe('all');
		expect(sectionFromStatus('')).toBe('all');
	});

	it('round-trips every listed section through statusFromSection', () => {
		for (const section of GENERATION_LIBRARY_SECTIONS) {
			expect(sectionFromStatus(statusFromSection(section.id))).toBe(section.id);
		}
	});

	it('clears the status filter for the "all" section', () => {
		expect(statusFromSection('all')).toBe('');
	});
});
