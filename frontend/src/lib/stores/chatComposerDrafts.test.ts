import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { chatComposerDrafts } from './chatComposerDrafts';

describe('chatComposerDrafts store', () => {
	beforeEach(() => chatComposerDrafts.reset());

	it('has no draft for a session that never saved one', () => {
		expect(chatComposerDrafts.load('s1')).toBeNull();
	});

	it('round-trips a saved draft', () => {
		chatComposerDrafts.save('s1', { text: 'hello', resources: {} });
		expect(chatComposerDrafts.load('s1')).toEqual({ text: 'hello', resources: {} });
	});

	it('keeps drafts isolated per session', () => {
		chatComposerDrafts.save('s1', { text: 'session one draft', resources: {} });
		chatComposerDrafts.save('s2', { text: 'session two draft', resources: {} });

		expect(chatComposerDrafts.load('s1')?.text).toBe('session one draft');
		expect(chatComposerDrafts.load('s2')?.text).toBe('session two draft');
	});

	it('gives a session with no server id (a conversation not yet sent) its own bucket', () => {
		chatComposerDrafts.save(null, { text: 'not sent yet', resources: {} });
		chatComposerDrafts.save('s1', { text: 'a real session', resources: {} });

		expect(chatComposerDrafts.load(null)?.text).toBe('not sent yet');
		expect(chatComposerDrafts.load('s1')?.text).toBe('a real session');
	});

	it('saving an empty draft clears the session (the send-path semantics)', () => {
		chatComposerDrafts.save('s1', { text: 'drafted message', resources: {} });
		expect(chatComposerDrafts.load('s1')).not.toBeNull();

		chatComposerDrafts.save('s1', { text: '', resources: {} });
		expect(chatComposerDrafts.load('s1')).toBeNull();
	});

	it('preserves attached resource chips alongside the text', () => {
		const resources = { 'res-1': { uri: '@form.prompt', label: 'prompt' } };
		chatComposerDrafts.save('s1', { text: 'see {res-1}', resources });
		expect(chatComposerDrafts.load('s1')).toEqual({ text: 'see {res-1}', resources });
	});

	it('clear() drops a session draft explicitly (new-chat semantics)', () => {
		chatComposerDrafts.save('s1', { text: 'drafted message', resources: {} });
		chatComposerDrafts.clear('s1');
		expect(chatComposerDrafts.load('s1')).toBeNull();
	});

	it('reset() wipes every session (identity-guard semantics on user switch)', () => {
		chatComposerDrafts.save('s1', { text: 'user a draft', resources: {} });
		chatComposerDrafts.save(null, { text: 'user a unsent draft', resources: {} });

		chatComposerDrafts.reset();

		expect(chatComposerDrafts.load('s1')).toBeNull();
		expect(chatComposerDrafts.load(null)).toBeNull();
		expect(get(chatComposerDrafts)).toEqual({});
	});
});
