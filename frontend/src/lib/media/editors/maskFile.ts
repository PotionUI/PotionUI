/**
 * The painted mask, on its way to being a stored file.
 *
 * A canvas hands back a data URL; the upload route takes a `File`. The
 * conversion is here rather than inline because the base64 payload starts after
 * the FIRST comma and the header before it can itself contain none - splitting
 * on every comma, or assuming `[1]` without checking the header, produces a
 * file the server accepts and cannot decode.
 */

/** The bytes a `data:` URL carries, or a refusal naming what was wrong. */
export function decodeDataUrl(dataUrl: string): { mimeType: string; bytes: Uint8Array<ArrayBuffer> } {
	const comma = typeof dataUrl === 'string' ? dataUrl.indexOf(',') : -1;
	if (comma < 0 || !dataUrl.startsWith('data:')) {
		throw new Error('The mask could not be read');
	}

	const header = dataUrl.slice(5, comma);
	if (!header.endsWith(';base64')) {
		throw new Error('The mask could not be read');
	}

	const mimeType = header.slice(0, -';base64'.length) || 'image/png';
	const binary = atob(dataUrl.slice(comma + 1));
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i += 1) {
		bytes[i] = binary.charCodeAt(i);
	}
	return { mimeType, bytes };
}

export function dataUrlToFile(dataUrl: string, fileName: string): File {
	const { mimeType, bytes } = decodeDataUrl(dataUrl);
	return new File([bytes], fileName, { type: mimeType });
}
