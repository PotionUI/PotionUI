import { describe, expect, it, vi } from 'vitest';
import { History, type Command } from './history';

function command(label: string, bytes: number, log: string[] = []): Command {
	return {
		label,
		bytes,
		undo: () => log.push(`undo ${label}`),
		redo: () => log.push(`redo ${label}`)
	};
}

describe('History', () => {
	it('undoes and redoes in order', () => {
		const log: string[] = [];
		const history = new History(1000);
		history.push(command('a', 10, log));
		history.push(command('b', 10, log));

		expect(history.undo()?.label).toBe('b');
		expect(history.undo()?.label).toBe('a');
		expect(history.undo()).toBeNull();
		expect(history.redo()?.label).toBe('a');
		expect(log).toEqual(['undo b', 'undo a', 'redo a']);
	});

	it('drops the redo branch when a new command is pushed', () => {
		const history = new History(1000);
		history.push(command('a', 10));
		history.push(command('b', 10));
		history.undo();
		history.push(command('c', 10));

		expect(history.canRedo).toBe(false);
		expect(history.undoLabel).toBe('c');
		expect(history.bytes).toBe(20);
	});

	it('evicts the oldest commands to stay inside the byte budget', () => {
		const history = new History(100);
		history.push(command('a', 60));
		history.push(command('b', 60));
		history.push(command('c', 60));

		expect(history.length).toBe(1);
		expect(history.undoLabel).toBe('c');
		expect(history.bytes).toBe(60);
	});

	it('keeps a single command even when it alone exceeds the budget', () => {
		const history = new History(10);
		history.push(command('huge', 500));
		expect(history.length).toBe(1);
		expect(history.canUndo).toBe(true);
	});

	it('tracks dirtiness against the last saved position', () => {
		const history = new History(1000);
		expect(history.dirty).toBe(false);
		history.push(command('a', 10));
		expect(history.dirty).toBe(true);
		history.markSaved();
		expect(history.dirty).toBe(false);
		history.undo();
		expect(history.dirty).toBe(true);
		history.redo();
		expect(history.dirty).toBe(false);
	});

	it('stays dirty after the saved state was evicted from the stack', () => {
		const history = new History(100);
		history.push(command('a', 60));
		history.markSaved();
		history.push(command('b', 60));
		history.push(command('c', 60));
		history.undo();
		history.undo();
		expect(history.dirty).toBe(true);
	});

	it('notifies subscribers and stops after unsubscribe', () => {
		const history = new History(1000);
		const listener = vi.fn();
		const off = history.subscribe(listener);
		history.push(command('a', 1));
		history.undo();
		expect(listener).toHaveBeenCalledTimes(2);
		off();
		history.redo();
		expect(listener).toHaveBeenCalledTimes(2);
	});

	it('forgets everything on clear and reads as clean', () => {
		const history = new History(1000);
		history.push(command('a', 10));
		history.clear();
		expect(history.canUndo).toBe(false);
		expect(history.dirty).toBe(false);
		expect(history.bytes).toBe(0);
	});
});
