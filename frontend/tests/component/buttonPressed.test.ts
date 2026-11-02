import { describe, expect, it } from 'vitest';
import { mountComponent } from './helpers/mount';

const { default: Button } = await import('$lib/components/ui/Button.svelte');

function render(props: Record<string, unknown>) {
	return mountComponent(Button, props).target.querySelector('button')!;
}

describe('Button pressed and overlay', () => {
	it('leaves aria-pressed off when the button is not a toggle', () => {
		expect(render({ variant: 'ghost' }).hasAttribute('aria-pressed')).toBe(false);
	});

	it('shows a pressed toggle in signal in place of its variant colours', () => {
		const button = render({ variant: 'ghost', pressed: true });
		expect(button.getAttribute('aria-pressed')).toBe('true');
		expect(button.className).toContain('text-signal');
		expect(button.className).not.toContain('text-fg-muted');
		expect(render({ variant: 'ghost', pressed: false }).getAttribute('aria-pressed')).toBe('false');
	});

	it('draws the overlay variant with scrim tokens and presses it without signal', () => {
		const rest = render({ variant: 'overlay' });
		expect(rest.className).toContain('bg-scrim/60');
		expect(rest.className).toContain('text-scrim-on');
		const on = render({ variant: 'overlay', pressed: true });
		expect(on.className).toContain('bg-scrim-on/15');
		expect(on.className).not.toContain('signal');
	});
});
