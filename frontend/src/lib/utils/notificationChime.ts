/**
 * A short, subtle two-tone notification chime synthesized with the Web Audio
 * API — no binary asset. The AudioContext is created lazily and resumed on
 * demand; browser autoplay policy may reject resume() before a user gesture,
 * so every failure path is swallowed silently (a missed chime is never worth
 * surfacing an error).
 */

let ctx: AudioContext | null = null;

function getContext(): AudioContext | null {
	if (typeof window === 'undefined') return null;
	try {
		if (!ctx) {
			const AC: typeof AudioContext | undefined =
				window.AudioContext ||
				(window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
			if (!AC) return null;
			ctx = new AC();
		}
		return ctx;
	} catch {
		return null;
	}
}

/** Play the notification chime. Safe to call anytime; silently no-ops on failure. */
export function playNotificationChime(): void {
	try {
		const audio = getContext();
		if (!audio) return;

		if (audio.state === 'suspended') {
			// May reject before a user gesture — ignore; the tone below still
			// schedules and simply won't be audible until the context is running.
			void audio.resume().catch(() => {});
		}

		const now = audio.currentTime;
		const peak = 0.2;
		// Two ascending tones (a perfect fifth) for a gentle "ding-ding".
		const tones: Array<{ freq: number; start: number; length: number }> = [
			{ freq: 660, start: 0, length: 0.09 },
			{ freq: 880, start: 0.07, length: 0.11 }
		];

		for (const tone of tones) {
			const osc = audio.createOscillator();
			const gain = audio.createGain();
			osc.type = 'sine';
			osc.frequency.setValueAtTime(tone.freq, now + tone.start);

			// Quick attack, exponential decay envelope.
			gain.gain.setValueAtTime(0.0001, now + tone.start);
			gain.gain.exponentialRampToValueAtTime(peak, now + tone.start + 0.01);
			gain.gain.exponentialRampToValueAtTime(0.0001, now + tone.start + tone.length);

			osc.connect(gain).connect(audio.destination);
			osc.start(now + tone.start);
			osc.stop(now + tone.start + tone.length + 0.02);
		}
	} catch {
		// Never let a chime failure bubble up.
	}
}
