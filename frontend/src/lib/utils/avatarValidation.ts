// Client-side mirror of the backend's avatar upload constraints (png/jpg/
// jpeg/webp/gif, up to 5 MB). The server remains the source of truth; this
// only avoids a wasted round trip for an obviously-invalid file.

const ALLOWED_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);

export const AVATAR_MAX_BYTES = 5 * 1024 * 1024;

export function validateAvatarFile(file: { type: string; size: number }): string | null {
	if (!ALLOWED_TYPES.has(file.type)) {
		return 'Please choose a PNG, JPG, WEBP, or GIF image.';
	}
	if (file.size > AVATAR_MAX_BYTES) {
		return 'Image must be 5 MB or smaller.';
	}
	return null;
}
