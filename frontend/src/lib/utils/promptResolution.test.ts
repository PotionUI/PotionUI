import { describe, it, expect } from 'vitest';
import { alignTemplateToRendered } from './promptResolution';

describe('alignTemplateToRendered', () => {
	it('returns a single static span for a template with no dynamic content', () => {
		const result = alignTemplateToRendered('a plain prompt', 'a plain prompt');
		expect(result).toEqual({
			spans: [{ type: 'static', text: 'a plain prompt' }],
			rolled: []
		});
	});

	it('resolves a single choice group', () => {
		const result = alignTemplateToRendered('a photo of {cat|dog}', 'a photo of dog');
		expect(result?.spans).toEqual([
			{ type: 'static', text: 'a photo of ' },
			{ type: 'resolved', text: 'dog', label: '{cat|dog}', kind: 'group', ambiguous: false }
		]);
		expect(result?.rolled).toEqual([
			{ label: '{cat|dog}', kind: 'group', resolvedText: 'dog', ambiguous: false }
		]);
	});

	it('resolves a single variable usage', () => {
		const result = alignTemplateToRendered('a photo, ${mood} lighting', 'a photo, somber lighting');
		expect(result?.rolled).toEqual([
			{ label: '${mood}', kind: 'variable', resolvedText: 'somber', ambiguous: false }
		]);
		expect(result?.spans).toEqual([
			{ type: 'static', text: 'a photo, ' },
			{ type: 'resolved', text: 'somber', label: '${mood}', kind: 'variable', ambiguous: false },
			{ type: 'static', text: ' lighting' }
		]);
	});

	it('resolves several groups and variables interleaved, each attributed separately', () => {
		const template = '${style} portrait of a {cat|dog}, ${mood} mood';
		const rendered = 'noir portrait of a cat, somber mood';
		const result = alignTemplateToRendered(template, rendered);
		expect(result?.rolled).toEqual([
			{ label: '${style}', kind: 'variable', resolvedText: 'noir', ambiguous: false },
			{ label: '{cat|dog}', kind: 'group', resolvedText: 'cat', ambiguous: false },
			{ label: '${mood}', kind: 'variable', resolvedText: 'somber', ambiguous: false }
		]);
	});

	it('handles a variable whose bound value itself contained a nested group (opaque resolved text)', () => {
		// ${mood} was bound to a variable template like "{happy|sad}" — dynamicprompts
		// resolves that nested group server-side (SamplingContext.with_variables,
		// see expander.py `_base_context`) before the ${mood} usage is substituted.
		// The FRONTEND only ever sees the final resolved text for the usage token,
		// never the nested group itself. Here the resolved text happens to still
		// contain literal `{`/`}` characters (e.g. the bound value wasn't valid
		// dynamicprompts syntax and survived verbatim) — that must not confuse the
		// static-chunk search, since it only ever looks at the TEMPLATE for braces.
		const template = 'a ${mood} painting';
		const rendered = 'a {happy} painting';
		const result = alignTemplateToRendered(template, rendered);
		expect(result?.rolled).toEqual([
			{ label: '${mood}', kind: 'variable', resolvedText: '{happy}', ambiguous: false }
		]);
	});

	it('marks a run of adjacent dynamic tokens (no static anchor between them) as ambiguous', () => {
		const template = '{a|b}${mood}';
		const rendered = 'bhappy';
		const result = alignTemplateToRendered(template, rendered);
		expect(result?.spans).toEqual([
			{ type: 'resolved', text: 'bhappy', label: '{a|b} + ${mood}', kind: 'mixed', ambiguous: true }
		]);
		expect(result?.rolled).toEqual([
			{ label: '{a|b} + ${mood}', kind: 'mixed', resolvedText: 'bhappy', ambiguous: true }
		]);
	});

	it('marks adjacent SAME-kind dynamic tokens as ambiguous but keeps a uniform kind label', () => {
		const template = '{a|b}{c|d}';
		const rendered = 'bc';
		const result = alignTemplateToRendered(template, rendered);
		expect(result?.rolled).toEqual([
			{ label: '{a|b} + {c|d}', kind: 'group', resolvedText: 'bc', ambiguous: true }
		]);
	});

	it('handles a choice group that rolled an empty option', () => {
		const result = alignTemplateToRendered('a {|x} b', 'a  b');
		expect(result?.rolled).toEqual([{ label: '{|x}', kind: 'group', resolvedText: '', ambiguous: false }]);
		expect(result?.spans).toEqual([
			{ type: 'static', text: 'a ' },
			{ type: 'resolved', text: '', label: '{|x}', kind: 'group', ambiguous: false },
			{ type: 'static', text: ' b' }
		]);
	});

	it('handles a dynamic token that is the very last thing in the template', () => {
		const result = alignTemplateToRendered('a photo of {cat|dog}', 'a photo of cat');
		expect(result?.rolled).toEqual([{ label: '{cat|dog}', kind: 'group', resolvedText: 'cat', ambiguous: false }]);
	});

	it('handles a dynamic token that is the very first thing in the template', () => {
		const result = alignTemplateToRendered('{cat|dog} in a field', 'dog in a field');
		expect(result?.rolled).toEqual([{ label: '{cat|dog}', kind: 'group', resolvedText: 'dog', ambiguous: false }]);
	});

	it('tolerates a template/rendered mismatch caused only by the backend stripping outer whitespace', () => {
		const result = alignTemplateToRendered('  a photo of {cat|dog}  ', 'a photo of dog');
		expect(result?.rolled).toEqual([{ label: '{cat|dog}', kind: 'group', resolvedText: 'dog', ambiguous: false }]);
	});

	it('returns null (alignment failed) when a static chunk is not found in the rendered text', () => {
		const result = alignTemplateToRendered('a photo of {cat|dog}', 'a completely different sentence');
		expect(result).toBeNull();
	});

	it('returns null when a plugin/prompt.transform rewrote the text beyond recognition', () => {
		const result = alignTemplateToRendered('sunny day, {cat|dog}', 'a rewritten prompt with no relation');
		expect(result).toBeNull();
	});

	it('returns null when the rendered text has unexplained trailing content', () => {
		const result = alignTemplateToRendered('a plain prompt', 'a plain prompt and then some');
		expect(result).toBeNull();
	});

	it('returns an empty alignment for an empty template and empty rendered text', () => {
		expect(alignTemplateToRendered('', '')).toEqual({ spans: [], rolled: [] });
		expect(alignTemplateToRendered('   ', '')).toEqual({ spans: [], rolled: [] });
	});

	it('returns null for an empty template but non-empty rendered text', () => {
		expect(alignTemplateToRendered('', 'unexpected text')).toBeNull();
	});

	it('never throws on pathological input (unbalanced braces, stray $)', () => {
		expect(() => alignTemplateToRendered('unterminated {broken', 'unterminated {broken')).not.toThrow();
		expect(() => alignTemplateToRendered('$ not a variable, {not a group', '$ not a variable, {not a group')).not.toThrow();
	});
});
