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

describe('the edit bar on desktop', () => {
	it('is the one header: Back, Editing, switcher, note, Discard and Save to elixir', () => {
		renderDesktop(model());
		const text = document.body.textContent ?? '';
		expect(document.body.querySelectorAll('[data-testid="step-edit-bar"]')).toHaveLength(1);
		expect(text).toContain('Back to Potion study');
		expect(text).toContain('Editing');
		expect(text).toContain('Orbit left');
		expect(text).toContain('1/3');
		expect(text).toContain('Steps');
		expect(text).toContain('Test runs go to History');
		expect(byText('Discard')).toBeTruthy();
		expect(byText('Save to elixir')).toBeTruthy();
	});

	it('routes the buttons to the model', () => {
		const m = model();
		renderDesktop(m);
		byLabel('Next step')!.click();
		expect(m.go).toHaveBeenCalledWith('b');
		byText('Save to elixir')!.click();
		expect(m.save).toHaveBeenCalled();
		byText('Discard')!.click();
		expect(m.back).toHaveBeenCalled();
	});

	it('disables Previous on the first step and Next on the last', () => {
		renderDesktop(model());
		expect(byLabel('Previous step')!.disabled).toBe(true);
		expect(byLabel('Next step')!.disabled).toBe(false);
		unmount(instance!);
		for (const item of extra.splice(0)) unmount(item);
		document.body.innerHTML = '';
		renderDesktop(model({ index: 2 }));
		expect(byLabel('Next step')!.disabled).toBe(true);
		expect(byLabel('Previous step')!.disabled).toBe(false);
	});

	it('lists every step with its preset in the Steps menu and switches on pick', () => {
		const m = model();
		renderDesktop(m);
		const trigger = buttons().find((button) => button.textContent?.includes('Steps'))!;
		trigger.click();
		flushSync();
		const items = [...document.body.querySelectorAll<HTMLElement>('[role="menuitemradio"]')];
		expect(items).toHaveLength(3);
		expect(items[1].textContent).toContain('Krea 2 · Text to image');
		expect(items[0].getAttribute('aria-checked')).toBe('true');
		items[2].click();
		expect(m.go).toHaveBeenCalledWith('c');
	});

	it('shows Unsaved once there are edits', () => {
		renderDesktop(model({ edits: 2 }));
		expect(document.body.querySelector('[data-testid="step-unsaved"]')).not.toBeNull();
	});

	it('locks the controls while saving', () => {
		renderDesktop(model({ saving: true }));
		expect(byText('Discard')!.disabled).toBe(true);
		expect(byLabel('Next step')!.disabled).toBe(true);
	});
});

describe('the edit bar for a followed step', () => {
	it('shows the session and Saved automatically, with no Save or Discard', () => {
		renderDesktop(model({ follow: true, sessionName: 'Bottle orbit', stepLabel: 'Close-up reveal', index: 1 }));
		expect(document.body.querySelector('[data-testid="step-follow-chip"]')?.textContent).toContain('Following Bottle orbit');
		expect(document.body.querySelector('[data-testid="step-autosaved"]')?.textContent).toContain('Saved automatically');
		expect(byText('Save to elixir')).toBeUndefined();
		expect(byText('Discard')).toBeUndefined();
		expect(document.body.querySelector('[data-testid="step-unsaved"]')).toBeNull();
	});
});

describe('the edit bar on a phone', () => {
	it('is one row: back arrow, Editing, menu and Save', () => {
		const m = model();
		render(StepEditBar, { model: m, part: 'phone' });
		expect(byLabel('Back to Potion study')).toBeTruthy();
		expect(document.body.textContent).toContain('Editing');
		expect(byLabel('Steps')).toBeTruthy();
		expect(byText('Save')).toBeTruthy();
		expect(document.body.textContent).not.toContain('Test runs go to History');
		byText('Save')!.click();
		expect(m.save).toHaveBeenCalled();
	});

	it('opens a Steps sheet with the steps and Discard changes', () => {
		const m = model();
		render(StepEditBar, { model: m, part: 'phone' });
		byLabel('Steps')!.click();
		flushSync();
		expect(document.body.querySelector('.lane-menu.sheet')).not.toBeNull();
		const items = [...document.body.querySelectorAll<HTMLElement>('[role="menuitemradio"]')];
		expect(items).toHaveLength(3);
		const discard = [...document.body.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((item) => item.textContent?.includes('Discard changes'));
		expect(discard).toBeTruthy();
		discard!.click();
		expect(m.back).toHaveBeenCalled();
	});

	it('has no Save and no Discard for a followed step', () => {
		render(StepEditBar, { model: model({ follow: true }), part: 'phone' });
		expect(byText('Save')).toBeUndefined();
		expect(document.body.querySelector('[data-testid="step-follow-chip"]')).not.toBeNull();
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
