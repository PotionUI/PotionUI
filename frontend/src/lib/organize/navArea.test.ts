import { describe, it, expect } from 'vitest';
import { isOrganizePath, navAreaPath, notificationLinkFor, pathMatchesNavItem } from './navArea';

describe('navAreaPath', () => {
	it('maps the subject to the owning page', () => {
		expect(navAreaPath('/auto-organize', '?subject=generations')).toBe('/history');
		expect(navAreaPath('/auto-organize', '?subject=uploads')).toBe('/library');
		expect(navAreaPath('/auto-organize', new URLSearchParams('subject=models'))).toBe('/models');
	});

	it('defaults to History and leaves other paths alone', () => {
		expect(navAreaPath('/auto-organize', '')).toBe('/history');
		expect(navAreaPath('/auto-organize', '?subject=nope')).toBe('/history');
		expect(navAreaPath('/models', '?subject=uploads')).toBe('/models');
	});
});

describe('pathMatchesNavItem', () => {
	it('highlights the matching rail item only', () => {
		expect(pathMatchesNavItem('/library', '/auto-organize', '?subject=uploads')).toBe(true);
		expect(pathMatchesNavItem('/history', '/auto-organize', '?subject=uploads')).toBe(false);
		expect(pathMatchesNavItem('/history', '/history/abc')).toBe(true);
	});

	it('recognises the page', () => {
		expect(isOrganizePath('/auto-organize')).toBe(true);
		expect(isOrganizePath('/auto-organized')).toBe(false);
	});
});

describe('notificationLinkFor', () => {
	it('links a paused rule to its page', () => {
		expect(notificationLinkFor('organize.rule_paused', { rule_id: 'r 1' })).toBe('/auto-organize?rule=r%201');
		expect(notificationLinkFor('organize.rule_paused', null)).toBe('/auto-organize');
	});

	it('links a finished backfill to activity', () => {
		expect(notificationLinkFor('organize.job_finished', {})).toBe('/auto-organize?view=activity');
	});

	it('ignores other types', () => {
		expect(notificationLinkFor('download.done', {})).toBeNull();
	});
});
