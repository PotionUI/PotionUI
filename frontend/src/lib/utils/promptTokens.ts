// Combined prompt-text tokenizer: text / {a|b|c} choice groups / ${name}
// variable usages, in one ordered, gap-free, non-overlapping pass. Two passes:
// parseChoiceGroupTokens finds `{a|b|c}` groups (it excludes any `{` preceded
// by `$`, so it never touches a variable usage), then each leftover text run
// is split by parseVariableUsageTokens for `${name}` spans. Because pass 1
// never emits a group overlapping a `${...}`, the two can't disagree about who
// owns a span.
//
// A `${name}` embedded INSIDE a group's option (e.g. `{a|${mood} b}`) stays
// opaque, part of that group's raw text — consistent with a nested `{x|y}`
// inside an option. Out of scope here.

import { parseChoiceGroupTokens, type GroupToken, type TextToken as GroupTextToken } from './choiceGroups';
import { parseVariableUsageTokens, type VariableUsageToken } from './promptVariables';

export type PromptToken = GroupTextToken | GroupToken | VariableUsageToken;

export function parsePromptTokens(text: string): PromptToken[] {
	const groupTokens = parseChoiceGroupTokens(text);
	const out: PromptToken[] = [];

	for (const gt of groupTokens) {
		if (gt.type === 'group') {
			out.push(gt);
			continue;
		}
		// A plain-text run from the group pass — split it further for `${name}`.
		const subTokens = parseVariableUsageTokens(gt.raw);
		for (const st of subTokens) {
			out.push({ ...st, start: st.start + gt.start, end: st.end + gt.start });
		}
	}

	return out;
}
