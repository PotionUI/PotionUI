import { expect, test } from 'vitest';
import { holdUnsavableVariables } from './draft';
import type { ElixirDefinition } from './types';

function writer(variables: Record<string, unknown>): ElixirDefinition {
	return {
		schema_version: 1,
		nodes: [
			{ id: 'w', kind: 'llm', title: null, instruction: 'x', target_node_id: null, pinned: {}, variables } as never,
			{ id: 'p', kind: 'preset', title: 'P', preset_id: 'a', mode: 'b' }
		],
		edges: [],
		viewport: null
	};
}

const empty = { type: 'choice', options: ['', ' '], mode: 'shuffle', pinnedIndex: null };
const filled = { type: 'choice', options: ['red', 'blue'], mode: 'shuffle', pinnedIndex: null };
const text = { type: 'text', value: 'hi' };

test('a choice variable with no non-empty option is not sent', () => {
	const sent = holdUnsavableVariables(writer({ colour: empty, note: text }), null);
	expect(sent.nodes[0].variables).toStrictEqual({ note: text });
});

test('the last saved version of the variable is sent while the draft is held', () => {
	const sent = holdUnsavableVariables(writer({ colour: empty }), writer({ colour: filled }));
	expect(sent.nodes[0].variables).toStrictEqual({ colour: filled });
});

test('the rest of the elixir is sent unchanged', () => {
	const draft = writer({ colour: empty });
	const sent = holdUnsavableVariables(draft, null);
	expect(sent.nodes[1]).toBe(draft.nodes[1]);
	expect(sent.edges).toBe(draft.edges);
});

test('a draft with usable variables is returned as is', () => {
	const draft = writer({ colour: filled, note: text });
	expect(holdUnsavableVariables(draft, null)).toBe(draft);
});
