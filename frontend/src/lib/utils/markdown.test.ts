import { describe, it, expect } from 'vitest';
import {
	processMarkdown,
	parseToolActions,
	processMarkdownWithActions,
	decorateVariableChips,
	parseToolCalls,
	injectToolCallChips,
	truncateAtReplyContractMarker
} from './markdown';

describe('processMarkdown - lists', () => {
	it('groups consecutive "- " lines into a single <ul> with no literal bullet', () => {
		const html = processMarkdown('- one\n- two\n- three');
		expect(html).toContain('<ul class="list-disc pl-5 my-2 space-y-0.5">');
		expect((html.match(/<ul/g) || []).length).toBe(1);
		expect((html.match(/<\/ul>/g) || []).length).toBe(1);
		expect(html).toContain('<li>one</li>');
		expect(html).toContain('<li>two</li>');
		expect(html).toContain('<li>three</li>');
		expect(html).not.toContain('•');
	});

	it('groups consecutive "N. " lines into a single <ol> with no literal number', () => {
		const html = processMarkdown('1. first\n2. second\n3. third');
		expect(html).toContain('<ol class="list-decimal pl-5 my-2 space-y-0.5">');
		expect((html.match(/<ol/g) || []).length).toBe(1);
		expect(html).toContain('<li>first</li>');
		expect(html).toContain('<li>second</li>');
		expect(html).toContain('<li>third</li>');
		expect(html).not.toMatch(/<li>1\./);
	});

	it('does not inject <br> between list items', () => {
		const html = processMarkdown('- one\n- two\n- three');
		expect(html).not.toMatch(/<li>one<\/li>\s*<br>/);
		expect(html).not.toMatch(/<\/li><br><li>/);
	});

	it('numbers an ordered list correctly even for the all-"1."-convention', () => {
		const html = processMarkdown('1. first\n1. second\n1. third');
		expect(html).toContain('<ol class="list-decimal pl-5 my-2 space-y-0.5">');
		expect((html.match(/<li>/g) || []).length).toBe(3);
	});

	it('handles a list immediately followed by a paragraph with no stray <br>', () => {
		const html = processMarkdown('- item one\n- item two\nSome trailing paragraph text.');
		expect(html).toContain('</ul>');
		expect(html).not.toMatch(/<\/ul><br>/);
		expect(html).toContain('Some trailing paragraph text.');
	});

	it('handles a paragraph immediately followed by a list with no stray <br>', () => {
		const html = processMarkdown('Common modes are:\n- txt2img\n- img2img');
		expect(html).not.toMatch(/Common modes are:<br><ul/);
		expect(html).toContain('Common modes are:');
		expect(html).toContain('<ul class="list-disc pl-5 my-2 space-y-0.5">');
	});
});

describe('processMarkdown - tables', () => {
	const table = [
		'| Key | Required | Type |',
		'|-----|----------|------|',
		'| `schema` | yes | int |',
		'| `id` | no | string |'
	].join('\n');

	it('renders a GFM table as a real <table> structure', () => {
		const html = processMarkdown(table);
		expect(html).toContain('<table class="w-full text-sm my-3 border-collapse">');
		expect(html).toContain('<thead>');
		expect(html).toContain('<tbody>');
		expect(html).toContain('<div class="overflow-x-auto">');
	});

	it('renders header cells with th classes and body cells with td classes', () => {
		const html = processMarkdown(table);
		expect(html).toContain(
			'<th class="text-left font-semibold px-3 py-1.5 border-b border-line-strong">Key</th>'
		);
		expect(html).toContain(
			'<td class="px-3 py-1.5 border-b border-line align-top"><code class="bg-surface-2 px-1 py-0.5 rounded text-sm font-mono text-fg-muted">schema</code></td>'
		);
	});

	it('runs inline formatting (inline code) inside cells', () => {
		const html = processMarkdown(table);
		expect(html).toContain('<code class="bg-surface-2 px-1 py-0.5 rounded text-sm font-mono text-fg-muted">schema</code>');
		expect(html).toContain('<code class="bg-surface-2 px-1 py-0.5 rounded text-sm font-mono text-fg-muted">id</code>');
	});

	it('does not leave raw pipe characters or literal separator rows in the output', () => {
		const html = processMarkdown(table);
		expect(html).not.toContain('|-----|');
		expect(html).not.toMatch(/\|\s*Key\s*\|/);
	});

	it('supports leading/trailing pipes and produces one row per body line', () => {
		const html = processMarkdown(table);
		const rowCount = (html.match(/<tr>/g) || []).length;
		// 1 header row + 2 body rows
		expect(rowCount).toBe(3);
	});

	it('supports tables without a wrapping pipe on every line variant (trailing pipe omitted)', () => {
		const noTrailingPipe = ['| A | B', '|---|---', '| 1 | 2'].join('\n');
		const html = processMarkdown(noTrailingPipe);
		expect(html).toContain('<table');
		expect(html).toContain('<th class="text-left font-semibold px-3 py-1.5 border-b border-line-strong">A</th>');
		expect(html).toContain('<td class="px-3 py-1.5 border-b border-line align-top">1</td>');
	});
});

