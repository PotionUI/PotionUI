/**
 * An image element that has finished decoding, or a rejection.
 *
 * The editors read a source's REAL pixel size from the decoded element rather
 * than trusting the metadata a row remembers: a crop rectangle checked against
 * stale dimensions is a rectangle the server refuses.
 */

export function loadImage(url: string): Promise<HTMLImageElement> {
	return new Promise((resolve, reject) => {
		const image = new Image();
		// The uploads route is same-origin through the dev proxy, but a
		// generated file may be served from an absolute base - without this a
		// canvas that reads the pixels back (the mask editor) is tainted.
		image.crossOrigin = 'anonymous';
		image.onload = () => resolve(image);
		image.onerror = () => reject(new Error('Could not load the image'));
		image.src = url;
	});
}
