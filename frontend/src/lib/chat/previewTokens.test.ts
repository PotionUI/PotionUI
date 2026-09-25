import { describe, expect, it } from 'vitest';
import { splitMarkerTokens } from './previewTokens';

describe('splitMarkerTokens', () => {
	it('renders bare and bracketed phrasebook markers as chips with the category as label', () => {
		expect(splitMarkerTokens('a #hair.color and #[lighting mood] b')).toEqual([
			{ kind: 'text', text: 'a ', label: '' },
			{ kind: 'phrasebook', text: '#hair.color', label: 'hair.color' },
			{ kind: 'text', text: ' and ', label: '' },
			{ kind: 'phrasebook', text: '#[lighting mood]', label: 'lighting mood' },
			{ kind: 'text', text: ' b', label: '' }
		]);
	});

	it('renders ${name} references as variable chips', () => {
		expect(splitMarkerTokens('portrait of ${subject}, ${ style }')).toEqual([
			{ kind: 'text', text: 'portrait of ', label: '' },
			{ kind: 'variable', text: '${subject}', label: 'subject' },
			{ kind: 'text', text: ', ', label: '' },
			{ kind: 'variable', text: '${ style }', label: 'style' }
		]);
	});

	it('leaves plain text untouched', () => {
		expect(splitMarkerTokens('no markers here')).toEqual([{ kind: 'text', text: 'no markers here', label: '' }]);
	});

	it('renders a bare marker with a hyphenated category path as a phrasebook chip', () => {
		expect(splitMarkerTokens('a #potion-light glow')).toEqual([
			{ kind: 'text', text: 'a ', label: '' },
			{ kind: 'phrasebook', text: '#potion-light', label: 'potion-light' },
			{ kind: 'text', text: ' glow', label: '' }
		]);
	});

	it('does not swallow a trailing period that ends the sentence, not the path', () => {
		expect(splitMarkerTokens('#potion-light.')).toEqual([
			{ kind: 'phrasebook', text: '#potion-light', label: 'potion-light' },
			{ kind: 'text', text: '.', label: '' }
		]);
	});
});
