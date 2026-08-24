import { describe, expect, it } from 'vitest';
import { shouldIgnoreEvent } from './keyboard';

function keyboardEvent(
	tagName: string,
	{ contentEditable = false, ctrl = false, meta = false, alt = false } = {}
): KeyboardEvent {
	return {
		target: { tagName, isContentEditable: contentEditable },
		ctrlKey: ctrl,
		metaKey: meta,
		altKey: alt
	} as unknown as KeyboardEvent;
}

describe('global keyboard focus guard', () => {
	it('keeps unmodified global navigation shortcuts out of editable fields', () => {
		expect(shouldIgnoreEvent(keyboardEvent('INPUT'))).toBe(true);
		expect(shouldIgnoreEvent(keyboardEvent('TEXTAREA'))).toBe(true);
		expect(shouldIgnoreEvent(keyboardEvent('DIV', { contentEditable: true }))).toBe(true);
	});

	it('still accepts global shortcuts outside editable fields or with modifiers', () => {
		expect(shouldIgnoreEvent(keyboardEvent('BUTTON'))).toBe(false);
		expect(shouldIgnoreEvent(keyboardEvent('INPUT', { ctrl: true }))).toBe(false);
	});
});