describe('processMarkdown - mixed document', () => {
	it('renders heading + list + table + code block together without cross-contamination', () => {
		const doc = [
			'## Section',
			'',
			'Some intro text.',
			'',
			'- alpha',
			'- beta',
			'',
			'| Col |',
			'|-----|',
			'| val |',
			'',
			'```js',
			'const x = 1;',
			'```'
		].join('\n');

		const html = processMarkdown(doc);
		expect(html).toContain('<h2 class="text-xl font-semibold mb-1.5 mt-3">Section</h2>');
		expect(html).toContain('<ul class="list-disc pl-5 my-2 space-y-0.5">');
		expect(html).toContain('<li>alpha</li>');
		expect(html).toContain('<table class="w-full text-sm my-3 border-collapse">');
		expect(html).toContain('<pre><code class="bg-surface-2 block p-3 rounded-lg text-sm font-mono text-fg-muted my-2 overflow-x-auto">const x = 1;</code></pre>');
	});
});

describe('processMarkdown - existing behavior regressions', () => {
	it('extracts and restores fenced code blocks verbatim', () => {
		const html = processMarkdown('```python\nprint("hi")\n```');
		expect(html).toContain(
			'<pre><code class="bg-surface-2 block p-3 rounded-lg text-sm font-mono text-fg-muted my-2 overflow-x-auto">print("hi")</code></pre>'
		);
	});

	it('escapes HTML entities outside of code blocks', () => {
		const html = processMarkdown('a < b & c > d');
		expect(html).toContain('a &lt; b &amp; c &gt; d');
	});

	it('does not double-escape inside inline code', () => {
		const html = processMarkdown('`a < b`');
		expect(html).toContain(
			'<code class="bg-surface-2 px-1 py-0.5 rounded text-sm font-mono text-fg-muted">a &lt; b</code>'
		);
	});

	it('renders links with target=_blank and rel attributes', () => {
		const html = processMarkdown('[click here](https://example.com)');
		expect(html).toContain(
			'<a href="https://example.com" class="text-signal hover:underline" target="_blank" rel="noopener noreferrer">click here</a>'
		);
	});

	it('renders blockquotes', () => {
		const html = processMarkdown('> a wise quote');
		expect(html).toContain(
			'<blockquote class="border-l-4 border-line-strong pl-4 italic text-fg-muted my-1">a wise quote</blockquote>'
		);
	});

	it('renders bold and italic', () => {
		const html = processMarkdown('**bold** and *italic*');
		expect(html).toContain('<strong class="font-semibold">bold</strong>');
		expect(html).toContain('<em class="italic">italic</em>');
	});

	it('converts single newlines to <br> and double newlines to a spacer span', () => {
		const html = processMarkdown('line one\nline two\n\nnew paragraph');
		expect(html).toContain('line one<br>line two');
		expect(html).toContain('<span class="block mt-2"></span>new paragraph');
	});

	it('can treat source-wrapped lines as flowing prose without removing paragraph breaks', () => {
		const html = processMarkdown(
			'A long paragraph is\nwrapped in the source.\n\nA second paragraph.',
			{
				softLineBreaks: 'space'
			}
		);

		expect(html).toContain('A long paragraph is wrapped in the source.');
		expect(html).toContain('<span class="block mt-2"></span>A second paragraph.');
		expect(html).not.toContain('<br>');
	});

	it('keeps lists structured when surrounding prose uses soft line breaks', () => {
		const html = processMarkdown('An introduction\nthat continues.\n\n- first\n- second', {
			softLineBreaks: 'space'
		});

		expect(html).toContain('An introduction that continues.');
		expect(html).toContain('<ul class="list-disc pl-5 my-2 space-y-0.5">');
		expect(html).toContain('<li>first</li><li>second</li>');
	});

	it('returns empty string for empty input', () => {
		expect(processMarkdown('')).toBe('');
	});
});

