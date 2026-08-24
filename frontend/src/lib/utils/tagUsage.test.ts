import { describe, it, expect } from 'vitest';
import { formatTagUsageError } from './tagUsage';

describe('formatTagUsageError', () => {
	it('lists the blocking presets by name and key', () => {
		const message = formatTagUsageError('anime', [
			{ preset_id: 'p1', preset_name: 'Krea 2', key: 'checkpoint_tags' },
			{ preset_id: 'p2', preset_name: 'SDXL Realistic', key: 'style_tags' }
		]);
		expect(message).toBe(
			'"anime" is used by Krea 2 (checkpoint_tags), SDXL Realistic (style_tags) and can\'t be deleted.'
		);
	});

	it('falls back to a generic message when used_by is empty', () => {
		expect(formatTagUsageError('anime', [])).toBe("\"anime\" is still in use and can't be deleted.");
		expect(formatTagUsageError('anime', null)).toBe("\"anime\" is still in use and can't be deleted.");
	});
});
