/**
 * Presentation rules for an inspiration card. Pure and side-effect free so it
 * stays trivially unit-testable - see inspirationCardMeta.test.ts.
 */

import type { InspirationDto, InspirationMedia } from '$lib/services/api/inspirations';
import { clampAspect } from '$lib/utils/justifiedLayout';

export interface InspirationAuthorLike {
	id: string;
	username: string;
	avatar_url?: string | null;
}

/** The card's primary media: the first entry, or null for a media-less row. */
export function inspirationPrimaryMedia(dto: Pick<InspirationDto, 'media'>): InspirationMedia | null {
	return dto.media?.[0] ?? null;
}

export function inspirationIsVideo(media: InspirationMedia | null): boolean {
	return (media?.type ?? '').toLowerCase() === 'video';
}

/**
 * Native aspect ratio (width / height) of the card's primary media, clamped
 * the same way the history justified grid clamps generation tiles. Falls
 * back to square for media with no reported dimensions.
 */
export function inspirationAspect(media: InspirationMedia | null): number {
	if (media?.width && media?.height) return clampAspect(media.width / media.height);
	return 1;
}

/** Falls back to the account name when no display title was given. */
export function inspirationDisplayTitle(dto: Pick<InspirationDto, 'title' | 'author'>): string {
	const title = (dto.title ?? '').trim();
	return title || `${dto.author.username}'s generation`;
}

/** The author's initial for an avatar-less chip - never empty, even for a blank username. */
export function inspirationAuthorInitial(author: InspirationAuthorLike): string {
	const trimmed = (author.username ?? '').trim();
	return trimmed ? trimmed.charAt(0).toUpperCase() : '?';
}

/**
 * Whether `viewer` may delete `dto` - the author, or an admin. Mirrors the
 * server-side rule stated in the API contract; the server is still the
 * authority, this only decides whether to render the control.
 */
export function canModerateInspiration(
	dto: Pick<InspirationDto, 'author'>,
	viewer: { id: string; account_type: string } | null | undefined
): boolean {
	if (!viewer) return false;
	return viewer.account_type === 'ADMIN' || viewer.id === dto.author.id;
}