describe('parseToolActions / processMarkdownWithActions', () => {
	it('extracts tool_action blocks and cleans the remaining text', () => {
		const text =
			'before <tool_action type="search" segment_index="0" segment_id="abc">query text</tool_action> after';
		const { cleanedText, actions } = parseToolActions(text);
		expect(cleanedText).toBe('before  after');
		expect(actions).toEqual([
			{ type: 'search', segmentIndex: 0, segmentId: 'abc', content: 'query text' }
		]);
	});

	it('extracts an update_director_segment block the same way as update_segment', () => {
		const text =
			'<tool_action type="update_director_segment" segment_index="2" segment_id="chain-2">a wide shot of the harbor</tool_action>';
		const { cleanedText, actions } = parseToolActions(text);
		expect(cleanedText).toBe('');
		expect(actions).toEqual([
			{ type: 'update_director_segment', segmentIndex: 2, segmentId: 'chain-2', content: 'a wide shot of the harbor' }
		]);
	});

	it('combines tool action parsing with markdown rendering', () => {
		const text = '**bold** <tool_action type="x" segment_index="1" segment_id="y">hidden</tool_action>';
		const { html, actions } = processMarkdownWithActions(text);
		expect(html).toContain('<strong class="font-semibold">bold</strong>');
		expect(actions).toHaveLength(1);
	});
});

describe('parseToolCalls', () => {
	it('does nothing when no tool_call tag is present', () => {
		const text = 'plain assistant text with no tags';
		const { cleanedText, calls } = parseToolCalls(text);
		expect(cleanedText).toBe(text);
		expect(calls).toEqual([]);
	});

	it('extracts a complete block and replaces it with a placement marker', () => {
		const text = 'before <tool_call>{"name": "update_video_director", "arguments": {}}</tool_call> after';
		const { cleanedText, calls } = parseToolCalls(text);
		expect(cleanedText).toBe('before \x00TOOLCALL0\x00 after');
		expect(calls).toEqual([{ status: 'complete', toolName: 'update_video_director' }]);
	});

	it('tolerates whitespace/newline variants around the tag and payload', () => {
		const text = 'x <tool_call>\n  {"name": "search_model_prompts"}\n</tool_call> y';
		const { calls } = parseToolCalls(text);
		expect(calls).toEqual([{ status: 'complete', toolName: 'search_model_prompts' }]);
	});

	it('parses the {"function": {"name": ...}} call shape too', () => {
		const text = '<tool_call>{"function": {"name": "enhance_prompt", "arguments": {}}}</tool_call>';
		const { calls } = parseToolCalls(text);
		expect(calls).toEqual([{ status: 'complete', toolName: 'enhance_prompt' }]);
	});

	it('falls back to a null tool name when the JSON payload fails to parse', () => {
		const text = '<tool_call>{name: update_video_director}</tool_call>';
		const { cleanedText, calls } = parseToolCalls(text);
		expect(cleanedText).toBe('\x00TOOLCALL0\x00');
		expect(calls).toEqual([{ status: 'complete', toolName: null }]);
	});

	it('falls back to a null tool name when the payload has mangled-quote artifacts', () => {
		const text = '<tool_call>{<|"|>name<|"|>: <|"|>update_video_director<|"|>}</tool_call>';
		const { calls } = parseToolCalls(text);
		expect(calls).toEqual([{ status: 'complete', toolName: null }]);
	});

	it('extracts multiple complete blocks in order with placement markers in text order', () => {
		const text =
			'a <tool_call>{"name": "one"}</tool_call> b <tool_call>{"name": "two"}</tool_call> c';
		const { cleanedText, calls } = parseToolCalls(text);
		expect(cleanedText).toBe('a \x00TOOLCALL0\x00 b \x00TOOLCALL1\x00 c');
		expect(calls).toEqual([
			{ status: 'complete', toolName: 'one' },
			{ status: 'complete', toolName: 'two' }
		]);
	});

	it('captures a trailing unclosed tool_call tag as an unclosed span running to the end', () => {
		const text = 'Let me check that.\n\n<tool_call>\n{"name": "update_video';
		const { cleanedText, calls } = parseToolCalls(text);
		expect(cleanedText).toBe('Let me check that.\n\n\x00TOOLCALL0\x00');
		expect(calls).toEqual([{ status: 'unclosed', toolName: null }]);
	});

	it('handles a complete block followed by a trailing unclosed one', () => {
		const text = '<tool_call>{"name": "one"}</tool_call> then <tool_call>{"name": "two"';
		const { cleanedText, calls } = parseToolCalls(text);
		expect(cleanedText).toBe('\x00TOOLCALL0\x00 then \x00TOOLCALL1\x00');
		expect(calls).toEqual([
			{ status: 'complete', toolName: 'one' },
			{ status: 'unclosed', toolName: null }
		]);
	});

	it('still parses tool_action blocks when a tool_call block is also present', () => {
		const text =
			'<tool_call>{"name": "one"}</tool_call> <tool_action type="x" segment_index="1" segment_id="y">hidden</tool_action>';
		const { cleanedText: withoutToolCalls, calls } = parseToolCalls(text);
		const { cleanedText, actions } = parseToolActions(withoutToolCalls);
		expect(calls).toEqual([{ status: 'complete', toolName: 'one' }]);
		expect(actions).toEqual([{ type: 'x', segmentIndex: 1, segmentId: 'y', content: 'hidden' }]);
		expect(cleanedText).toBe('\x00TOOLCALL0\x00');
	});

	it('processMarkdownWithActions exposes the extracted calls alongside actions', () => {
		const text = 'see <tool_call>{"name": "one"}</tool_call> here';
		const { html, toolCalls } = processMarkdownWithActions(text);
		expect(html).toContain('\x00TOOLCALL0\x00');
		expect(toolCalls).toEqual([{ status: 'complete', toolName: 'one' }]);
	});
});

