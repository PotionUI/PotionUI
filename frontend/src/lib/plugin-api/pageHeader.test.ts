import { describe, expect, it, vi } from 'vitest';
import { createPageHeaderControls } from './pageHeader';

describe('page header controls', () => {
	it('reports a hidden title once', () => {
		const onChange = vi.fn();
		const controls = createPageHeaderControls(onChange);
		controls.setTitleHidden(true);
		controls.setTitleHidden(true);
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith({ titleHidden: true, hidden: false });
	});

	it('shows the title again after a reset', () => {
		const onChange = vi.fn();
		const controls = createPageHeaderControls(onChange);
		controls.setTitleHidden(true);
		controls.reset();
		expect(onChange).toHaveBeenLastCalledWith({ titleHidden: false, hidden: false });
	});

	it('does not report a reset when nothing was hidden', () => {
		const onChange = vi.fn();
		createPageHeaderControls(onChange).reset();
		expect(onChange).not.toHaveBeenCalled();
	});

	it('treats a falsy value as visible', () => {
		const onChange = vi.fn();
		const controls = createPageHeaderControls(onChange);
		controls.setTitleHidden(true);
		controls.setTitleHidden(undefined as unknown as boolean);
		expect(onChange).toHaveBeenLastCalledWith({ titleHidden: false, hidden: false });
	});

	it('hides the whole header row independently of the title', () => {
		const onChange = vi.fn();
		const controls = createPageHeaderControls(onChange);
		controls.setHidden(true);
		controls.setHidden(true);
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith({ titleHidden: false, hidden: true });
	});

	it('reset restores both the title and the row', () => {
		const onChange = vi.fn();
		const controls = createPageHeaderControls(onChange);
		controls.setTitleHidden(true);
		controls.setHidden(true);
		controls.reset();
		expect(onChange).toHaveBeenLastCalledWith({ titleHidden: false, hidden: false });
	});
});
