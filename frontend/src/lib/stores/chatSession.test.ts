import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { chatSession, modeLocked, DEFAULT_CHAT_MODE } from './chatSession';

describe('chatSession store', () => {
	beforeEach(() => chatSession.reset());

	it('starts with the default mode', () => {
		const s = get(chatSession);
		expect(s.mode).toBe(DEFAULT_CHAT_MODE);
		expect(s.sessionId).toBeNull();
		expect(s.messages).toEqual([]);
	});

	it('newConversation resets everything but adopts the given mode', () => {
		chatSession.patch({ sessionId: 'x', error: 'boom', disabledTools: ['a'] });
		chatSession.addMessage({ role: 'user', content: 'hi', timestamp: 1 });
		chatSession.newConversation('dataset-generator');
		const s = get(chatSession);
		expect(s).toMatchObject({
			sessionId: null,
			mode: 'dataset-generator',
			messages: [],
			disabledTools: [],
			error: ''
		});
	});

	it('modeLocked derives from message presence', () => {
		expect(get(modeLocked)).toBe(false);
		chatSession.addMessage({ role: 'user', content: 'hi', timestamp: 1 });
		expect(get(modeLocked)).toBe(true);
		chatSession.newConversation();
		expect(get(modeLocked)).toBe(false);
	});

	it('loadedSession adopts the session mode', () => {
		chatSession.loadedSession({ id: 's1', mode: 'plugin-mode' }, [
			{ role: 'user', content: 'a', timestamp: 1 }
		]);
		const s = get(chatSession);
		expect(s).toMatchObject({
			sessionId: 's1',
			mode: 'plugin-mode'
		});
		expect(s.messages).toHaveLength(1);
	});

	it('loadedSession keeps the current mode when the session has none', () => {
		chatSession.newConversation('generation');
		chatSession.loadedSession({ id: 's2' }, []);
		expect(get(chatSession).mode).toBe('generation');
	});

	it('applyStreamEvent routes token events with accumulated content', () => {
		chatSession.addMessage({ role: 'user', content: 'q', timestamp: 1 });
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: 2, isStreaming: true });
		chatSession.applyStreamEvent({ type: 'token', data: { content: 'He' } }, { accumulated: 'He' });
		chatSession.applyStreamEvent(
			{ type: 'token', data: { content: 'y' } },
			{ accumulated: 'Hey' }
		);
		const s = get(chatSession);
		expect(s.messages[1].content).toBe('Hey');
	});

	it('applyStreamEvent routes tool_start/tool_end events', () => {
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: 1, isStreaming: true });
		chatSession.applyStreamEvent({ type: 'tool_start', data: { tool_name: 'list_models' } });
		chatSession.applyStreamEvent({ type: 'tool_end', data: { tool_name: 'list_models' } });
		const execs = get(chatSession).messages[0].tool_executions!;
		expect(execs).toHaveLength(1);
		expect(execs[0].status).toBe('done');
	});

	it('applyStreamEvent error removes the streaming placeholder', () => {
		chatSession.addMessage({ role: 'user', content: 'q', timestamp: 1 });
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: 2, isStreaming: true });
		chatSession.applyStreamEvent({ type: 'error', data: {} });
		expect(get(chatSession).messages).toHaveLength(1);
	});

	it('updateMessages applies a pure transform', () => {
		chatSession.addMessage({ role: 'user', content: 'a', timestamp: 1 });
		chatSession.updateMessages((msgs) => msgs.map((m) => ({ ...m, content: 'b' })));
		expect(get(chatSession).messages[0].content).toBe('b');
	});

	describe('clientKey assignment (keyed {#each} identity)', () => {
		it('assigns a clientKey to a message added with neither id nor clientKey', () => {
			chatSession.addMessage({ role: 'user', content: 'hi', timestamp: 1 });
			const [msg] = get(chatSession).messages;
			expect(msg.clientKey).toBeTruthy();
		});

		it('gives two messages added back to back distinct clientKeys', () => {
			chatSession.addMessage({ role: 'user', content: 'a', timestamp: 1 });
			chatSession.addMessage({ role: 'assistant', content: '', timestamp: 2, isStreaming: true });
			const [first, second] = get(chatSession).messages;
			expect(first.clientKey).not.toBe(second.clientKey);
		});

		it('never overwrites a caller-supplied id or clientKey', () => {
			chatSession.addMessage({ id: 'server-id', role: 'user', content: 'hi', timestamp: 1 });
			chatSession.addMessage({ clientKey: 'my-key', role: 'user', content: 'hi', timestamp: 2 });
			const [withId, withKey] = get(chatSession).messages;
			expect(withId.clientKey).toBeUndefined();
			expect(withId.id).toBe('server-id');
			expect(withKey.clientKey).toBe('my-key');
		});
	});

	describe('windowed transcript', () => {
		it('loadedSession without meta assumes the whole conversation was loaded', () => {
			chatSession.loadedSession({ id: 's1', mode: 'generation' }, [
				{ role: 'user', content: 'a', timestamp: 1 },
				{ role: 'assistant', content: 'b', timestamp: 2 }
			]);
			const s = get(chatSession);
			expect(s.messageCount).toBe(2);
			expect(s.hasEarlier).toBe(false);
		});

		it('loadedSession with meta records a tail window short of the true total', () => {
			chatSession.loadedSession(
				{ id: 's1', mode: 'generation' },
				[{ role: 'assistant', content: 'recent', timestamp: 2 }],
				{ messageCount: 200, hasEarlier: true }
			);
			const s = get(chatSession);
			expect(s.messages).toHaveLength(1);
			expect(s.messageCount).toBe(200);
			expect(s.hasEarlier).toBe(true);
		});

		it('prependMessages puts the older page before what was already loaded and updates hasEarlier', () => {
			chatSession.loadedSession(
				{ id: 's1', mode: 'generation' },
				[{ id: 'm3', role: 'user', content: 'third', timestamp: 3 }],
				{ messageCount: 3, hasEarlier: true }
			);
			chatSession.prependMessages(
				[
					{ id: 'm1', role: 'user', content: 'first', timestamp: 1 },
					{ id: 'm2', role: 'assistant', content: 'second', timestamp: 2 }
				],
				false
			);
			const s = get(chatSession);
			expect(s.messages.map((m) => m.id)).toEqual(['m1', 'm2', 'm3']);
			expect(s.hasEarlier).toBe(false);
			expect(s.loadingEarlier).toBe(false);
		});

		it('addMessage keeps messageCount in step with live growth', () => {
			chatSession.loadedSession(
				{ id: 's1', mode: 'generation' },
				[{ id: 'm1', role: 'user', content: 'a', timestamp: 1 }],
				{ messageCount: 1, hasEarlier: false }
			);
			chatSession.addMessage({ role: 'user', content: 'b', timestamp: 2 });
			expect(get(chatSession).messageCount).toBe(2);
		});

		it('modeLocked is true for a loaded session whose window happens to be empty but has a real message count', () => {
			chatSession.loadedSession({ id: 's1', mode: 'generation' }, [], { messageCount: 5, hasEarlier: true });
			expect(get(modeLocked)).toBe(true);
		});
	});
});