describe('injectToolCallChips', () => {
	it('is a no-op when there are no calls', () => {
		const html = '<p>hello \x00TOOLCALL0\x00 world</p>';
		expect(injectToolCallChips(html, [], true)).toBe(html);
	});

	it('renders a subdued chip with the tool name for a parsed complete call', () => {
		const html = injectToolCallChips('a \x00TOOLCALL0\x00 b', [
			{ status: 'complete', toolName: 'update_video_director' }
		], false);
		expect(html).toContain('Update Video Director');
		expect(html).not.toContain('\x00TOOLCALL0\x00');
		expect(html).not.toMatch(/tool_call/i);
	});

	it('renders a generic label when the complete call failed to parse', () => {
		const html = injectToolCallChips('\x00TOOLCALL0\x00', [{ status: 'complete', toolName: null }], false);
		expect(html).toContain('Tool call');
	});

	it('renders a pulsing "Calling tool" chip for an unclosed span while streaming', () => {
		const html = injectToolCallChips('\x00TOOLCALL0\x00', [{ status: 'unclosed', toolName: null }], true);
		expect(html).toContain('Calling tool');
		expect(html).toContain('animate-pulse');
	});

	it('renders a muted "cut off" chip for an unclosed span once streaming has stopped', () => {
		const html = injectToolCallChips('\x00TOOLCALL0\x00', [{ status: 'unclosed', toolName: null }], false);
		expect(html).toContain('cut off');
		expect(html).not.toContain('animate-pulse');
	});

	it('never renders the raw JSON payload for a complete call', () => {
		const text = '<tool_call>{"name": "one", "arguments": {"secret": "shhh"}}</tool_call>';
		const { html, toolCalls } = processMarkdownWithActions(text);
		const rendered = injectToolCallChips(html, toolCalls, false);
		expect(rendered).not.toContain('shhh');
		expect(rendered).not.toContain('arguments');
	});
});

describe('link sanitization', () => {
	// Descriptions are editable by any authenticated user and rendered with {@html},
	// so a link target is untrusted input. The entity pass only escapes `< > &`.
	it('escapes a quote that would close the href and inject an attribute', () => {
		const html = processMarkdown('[click](" onmouseover="alert(1))');
		const tag = html.match(/<a\b[^>]*>/)?.[0] ?? '';
		const attrs = [...tag.matchAll(/\s([a-zA-Z-]+)=/g)].map((m) => m[1]);

		expect(attrs).toEqual(['href', 'class', 'target', 'rel']);
		expect(tag).not.toMatch(/\sonmouseover=/);
	});

	it('drops javascript: urls, keeping the label as plain text', () => {
		const html = processMarkdown('[click](javascript:alert(1))');
		expect(html).not.toContain('<a ');
		expect(html).not.toMatch(/javascript:/i);
		expect(html).toContain('click');
	});

	it('drops data: and vbscript: urls', () => {
		expect(processMarkdown('[x](data:text/html,hi)')).not.toContain('<a ');
		expect(processMarkdown('[x](vbscript:msgbox)')).not.toContain('<a ');
	});

	it('keeps http, https and mailto links', () => {
		expect(processMarkdown('[a](https://example.com)')).toContain('href="https://example.com"');
		expect(processMarkdown('[a](http://example.com)')).toContain('href="http://example.com"');
		expect(processMarkdown('[a](mailto:a@b.c)')).toContain('href="mailto:a@b.c"');
	});

	it('keeps relative and anchor links', () => {
		expect(processMarkdown('[a](/models/abc)')).toContain('href="/models/abc"');
		expect(processMarkdown('[a](#section)')).toContain('href="#section"');
	});

	it('does not double-escape ampersands already entity-escaped', () => {
		expect(processMarkdown('[a](https://x.com/?b=1&c=2)')).toContain('href="https://x.com/?b=1&amp;c=2"');
	});

	it('uses design-system tokens, not zinc colours', () => {
		const html = processMarkdown('[a](https://example.com) and `code`');
		expect(html).not.toMatch(/zinc-/);
	});
});

