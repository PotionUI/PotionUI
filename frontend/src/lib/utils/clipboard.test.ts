import { describe, it, expect, afterEach, vi } from 'vitest';

// The suite runs in the 'node' environment (no DOM), so navigator/document are
// stubbed directly on globalThis per test, mirroring notificationChime.test.ts.

function installNavigator(writeText: ReturnType<typeof vi.fn> | undefined) {
	(globalThis as { navigator?: unknown }).navigator = writeText
		? { clipboard: { writeText } }
		: {};
}

function installDocument(execCommandResult: boolean) {
	const created: Array<{ value: string; style: Record<string, string> }> = [];
	const execCommand = vi.fn().mockReturnValue(execCommandResult);
	(globalThis as { document?: unknown }).document = {
		createElement: () => {
			const el = {
				value: '',
				style: {} as Record<string, string>,
				setAttribute: vi.fn(),
				select: vi.fn(),
				setSelectionRange: vi.fn()
			};
			created.push(el);
			return el;
		},
		body: { appendChild: vi.fn(), removeChild: vi.fn() },
		execCommand
	};
	return { execCommand, created };
}

describe('copyText', () => {
	afterEach(() => {
		vi.restoreAllMocks();
		delete (globalThis as { navigator?: unknown }).navigator;
		delete (globalThis as { document?: unknown }).document;
	});

	it('uses the Clipboard API when available', async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		installNavigator(writeText);
		const { copyText } = await import('./clipboard');
		const ok = await copyText('hello');
		expect(ok).toBe(true);
		expect(writeText).toHaveBeenCalledWith('hello');
	});

	it('falls back to execCommand when the Clipboard API rejects', async () => {
		const writeText = vi.fn().mockRejectedValue(new Error('denied'));
		installNavigator(writeText);
		const { execCommand } = installDocument(true);
		const { copyText } = await import('./clipboard');
		const ok = await copyText('hello');
		expect(ok).toBe(true);
		expect(execCommand).toHaveBeenCalledWith('copy');
	});

	it('falls back to execCommand when Clipboard API is undefined (insecure context)', async () => {
		installNavigator(undefined);
		installDocument(true);
		const { copyText } = await import('./clipboard');
		const ok = await copyText('hello');
		expect(ok).toBe(true);
	});

	it('returns false when neither Clipboard API nor document are available', async () => {
		installNavigator(undefined);
		const { copyText } = await import('./clipboard');
		const ok = await copyText('hello');
		expect(ok).toBe(false);
	});

	it('returns false when execCommand fails', async () => {
		installNavigator(undefined);
		installDocument(false);
		const { copyText } = await import('./clipboard');
		const ok = await copyText('hello');
		expect(ok).toBe(false);
	});
});
