import { describe, it, expect } from 'vitest';
import { stepKindDisplay } from './recipeStepKindDisplay';

describe('stepKindDisplay', () => {
	it('resolves an icon and a plain-language description for known kinds', () => {
		expect(stepKindDisplay('artifacts.fetch')).toEqual({
			icon: 'download',
			description: 'Downloads the models this recipe needs.'
		});
		expect(stepKindDisplay('preset.ensure').icon).toBe('layers');
		expect(stepKindDisplay('generation.smoke').icon).toBe('wand');
	});

	it('falls back to a generic display for an unrecognized kind', () => {
		expect(stepKindDisplay('some.plugin.kind')).toEqual({
			icon: 'settings',
			description: 'Runs this step.'
		});
	});
});