describe('decorateVariableChips', () => {
	const tips = { mood: 'one of noir, sunlit — shuffles each generation' };

	it('wraps a known ${name} in an accent-token chip with a tooltip', () => {
		const out = decorateVariableChips('use ${mood} please', tips);
		expect(out).toContain('border-accent/30');
		expect(out).toContain('bg-accent/10');
		expect(out).toContain('title="one of noir, sunlit — shuffles each generation"');
		expect(out).toContain('>mood<');
		expect(out).not.toContain('${mood}');
	});

	it('leaves an unknown ${name} as literal text (no warning)', () => {
		const out = decorateVariableChips('use ${unknown} here', tips);
		expect(out).toBe('use ${unknown} here');
	});

	it('does not decorate inside an inline <code> element', () => {
		const html = '<code class="x">${mood}</code>';
		expect(decorateVariableChips(html, tips)).toBe(html);
	});

	it('does not corrupt an href attribute containing ${name}', () => {
		const html = '<a href="https://x/${mood}">label</a>';
		expect(decorateVariableChips(html, tips)).toBe(html);
	});

	it('decorates the visible label of a link but not its href', () => {
		const html = '<a href="https://x">${mood}</a>';
		const out = decorateVariableChips(html, tips);
		expect(out).toContain('href="https://x"');
		expect(out).toContain('border-accent/30');
	});

	it('is a no-op when the tooltip map is empty', () => {
		expect(decorateVariableChips('${mood}', {})).toBe('${mood}');
	});

	it('attribute-escapes untrusted tooltip text (XSS-safe)', () => {
		const out = decorateVariableChips('${x}', { x: '"><img src=q onerror=alert(1)>' });
		expect(out).not.toContain('<img');
		expect(out).toContain('&quot;&gt;&lt;img');
	});
});

describe('processMarkdown - variable chips integration', () => {
	const opts = { variableChips: { mood: 'one of noir, sunlit — shuffles each generation' } };

	it('renders a chip for a known ${name} in prose', () => {
		const html = processMarkdown('Try ${mood} for atmosphere', opts);
		expect(html).toContain('border-accent/30');
		expect(html).toContain('>mood<');
	});

	it('keeps ${name} literal inside a fenced code block', () => {
		const html = processMarkdown('```\nvalue ${mood}\n```', opts);
		expect(html).toContain('${mood}');
		expect(html).not.toContain('border-accent/30');
	});

	it('keeps ${name} literal inside inline code', () => {
		const html = processMarkdown('use `${mood}` here', opts);
		expect(html).toContain('${mood}');
		// the prose copy outside code is unaffected; only the code span stays literal
	});

	it('does nothing when no variableChips option is passed', () => {
		const html = processMarkdown('Try ${mood} out');
		expect(html).toContain('${mood}');
		expect(html).not.toContain('border-accent/30');
	});

	it('processMarkdownWithActions forwards variableChips', () => {
		const { html } = processMarkdownWithActions('Try ${mood}', opts);
		expect(html).toContain('border-accent/30');
	});
});

describe('truncateAtReplyContractMarker', () => {
	it('cuts the text at a ## improved marker', () => {
		const text = 'Here is the revised prompt.\n\n## improved\n- Added lens detail';
		expect(truncateAtReplyContractMarker(text)).toBe('Here is the revised prompt.');
	});

	it('cuts the text at a ## questions marker', () => {
		const text = 'Sure, done.\n\n## questions\n- Golden hour or overcast?';
		expect(truncateAtReplyContractMarker(text)).toBe('Sure, done.');
	});

	it('is case-insensitive and tolerant of extra spacing', () => {
		const text = 'Prose.\n\n##   Improved\n- item';
		expect(truncateAtReplyContractMarker(text)).toBe('Prose.');
	});

	it('leaves text without a marker untouched', () => {
		const text = 'Just a plain reply with no sections.';
		expect(truncateAtReplyContractMarker(text)).toBe(text);
	});

	it('does not match a marker embedded mid-line', () => {
		const text = 'Discussing ## improved contrast inline is fine';
		expect(truncateAtReplyContractMarker(text)).toBe(text);
	});
});
