/**
 * Pure, framework-free helpers for turning decoded PCM samples into a
 * waveform's bar heights. Kept free of any Web Audio / DOM API so they can
 * be unit-tested with plain arrays (jsdom has no real Web Audio engine).
 *
 * Two-stage design:
 *  - `computePeaks` reduces raw decoded channel data (millions of samples)
 *    down to a fixed-resolution peak array, once per decode.
 *  - `resamplePeaks` re-buckets that fixed array to whatever bar count the
 *    canvas currently needs (canvas width changes on resize far more often
 *    than the underlying audio does), without touching the raw samples again.
 */

/** Peak resolution the decoded audio is reduced to and cached at. */
export const PEAK_RESOLUTION = 600;

/** OfflineAudioContext sample rate used for the peak decode. Low on purpose:
 *  peaks only need enough resolution to look right at a few hundred bars,
 *  not audio fidelity. Decoding at 8kHz instead of a source's native 44.1kHz
 *  keeps the transient Float32 buffer (and CPU work) roughly 5x smaller. */
export const PEAK_DECODE_SAMPLE_RATE = 8000;

/** Skip the decode above this response size and fall back to a flat
 *  waveform instead. 25MB at 8kHz mono decode target is already a multi-
 *  second main-thread hit; above this we'd rather show a placeholder than
 *  risk freezing the tab on an unexpectedly huge file. */
export const MAX_DECODE_BYTES = 25 * 1024 * 1024;

/** A single amplitude value, always within [0, 1]. */
export type Peaks = number[];

/**
 * Downmix N channels of raw samples (e.g. from AudioBuffer.getChannelData)
 * to `barCount` peak values in [0, 1], each the max absolute sample across
 * all channels within that bar's slice of the timeline.
 *
 * Multiple channels are combined by taking the max across channels at each
 * sample index (a "loudest channel wins" downmix) rather than averaging, so
 * a quiet mono narration track and a hot stereo mix both produce a waveform
 * that reflects actual peaks rather than being flattened by averaging.
 */
export function computePeaks(channels: ArrayLike<number>[], barCount: number): Peaks {
	if (barCount <= 0) return [];

	const nonEmptyChannels = channels.filter((c) => c.length > 0);
	if (nonEmptyChannels.length === 0) {
		return createFlatWaveform(barCount);
	}

	const totalSamples = Math.max(...nonEmptyChannels.map((c) => c.length));
	const peaks: Peaks = new Array(barCount);

	for (let bar = 0; bar < barCount; bar++) {
		const start = Math.floor((bar / barCount) * totalSamples);
		const end = Math.max(start + 1, Math.floor(((bar + 1) / barCount) * totalSamples));

		let max = 0;
		for (const channel of nonEmptyChannels) {
			const channelEnd = Math.min(end, channel.length);
			for (let i = start; i < channelEnd; i++) {
				const abs = Math.abs(channel[i]);
				if (abs > max) max = abs;
			}
		}

		peaks[bar] = clampAmplitude(max);
	}

	return peaks;
}

/**
 * Re-bucket an existing peak array to a different bar count (block-max
 * when downsampling, nearest-neighbor when upsampling). Used when the
 * canvas resizes so a resize never needs to re-fetch or re-decode audio.
 */
export function resamplePeaks(peaks: Peaks, barCount: number): Peaks {
	if (barCount <= 0) return [];
	if (peaks.length === 0) return createFlatWaveform(barCount);
	if (peaks.length === barCount) return peaks.slice();

	const resampled: Peaks = new Array(barCount);

	for (let bar = 0; bar < barCount; bar++) {
		const start = Math.floor((bar / barCount) * peaks.length);
		const end = Math.max(start + 1, Math.floor(((bar + 1) / barCount) * peaks.length));

		let max = 0;
		for (let i = start; i < end && i < peaks.length; i++) {
			if (peaks[i] > max) max = peaks[i];
		}

		resampled[bar] = max;
	}

	return resampled;
}

/** Placeholder bars shown while nothing has decoded yet, or when decode
 *  failed — playback must never depend on a real waveform being available. */
export function createFlatWaveform(barCount: number, amplitude = 0.15): Peaks {
	if (barCount <= 0) return [];
	return new Array(barCount).fill(clampAmplitude(amplitude));
}

function clampAmplitude(value: number): number {
	if (Number.isNaN(value)) return 0;
	if (value < 0) return 0;
	if (value > 1) return 1;
	return value;
}
