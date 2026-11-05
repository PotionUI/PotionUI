import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import StepEditBar from '$lib/components/elixirs/StepEditBar.svelte';
import StepDialogs from '$lib/components/elixirs/StepDialogs.svelte';
import type { StepBarModel } from '$lib/elixirs/stepBar';

let instance: ReturnType<typeof mount> | undefined;

function model(over: Partial<StepBarModel> = {}): StepBarModel {
	return {
		elixirName: 'Potion study',
		stepLabel: 'Orbit left',
		follow: false,
		sessionName: null,
		steps: [
			{ id: 'a', label: 'Orbit left', detail: 'Wan 2.2 · Image to video', follow: false },
			{ id: 'b', label: 'Close-up reveal', detail: 'Krea 2 · Text to image', follow: true },
			{ id: 'c', label: 'Upscale', detail: 'SeedVR2 · Upscale', follow: false }
		],
		index: 0,
		edits: 0,
		saving: false,
		opening: false,
		confirm: null,
		conflict: null,
		back: vi.fn(),
		go: vi.fn(),
		save: vi.fn(),
		answerConfirm: vi.fn(),
		resolveConflict: vi.fn(),
		...over
	};
}

function render(component: typeof StepEditBar | typeof StepDialogs, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(component as never, { target, props });
	flushSync();
}

const extra: Array<ReturnType<typeof mount>> = [];

function renderDesktop(m: StepBarModel) {
	const lead = document.createElement('div');
	const actions = document.createElement('div');
	document.body.append(lead, actions);
	instance = mount(StepEditBar as never, { target: lead, props: { model: m, part: 'lead' } });
	extra.push(mount(StepEditBar as never, { target: actions, props: { model: m, part: 'actions' } }));
	flushSync();
}

const buttons = () => [...document.body.querySelectorAll<HTMLButtonElement>('button')];
const byText = (text: string) => buttons().find((button) => button.textContent?.trim() === text);
const byLabel = (label: string) => document.body.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);

