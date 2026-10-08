import { describe, it, expect, afterEach, vi } from 'vitest';

function installNavigator(writeText: ReturnType<typeof vi.fn> | undefined) {
	vi.stubGlobal('navigator', writeText ? { clipboard: { writeText } } : {});
}

function installDocument(execCommandResult: boolean) {
	const created: Array<{ value: string; style: Record<string, string> }> = [];
	const execCommand = vi.fn().mockReturnValue(execCommandResult);
	vi.stubGlobal('document', {
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
	});
	return { execCommand, created };
}

describe('copyText', () => {
	afterEach(() => {
		vi.restoreAllMocks();
		vi.unstubAllGlobals();
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
