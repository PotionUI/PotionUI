import { describe, expect, it } from 'vitest';
import { kitNoticeText, kitNoticesOf } from './kitSubmit';

const notice = { code: 'unsupported_kind', message: 'This model does not take audio: `voice` is skipped.', kit: 'mara', item: 'voice', kind: 'audio', field: 'refs' };

describe('submit notices', () => {
	it('reads them from the start result and ignores a result without any', () => {
		expect(kitNoticesOf({ generation_id: 'g', kits: { used: [], notices: [notice] } })).toEqual([notice]);
		expect(kitNoticesOf({ generation_id: 'g' })).toEqual([]);
		expect(kitNoticesOf(null)).toEqual([]);
	});

	it('words them as plain lines, once each', () => {
		expect(kitNoticeText([notice, notice])).toBe('This model does not take audio: voice is skipped.');
	});
});
