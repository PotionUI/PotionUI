import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import EditConflictNotice from './EditConflictNotice.svelte';
import EditSaveIndicator from './EditSaveIndicator.svelte';
import type { AutosaveStatus } from '$lib/tool-tabs/image/editAutosave';

const indicator = (status: AutosaveStatus, chip = false) => render(EditSaveIndicator, { props: { status, chip } }).body;
const notice = (props: Record<string, unknown>) => render(EditConflictNotice, { props: props as never }).body;

function isDisabled(html: string, label: string): boolean {
	const buttons = html.match(/<button[\s\S]*?<\/button>/g) ?? [];
	const button = buttons.find((candidate) => candidate.includes(label));
	if (!button) throw new Error(`no button labelled ${label}`);
	return /\sdisabled(=|\s|>)/.test(button.slice(0, button.indexOf('>') + 1));
}

describe('EditSaveIndicator', () => {
	it('shows nothing before there is anything to save', () => {
		expect(indicator('idle')).not.toContain('data-edit-save-state');
	});

	it('says saved, saving and offline in plain words', () => {
		expect(indicator('saved')).toContain('Saved');
		expect(indicator('saving')).toContain('Saving…');
		expect(indicator('offline')).toContain('Offline');
	});

	it('marks each state so it can be told apart', () => {
		const labels = {
			saved: 'Saved',
			saving: 'Saving…',
			offline: 'Offline',
			failed: 'Not saved',
			conflict: 'Changed elsewhere',
			'too-large': 'Not saved'
		} as const;
		for (const status of Object.keys(labels) as Array<keyof typeof labels>) {
			const html = indicator(status);
			expect(html).toContain(`data-edit-save-state="${status}"`);
			expect(html).toContain(labels[status]);
		}
	});

	it('renders the same state on the phone chip', () => {
		const html = indicator('offline', true);
		expect(html).toContain('data-edit-save-state="offline"');
		expect(html).toContain('Offline');
	});

	it('keeps technical words out of the copy', () => {
		for (const status of ['saved', 'saving', 'offline', 'failed', 'conflict', 'too-large'] as const) {
			expect(indicator(status)).not.toMatch(/revision|server|409|413|session/i);
		}
	});
});

describe('EditConflictNotice', () => {
	it('offers both ways out of a conflict', () => {
		const html = notice({ kind: 'conflict' });
		expect(html).toContain('Load the newer one');
		expect(html).toContain('Keep mine as a copy');
		expect(html).toContain('data-edit-notice="conflict"');
		expect(html).toContain('Nothing has been replaced');
	});

	it('disables both actions while one is running', () => {
		const html = notice({ kind: 'conflict', busy: true });
		expect(isDisabled(html, 'Load the newer one')).toBe(true);
		expect(isDisabled(html, 'Keep mine as a copy')).toBe(true);
	});

	it('enables both actions when nothing is running', () => {
		const html = notice({ kind: 'conflict' });
		expect(isDisabled(html, 'Load the newer one')).toBe(false);
		expect(isDisabled(html, 'Keep mine as a copy')).toBe(false);
	});

	it('explains an oversized edit without offering actions', () => {
		const html = notice({ kind: 'too-large' });
		expect(html).toContain('data-edit-notice="too-large"');
		expect(html).toContain('stays in this browser');
		expect(html).not.toContain('Load the newer one');
		expect(html).not.toContain('Keep mine as a copy');
	});

	it('keeps technical words out of the copy', () => {
		for (const kind of ['conflict', 'too-large'] as const) {
			expect(notice({ kind })).not.toMatch(/revision|server|409|413|session/i);
		}
	});
});
