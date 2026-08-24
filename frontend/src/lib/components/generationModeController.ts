import { writable, type Readable } from 'svelte/store';
import { createContinuousGenerationScheduler } from './continuousGeneration';

export type GenerationMode = 'once' | 'forever';

export interface GenerationModeController {
	mode: Readable<GenerationMode>;
	stopAfterCurrentRequested: Readable<boolean>;
	setMode(mode: GenerationMode): void;
	handleGenerationStart(): void;
	handleGenerationComplete(): void;
	requestStopAfterCurrent(): void;
	cancel(): void;
	dispose(): void;
}

export function createGenerationModeController(
	onContinue: () => void,
	delayMs = 1_000
): GenerationModeController {
	const scheduler = createContinuousGenerationScheduler(onContinue, delayMs);
	const mode = writable<GenerationMode>('once');
	const stopAfterCurrentRequested = writable(false);

	function setMode(next: GenerationMode) {
		mode.set(next);
		if (next === 'forever') {
			scheduler.arm();
		} else {
			scheduler.disarm();
		}
	}

	function handleGenerationStart() {
		stopAfterCurrentRequested.set(false);
	}

	function handleGenerationComplete() {
		scheduler.schedule();
		stopAfterCurrentRequested.set(false);
	}

	function requestStopAfterCurrent() {
		stopAfterCurrentRequested.set(true);
		setMode('once');
	}

	function cancel() {
		setMode('once');
	}

	function dispose() {
		scheduler.dispose();
	}

	return {
		mode,
		stopAfterCurrentRequested,
		setMode,
		handleGenerationStart,
		handleGenerationComplete,
		requestStopAfterCurrent,
		cancel,
		dispose
	};
}
