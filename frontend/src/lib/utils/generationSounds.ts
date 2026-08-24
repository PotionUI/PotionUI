// Synthesized generation-outcome cues — no audio assets, so no licensing
// surface. One AudioContext is shared for the page's lifetime; browsers start
// it suspended until a user gesture, so `unlockGenerationSoundContext` is
// called from the generate-click path (the gesture the user always performs
// before a sound would need to play).
let audioContext: AudioContext | null = null;

function getAudioContext(): AudioContext | null {
	if (typeof window === 'undefined') return null;
	if (!audioContext) {
		const Ctor = window.AudioContext ?? (window as any).webkitAudioContext;
		if (!Ctor) return null;
		audioContext = new Ctor();
	}
	return audioContext;
}

export function unlockGenerationSoundContext(): void {
	const ctx = getAudioContext();
	if (ctx?.state === 'suspended') {
		ctx.resume().catch(() => {});
	}
}

function playTone(ctx: AudioContext, frequency: number, startTime: number, duration: number, peakGain: number): void {
	const oscillator = ctx.createOscillator();
	const gain = ctx.createGain();
	oscillator.type = 'sine';
	oscillator.frequency.setValueAtTime(frequency, startTime);
	gain.gain.setValueAtTime(0, startTime);
	gain.gain.linearRampToValueAtTime(peakGain, startTime + 0.015);
	gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
	oscillator.connect(gain);
	gain.connect(ctx.destination);
	oscillator.start(startTime);
	oscillator.stop(startTime + duration + 0.02);
}

/** Soft two-note ascending chime, ~260ms total. */
export function playGenerationCompleteSound(): void {
	const ctx = getAudioContext();
	if (!ctx) return;
	const now = ctx.currentTime;
	playTone(ctx, 660, now, 0.14, 0.1);
	playTone(ctx, 880, now + 0.1, 0.16, 0.1);
}

/** Low, brief descending buzz, ~320ms. */
export function playGenerationErrorSound(): void {
	const ctx = getAudioContext();
	if (!ctx) return;
	const now = ctx.currentTime;
	const oscillator = ctx.createOscillator();
	const gain = ctx.createGain();
	oscillator.type = 'sawtooth';
	oscillator.frequency.setValueAtTime(220, now);
	oscillator.frequency.exponentialRampToValueAtTime(110, now + 0.3);
	gain.gain.setValueAtTime(0, now);
	gain.gain.linearRampToValueAtTime(0.09, now + 0.02);
	gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.32);
	oscillator.connect(gain);
	gain.connect(ctx.destination);
	oscillator.start(now);
	oscillator.stop(now + 0.35);
}
