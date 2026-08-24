/**
 * The metadata a loaded item shows: chips under the single preview, one line
 * under each multi tile.
 *
 * Everything here is best-effort. An older upload, or a generation from before
 * probing existed, has no dimensions and no duration — a missing field renders
 * nothing rather than a placeholder, so the line never claims a value it does
 * not have.
 */

import { formatBytes, formatSeconds } from '$lib/utils/format';
import type { MediaKind } from './mediaLoaderConfig';

export interface MediaItemMetadata {
	width?: number | null;
	height?: number | null;
	duration_seconds?: number | null;
	fps?: number | null;
	size?: number | null;
}

export type MetaTone = 'default' | 'signal' | 'success' | 'subtle';

export interface MetaChip {
	key: string;
	text: string;
	title: string;
	icon: MetaChipIcon;
	tone: MetaTone;
}

export type MetaChipIcon = 'ruler' | 'clock' | 'film' | 'weight' | 'doc';

export function formatDimensions(meta: MediaItemMetadata | null | undefined): string | null {
	if (!meta?.width || !meta?.height) return null;
	return `${meta.width}×${meta.height}`;
}

/** Uppercase container from a filename — "PNG", "MP4". */
export function formatContainer(fileName: string | null | undefined): string | null {
	if (!fileName) return null;
	const ext = fileName.split('?')[0].split('.').pop();
	if (!ext || ext === fileName) return null;
	return ext.toUpperCase();
}

/**
 * Chips for the single-item preview. `cropped` marks a resolution the user
 * changed themselves, which is the one number they will look for after an
 * edit — it reads as success rather than as neutral metadata.
 */
export function metaChips(
	meta: MediaItemMetadata | null | undefined,
	kind: MediaKind,
	fileName: string | null | undefined,
	options: { edited?: boolean } = {}
): MetaChip[] {
	const chips: MetaChip[] = [];
	const dimensions = formatDimensions(meta);

	if (dimensions) {
		chips.push({
			key: 'dimensions',
			text: dimensions,
			title: 'Resolution',
			icon: 'ruler',
			tone: options.edited ? 'success' : 'default'
		});
	}

	if (kind !== 'image' && typeof meta?.duration_seconds === 'number' && meta.duration_seconds > 0) {
		chips.push({
			key: 'duration',
			text: formatSeconds(meta.duration_seconds),
			title: 'Duration',
			icon: 'clock',
			tone: 'signal'
		});
	}

	if (kind === 'video' && typeof meta?.fps === 'number' && meta.fps > 0) {
		chips.push({
			key: 'fps',
			text: `${Math.round(meta.fps)} fps`,
			title: 'Frame rate',
			icon: 'film',
			tone: 'default'
		});
	}

	const container = formatContainer(fileName);
	if (container && kind === 'image') {
		chips.push({ key: 'format', text: container, title: 'Format', icon: 'doc', tone: 'default' });
	}

	if (typeof meta?.size === 'number' && meta.size > 0) {
		chips.push({
			key: 'size',
			text: formatBytes(meta.size),
			title: 'File size',
			icon: 'weight',
			tone: 'default'
		});
	}

	return chips;
}

/** One dot-separated line, for a multi tile that has no room for chips. */
export function metaLine(meta: MediaItemMetadata | null | undefined, kind: MediaKind | null): string | null {
	const parts: string[] = [];
	const dimensions = formatDimensions(meta);
	if (dimensions) parts.push(dimensions);
	if (kind !== 'image' && typeof meta?.duration_seconds === 'number' && meta.duration_seconds > 0) {
		parts.push(formatSeconds(meta.duration_seconds));
	}
	if (kind === 'video' && typeof meta?.fps === 'number' && meta.fps > 0) parts.push(`${Math.round(meta.fps)}fps`);
	if (typeof meta?.size === 'number' && meta.size > 0) parts.push(formatBytes(meta.size));
	return parts.length > 0 ? parts.join(' · ') : null;
}

/** The duration badge on a timed tile, or null for a still. */
export function durationBadge(meta: MediaItemMetadata | null | undefined, kind: MediaKind | null): string | null {
	if (kind === 'image' || !kind) return null;
	if (typeof meta?.duration_seconds !== 'number' || meta.duration_seconds <= 0) return null;
	return formatSeconds(meta.duration_seconds);
}
