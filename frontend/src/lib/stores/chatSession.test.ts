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
});
