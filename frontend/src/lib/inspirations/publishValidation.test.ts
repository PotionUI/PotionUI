import { describe, it, expect } from 'vitest';
import { validatePublishForm } from './publishValidation';

describe('validatePublishForm', () => {
	it('accepts a title with a single available file', () => {
		const result = validatePublishForm({
			title: 'My generation',
			availableFilenames: ['out.png'],
			selectedFilenames: []
		});
		expect(result).toEqual({ valid: true });
	});

	it('rejects a blank title', () => {
		const result = validatePublishForm({
			title: '   ',
			availableFilenames: ['out.png'],
			selectedFilenames: []
		});
		expect(result.valid).toBe(false);
		expect(result.error).toMatch(/title/i);
	});

	it('rejects an overlong title', () => {
		const result = validatePublishForm({
			title: 'a'.repeat(201),
			availableFilenames: ['out.png'],
			selectedFilenames: []
		});
		expect(result.valid).toBe(false);
		expect(result.error).toMatch(/200/);
	});

	it('requires at least one selected file when there are multiple outputs', () => {
		const result = validatePublishForm({
			title: 'My generation',
			availableFilenames: ['a.png', 'b.png'],
			selectedFilenames: []
		});
		expect(result.valid).toBe(false);
		expect(result.error).toMatch(/select/i);
	});

	it('accepts a multi-output generation once a file is selected', () => {
		const result = validatePublishForm({
			title: 'My generation',
			availableFilenames: ['a.png', 'b.png'],
			selectedFilenames: ['a.png']
		});
		expect(result).toEqual({ valid: true });
	});
});
