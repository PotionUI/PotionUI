/**
 * Turns a MediaLoaderField pick into a validated avatar `File`, split out so
 * it's testable without mounting the settings page.
 */

import { validateAvatarFile } from '$lib/utils/avatarValidation';

export interface AvatarMediaResult {
	file: File | null;
	error: string | null;
}

/**
 * MediaLoaderField's onChange value is served content (an upload, a library
 * pick, a generation-history pick) - never a blob the caller already holds -
 * so turning it into an avatar upload means re-fetching the served URL and
 * re-validating it against the avatar-specific constraints (size, MIME set)
 * before it's handed to `api.uploadAvatar`.
 */
export async function resolveAvatarFileFromMediaItem(
	item: { url: string; name?: string },
	fetchImpl: typeof fetch = fetch
): Promise<AvatarMediaResult> {
	const response = await fetchImpl(item.url);
	if (!response.ok) {
		return { file: null, error: 'Failed to load the selected image.' };
	}

	const blob = await response.blob();
	const file = new File([blob], item.name || 'avatar.png', { type: blob.type || 'image/png' });

	const validationError = validateAvatarFile(file);
	if (validationError) {
		return { file: null, error: validationError };
	}

	return { file, error: null };
}
