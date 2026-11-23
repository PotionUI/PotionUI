const DATA_URL = /^data:([^;,]*)(?:;[^,]*)?;base64,/;
const REVOKE_DELAY_MS = 1500;

const liveByTab = new Map<string, string>();

function decodeBase64(base64: string): Uint8Array {
	const binary = atob(base64);
	const bytes = new Uint8Array(binary.length);
	for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
	return bytes;
}

function revokeLater(url: string): void {
	setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
}

export function previewFrameUrl(tabId: string, imageData: string): string {
	const match = DATA_URL.exec(imageData);
	const dataUrl = match ? imageData : `data:image/png;base64,${imageData}`;
	if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function' || typeof Blob === 'undefined') return dataUrl;
	const mime = match?.[1] || 'image/png';
	const base64 = match ? imageData.slice(match[0].length) : imageData;
	let url: string;
	try {
		url = URL.createObjectURL(new Blob([decodeBase64(base64) as BlobPart], { type: mime }));
	} catch {
		return dataUrl;
	}
	const previous = liveByTab.get(tabId);
	if (previous) revokeLater(previous);
	liveByTab.set(tabId, url);
	return url;
}

export function withoutPreviewBlob<T extends { current_image?: unknown } | null | undefined>(current: T): T {
	const image = current?.current_image;
	if (typeof image !== 'string' || !image.startsWith('blob:')) return current;
	return { ...current, current_image: null };
}

export function releasePreviewFrame(tabId: string): void {
	const previous = liveByTab.get(tabId);
	if (!previous) return;
	liveByTab.delete(tabId);
	revokeLater(previous);
}
