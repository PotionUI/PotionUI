import { describe, it, expect } from 'vitest';
import { formatVramBadge, formatRamBadge, vramShortfall } from './presetHardware';

describe('formatVramBadge', () => {
	it('is null when requires is absent', () => {
		expect(formatVramBadge(undefined)).toBeNull();
		expect(formatVramBadge(null)).toBeNull();
	});

	it('is null when requires has neither vram key', () => {
		expect(formatVramBadge({ min_ram_gb: 16 })).toBeNull();
	});

	it('prefers min_vram_gb over recommended_vram_gb', () => {
		expect(formatVramBadge({ min_vram_gb: 12, recommended_vram_gb: 16 })).toBe('12 GB VRAM');
	});

	it('falls back to recommended_vram_gb when min is unset', () => {
		expect(formatVramBadge({ recommended_vram_gb: 16 })).toBe('16+ GB VRAM recommended');
	});

	it('does not append a trailing .0 for whole numbers', () => {
		expect(formatVramBadge({ min_vram_gb: 8 })).toBe('8 GB VRAM');
	});

	it('keeps a fractional GB value', () => {
		expect(formatVramBadge({ min_vram_gb: 8.5 })).toBe('8.5 GB VRAM');
	});
});

describe('formatRamBadge', () => {
	it('is null when min_ram_gb is unset', () => {
		expect(formatRamBadge({ min_vram_gb: 8 })).toBeNull();
		expect(formatRamBadge(undefined)).toBeNull();
	});

	it('formats the RAM figure', () => {
		expect(formatRamBadge({ min_ram_gb: 16 })).toBe('16 GB RAM');
	});
});

describe('vramShortfall', () => {
	it('is null when requires has no min_vram_gb', () => {
		expect(vramShortfall({ recommended_vram_gb: 16 }, 8)).toBeNull();
	});

	it('is null when detected VRAM is unknown', () => {
		expect(vramShortfall({ min_vram_gb: 12 }, null)).toBeNull();
		expect(vramShortfall({ min_vram_gb: 12 }, undefined)).toBeNull();
	});

	it('is null when the detected VRAM meets the minimum', () => {
		expect(vramShortfall({ min_vram_gb: 12 }, 12)).toBeNull();
		expect(vramShortfall({ min_vram_gb: 12 }, 16)).toBeNull();
	});

	it('warns when detected VRAM is below the minimum', () => {
		expect(vramShortfall({ min_vram_gb: 12 }, 8)).toBe('needs 12 GB — this machine has 8 GB');
	});
});