afterEach(() => {
	for (const item of extra.splice(0)) unmount(item);
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

const sessionState = {
	sessionControlsEnabled: true,
	currentSession: { id: 's1', name: 'Bottle orbit' },
	hasUnsavedChanges: false,
	isQuickSaving: false
};
const sessionActions = {};

const removed = ['Editing', 'Following', 'Saved automatically', 'Test runs go to History', 'Steps'];

function expectNoLeadExtras() {
	const text = document.body.textContent ?? '';
	for (const word of removed) expect(text).not.toContain(word);
	expect(document.body.querySelector('[data-testid="step-follow-chip"]')).toBeNull();
	expect(document.body.querySelector('[data-testid="step-autosaved"]')).toBeNull();
	expect(document.body.querySelector('[data-testid="step-history-note"]')).toBeNull();
	expect(byLabel('Previous step')).toBeNull();
	expect(byLabel('Next step')).toBeNull();
}

describe('the edit bar on desktop in copy mode', () => {
	it('is Back plus Discard and Save to elixir, with nothing else', () => {
		renderDesktop(model());
		expect(document.body.querySelectorAll('[data-testid="step-edit-bar"]')).toHaveLength(1);
		expect(document.body.textContent).toContain('Back to Potion study');
		expect(byText('Discard')).toBeTruthy();
		expect(byText('Save to elixir')).toBeTruthy();
		expectNoLeadExtras();
	});

	it('routes the buttons to the model', () => {
		const m = model();
		renderDesktop(m);
		byText('Save to elixir')!.click();
		expect(m.save).toHaveBeenCalled();
		byText('Discard')!.click();
		expect(m.back).toHaveBeenCalled();
		byText('Back to Potion study')!.click();
		expect(m.back).toHaveBeenCalledTimes(2);
	});

	it('shows Unsaved once there are edits', () => {
		renderDesktop(model({ edits: 2 }));
		expect(document.body.querySelector('[data-testid="step-unsaved"]')).not.toBeNull();
	});

	it('locks the controls while saving', () => {
		renderDesktop(model({ saving: true }));
		expect(byText('Discard')!.disabled).toBe(true);
		expect(byText('Back to Potion study')!.disabled).toBe(true);
	});
});

describe('the edit bar for a followed step', () => {
	it('is only Back, with no Save, Discard or Unsaved', () => {
		renderDesktop(model({ follow: true, sessionName: 'Bottle orbit', stepLabel: 'Close-up reveal', index: 1 }));
		expect(document.body.textContent).toContain('Back to Potion study');
		expect(byText('Save to elixir')).toBeUndefined();
		expect(byText('Discard')).toBeUndefined();
		expect(document.body.querySelector('[data-testid="step-unsaved"]')).toBeNull();
		expectNoLeadExtras();
	});
});

describe('the edit bar on a phone', () => {
	it('in copy mode is Back and Save only', () => {
		const m = model();
		render(StepEditBar, { model: m, part: 'phone' });
		expect(byLabel('Back to Potion study')).toBeTruthy();
		expect(byText('Save')).toBeTruthy();
		expect(byLabel('Steps')).toBeNull();
		expectNoLeadExtras();
		byText('Save')!.click();
		expect(m.save).toHaveBeenCalled();
	});

	it('for a followed step is Back and the session control, with no Save', () => {
		render(StepEditBar, { model: model({ follow: true }), part: 'phone', session: sessionState, actions: sessionActions });
		expect(byLabel('Back to Potion study')).toBeTruthy();
		expect(byText('Save to elixir')).toBeUndefined();
		expect(document.body.querySelector('[data-testid="step-session"] [data-testid="session-save"]')).not.toBeNull();
		expect(document.body.querySelector('[data-testid="step-session"]')?.textContent).toContain('Bottle orbit');
		expectNoLeadExtras();
	});
});

describe('the step dialogs', () => {
	it('renders nothing while idle', () => {
		render(StepDialogs, { model: model() });
		expect(document.body.querySelector('[data-testid="step-confirm"]')).toBeNull();
		expect(document.body.querySelector('[data-testid="step-conflict"]')).toBeNull();
	});

	it('asks to save the changes to the step', () => {
		render(StepDialogs, { model: model({ confirm: { reason: 'leave', target: null } }) });
		expect(document.body.querySelector('[data-testid="step-confirm"]')?.textContent).toContain('Save changes to Orbit left?');
		const labels = buttons().map((button) => button.textContent ?? '');
		expect(labels.some((label) => label.includes('Save to elixir'))).toBe(true);
		expect(labels.some((label) => label.trim() === 'Discard')).toBe(true);
		expect(labels.some((label) => label.includes('Cancel'))).toBe(true);
	});

	it.each([
		['Save to elixir', 'save'],
		['Discard', 'discard'],
		['Cancel', 'cancel']
	])('%s answers %s', (label, answer) => {
		const m = model({ confirm: { reason: 'leave', target: null } });
		render(StepDialogs, { model: m });
		buttons().find((button) => button.textContent?.includes(label))!.click();
		expect(m.answerConfirm).toHaveBeenCalledWith(answer);
	});

	it('names the conflict', () => {
		render(StepDialogs, { model: model({ conflict: { reason: 'changed', canOverwrite: true, after: 'leave' } }) });
		expect(document.body.querySelector('[data-testid="step-conflict"]')?.textContent).toContain('Potion study changed since you opened this step');
	});

	it.each([
		['Reload step', 'reload'],
		['Overwrite', 'overwrite'],
		['Cancel', 'cancel']
	])('conflict %s resolves as %s', (label, answer) => {
		const m = model({ conflict: { reason: 'changed', canOverwrite: true, after: 'leave' } });
		render(StepDialogs, { model: m });
		buttons().find((button) => button.textContent?.includes(label))!.click();
		expect(m.resolveConflict).toHaveBeenCalledWith(answer);
	});

	it('disables Overwrite when the step cannot be overwritten', () => {
		render(StepDialogs, { model: model({ conflict: { reason: 'gone', canOverwrite: false, after: 'leave' } }) });
		const overwrite = [...document.body.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.includes('Overwrite'))!;
		expect(overwrite.disabled).toBe(true);
		expect(document.body.textContent).toContain('was removed');
	});
});
