import { describe, it, expect } from 'vitest';
import {
	inspirationPrimaryMedia,
	inspirationIsVideo,
	inspirationDisplayTitle,
	inspirationAuthorInitial,
	canModerateInspiration
} from './inspirationCardMeta';

const author = { id: 'user-1', username: 'ada', avatar_url: null };

describe('inspirationPrimaryMedia', () => {
	it('returns the first media entry', () => {
		const media = [
			{ url: '/a.png', type: 'image' },
			{ url: '/b.png', type: 'image' }
		];
		expect(inspirationPrimaryMedia({ media })).toBe(media[0]);
	});

	it('returns null when there is no media', () => {
		expect(inspirationPrimaryMedia({ media: [] })).toBeNull();
	});
});

describe('inspirationIsVideo', () => {
	it('is case-insensitive', () => {
		expect(inspirationIsVideo({ url: '/a.mp4', type: 'Video' })).toBe(true);
		expect(inspirationIsVideo({ url: '/a.png', type: 'image' })).toBe(false);
	});

	it('treats a missing media entry as not a video', () => {
		expect(inspirationIsVideo(null)).toBe(false);
	});
});

describe('inspirationDisplayTitle', () => {
	it('uses the given title', () => {
		expect(inspirationDisplayTitle({ title: 'Sunset over water', author })).toBe(
			'Sunset over water'
		);
	});

	it('falls back to the author when the title is blank', () => {
		expect(inspirationDisplayTitle({ title: '   ', author })).toBe("ada's generation");
	});
});

describe('inspirationAuthorInitial', () => {
	it('uppercases the first letter of the username', () => {
		expect(inspirationAuthorInitial({ id: '1', username: 'blake' })).toBe('B');
	});

	it('falls back to a placeholder for a blank username', () => {
		expect(inspirationAuthorInitial({ id: '1', username: '   ' })).toBe('?');
	});
});

describe('canModerateInspiration', () => {
	it('allows the author', () => {
		expect(
			canModerateInspiration({ author }, { id: 'user-1', account_type: 'USER' })
		).toBe(true);
	});

	it('allows an admin who is not the author', () => {
		expect(
			canModerateInspiration({ author }, { id: 'user-2', account_type: 'ADMIN' })
		).toBe(true);
	});

	it('denies a different non-admin user', () => {
		expect(
			canModerateInspiration({ author }, { id: 'user-2', account_type: 'USER' })
		).toBe(false);
	});

	it('denies a signed-out viewer', () => {
		expect(canModerateInspiration({ author }, null)).toBe(false);
	});
});
