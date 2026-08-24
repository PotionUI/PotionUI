import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// The suite runs in the 'node' environment (no DOM), so we stub globalThis.window
// directly. These mocks are just enough to assert the chime wires oscillators to
// destination without throwing — real audio is not exercised.
function makeParam() {
	return { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() };
}

function installWindow(withAudio: boolean) {
	const oscillators: Array<{ start: ReturnType<typeof vi.fn> }> = [];
	class MockAudioContext {
		currentTime = 0;
		state: 'running' | 'suspended' = 'running';
		destination = {};
		resume = vi.fn().mockResolvedValue(undefined);
		createOscillator() {
			const osc = {
				type: 'sine',
				frequency: makeParam(),
				connect: vi.fn().mockReturnValue({ connect: vi.fn() }),
				start: vi.fn(),
				stop: vi.fn()
			};
			oscillators.push(osc);
			return osc;
		}
		createGain() {
			return { gain: makeParam(), connect: vi.fn().mockReturnValue({ connect: vi.fn() }) };
		}
	}
	(globalThis as { window?: unknown }).window = withAudio ? { AudioContext: MockAudioContext } : {};
	return oscillators;
}

describe('playNotificationChime', () => {
	// Fresh module each test so the internal AudioContext singleton is reset.
	beforeEach(() => {
		vi.resetModules();
	});

	afterEach(() => {
		vi.restoreAllMocks();
		delete (globalThis as { window?: unknown }).window;
	});

	it('creates and starts oscillators without throwing', async () => {
		const oscillators = installWindow(true);
		const { playNotificationChime } = await import('./notificationChime');
		expect(() => playNotificationChime()).not.toThrow();
		expect(oscillators.length).toBeGreaterThan(0);
		expect(oscillators[0].start).toHaveBeenCalled();
	});

	it('silently no-ops when Web Audio is unavailable', async () => {
		installWindow(false);
		const { playNotificationChime } = await import('./notificationChime');
		expect(() => playNotificationChime()).not.toThrow();
	});

	it('silently no-ops when window is undefined (SSR)', async () => {
		delete (globalThis as { window?: unknown }).window;
		const { playNotificationChime } = await import('./notificationChime');
		expect(() => playNotificationChime()).not.toThrow();
	});
});
