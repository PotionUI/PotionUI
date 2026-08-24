/**
 * Moving a `<video>` to a moment and waiting for it to actually be there.
 *
 * The element is the one already on screen, seeked in place - never a copy of
 * the clip in memory. Scrubbing a video by downloading it into a blob first
 * defeats range requests, so a 400 MB clip would have to arrive in full before
 * the first seek; the element streams the byte range it needs and nothing else.
 */

const SEEK_TIMEOUT_MS = 10000;

/** Resolve once the element is actually showing `time`. */
export function seekVideo(video: HTMLVideoElement, time: number): Promise<void> {
	return new Promise((resolve, reject) => {
		if (Math.abs(video.currentTime - time) < 0.001 && video.readyState >= 2) {
			resolve();
			return;
		}

		let timer: ReturnType<typeof setTimeout>;

		const done = () => {
			clearTimeout(timer);
			video.removeEventListener('seeked', done);
			video.removeEventListener('error', failed);
			resolve();
		};
		const failed = () => {
			clearTimeout(timer);
			video.removeEventListener('seeked', done);
			video.removeEventListener('error', failed);
			reject(new Error('Could not seek the video'));
		};

		// A seek that never lands - a broken range response, a codec the
		// decoder gave up on - would otherwise leave the editor spinning
		// forever with no way back.
		timer = setTimeout(failed, SEEK_TIMEOUT_MS);
		video.addEventListener('seeked', done);
		video.addEventListener('error', failed);
		video.currentTime = time;
	});
}

