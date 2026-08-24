import { describe, it, expect } from 'vitest';
import { deriveMarkState, deriveModeChromeGlyph, formatDurationSeconds, formatDurationMs } from './barState';

describe('deriveMarkState', () => {
	it('is running whenever a generation is in flight, even if canGenerate is false', () => {
		expect(deriveMarkState({ isGenerating: true, canGenerate: false, mode: 'once' })).toBe('running');
	});

	it('is disabled when idle and not ready to generate', () => {
		expect(deriveMarkState({ isGenerating: false, canGenerate: false, mode: 'once' })).toBe('disabled');
	});

	it('is ready when idle, ready, and mode is once', () => {
		expect(deriveMarkState({ isGenerating: false, canGenerate: true, mode: 'once' })).toBe('ready');
	});

	it('is continuous-armed when idle, ready, and mode is forever', () => {
		expect(deriveMarkState({ isGenerating: false, canGenerate: true, mode: 'forever' })).toBe('continuous-armed');
	});
});

describe('deriveModeChromeGlyph', () => {
	it('never regresses from stopping back to pause', () => {
		expect(
			deriveModeChromeGlyph({ isGenerating: true, mode: 'forever', stopAfterCurrentRequested: true })
		).toBe('stopping');
	});

	it('is pause during a continuous run with no stop requested', () => {
		expect(
			deriveModeChromeGlyph({ isGenerating: true, mode: 'forever', stopAfterCurrentRequested: false })
		).toBe('pause');
	});

	it('is idle while running in once mode (nothing to stop-after)', () => {
		expect(
			deriveModeChromeGlyph({ isGenerating: true, mode: 'once', stopAfterCurrentRequested: false })
		).toBe('idle');
	});

	it('is idle at rest', () => {
		expect(
			deriveModeChromeGlyph({ isGenerating: false, mode: 'once', stopAfterCurrentRequested: false })
		).toBe('idle');
	});
});

describe('formatDurationSeconds', () => {
	it('renders "none" for a value that does not exist yet', () => {
		expect(formatDurationSeconds(null)).toBe('none');
	});

	it('renders sub-minute durations with one decimal', () => {
		expect(formatDurationSeconds(12.4)).toBe('12.4s');
	});

	it('renders minute-scale durations as Nm Ns', () => {
		expect(formatDurationSeconds(125)).toBe('2m 5s');
	});

	it('renders hour-scale durations as Nh Nm', () => {
		expect(formatDurationSeconds(3725)).toBe('1h 2m');
	});
});

describe('formatDurationMs', () => {
	it('converts milliseconds to the same rendering as seconds', () => {
		expect(formatDurationMs(12400)).toBe('12.4s');
	});

	it('renders "none" for null', () => {
		expect(formatDurationMs(null)).toBe('none');
	});
});
