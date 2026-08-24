/**
 * Measures a picked file in the browser, before it is uploaded.
 *
 * Resolution and duration limits can only be enforced up front if the field
 * knows the numbers up front, and a `File` carries neither. Decoding just the
 * header via an off-document element is enough for both.
 *
 * Every failure path resolves empty rather than rejecting: a codec the browser
 * cannot decode is not a reason to block an upload the backend may well
 * accept, it only means this particular limit goes unchecked here.
 */

import type { MediaKind } from './mediaLoaderConfig';

export interface ProbedMedia {
	width?: number;
	height?: number;
	durationSeconds?: number;
}

const PROBE_TIMEOUT_MS = 4000;

export function probeMediaFile(file: File, kind: MediaKind | null): Promise<ProbedMedia> {
	if (!kind || typeof window === 'undefined' || typeof URL.createObjectURL !== 'function') {
		return Promise.resolve({});
	}

	return new Promise<ProbedMedia>((resolve) => {
		const url = URL.createObjectURL(file);
		let settled = false;

		const finish = (result: ProbedMedia) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			URL.revokeObjectURL(url);
			resolve(result);
		};

		const timer = setTimeout(() => finish({}), PROBE_TIMEOUT_MS);

		if (kind === 'image') {
			const image = new Image();
			image.onload = () => finish({ width: image.naturalWidth, height: image.naturalHeight });
			image.onerror = () => finish({});
			image.src = url;
			return;
		}

		const element = document.createElement(kind === 'video' ? 'video' : 'audio');
		element.preload = 'metadata';
		element.onloadedmetadata = () => {
			const media = element as HTMLVideoElement;
			finish({
				width: media.videoWidth || undefined,
				height: media.videoHeight || undefined,
				durationSeconds: Number.isFinite(media.duration) ? media.duration : undefined
			});
		};
		element.onerror = () => finish({});
		element.src = url;
	});
}
