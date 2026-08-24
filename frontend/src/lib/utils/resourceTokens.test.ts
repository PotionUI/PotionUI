import { describe, it, expect } from 'vitest';
import {
	encodeResourceToken,
	parseResourceTokens,
	splitResourceTokens
} from './resourceTokens';

describe('encodeResourceToken', () => {
	it('encodes simple uris plainly', () => {
		expect(encodeResourceToken('models.lora.detailer')).toBe('@models.lora.detailer');
	});

	it('keeps dashes and underscores plain', () => {
		expect(encodeResourceToken('models.lora.add-detail_xl')).toBe('@models.lora.add-detail_xl');
	});

	it('brackets uris with spaces', () => {
		expect(encodeResourceToken('phrasebook.camera angles')).toBe('@[phrasebook.camera angles]');
	});
});

describe('parseResourceTokens', () => {
	it('round-trips simple and bracketed tokens', () => {
		const text = `check ${encodeResourceToken('models.lora.detailer')} and ${encodeResourceToken('presets.SDXL realistic.form.angles')}`;
		expect(parseResourceTokens(text)).toEqual([
			'models.lora.detailer',
			'presets.SDXL realistic.form.angles'
		]);
	});

	it('returns duplicates in order', () => {
		expect(parseResourceTokens('@a.b then @a.b')).toEqual(['a.b', 'a.b']);
	});

	it('ignores emails and mid-word @', () => {
		expect(parseResourceTokens('mail me at user@example.com or foo@@bar')).toEqual([]);
	});

	it('matches at start of text and after punctuation', () => {
		expect(parseResourceTokens('@models.lora ok')).toEqual(['models.lora']);
		expect(parseResourceTokens('(@models.lora)')).toEqual(['models.lora']);
		expect(parseResourceTokens('see:\n@models.lora')).toEqual(['models.lora']);
	});

	it('handles empty and token-free text', () => {
		expect(parseResourceTokens('')).toEqual([]);
		expect(parseResourceTokens('no tokens here')).toEqual([]);
	});
});

describe('splitResourceTokens', () => {
	it('splits mixed content preserving surrounding text', () => {
		expect(splitResourceTokens('use @models.lora.detailer for detail')).toEqual([
			{ type: 'text', value: 'use ' },
			{ type: 'resource', value: 'models.lora.detailer' },
			{ type: 'text', value: ' for detail' }
		]);
	});

	it('handles adjacent tokens separated by whitespace', () => {
		expect(splitResourceTokens('@a.b @c.d')).toEqual([
			{ type: 'resource', value: 'a.b' },
			{ type: 'text', value: ' ' },
			{ type: 'resource', value: 'c.d' }
		]);
	});

	it('decodes bracketed uris', () => {
		expect(splitResourceTokens('@[path with spaces] end')).toEqual([
			{ type: 'resource', value: 'path with spaces' },
			{ type: 'text', value: ' end' }
		]);
	});

	it('leaves emails as plain text', () => {
		expect(splitResourceTokens('user@example.com')).toEqual([
			{ type: 'text', value: 'user@example.com' }
		]);
	});

	it('handles a bare trailing @', () => {
		expect(splitResourceTokens('hello @')).toEqual([{ type: 'text', value: 'hello @' }]);
	});
});
