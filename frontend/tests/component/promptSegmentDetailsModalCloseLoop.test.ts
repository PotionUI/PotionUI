// @vitest-environment jsdom
//
// fe74 (live, real browser) reported the SECOND "Details" click intercepted
// by a backdrop left over from the FIRST segment's modal — i.e. Save details
// did not actually close it before the next interaction. This drives the
// exact loop the spec does, through the SAME locators it uses
// (`getByRole('dialog', {name:'Segment details'})` /
// `getByRole('button', {name:'Save details'})`), with BaseModal's real
// `transition:fade`/`transition:scale` running (not flushSync-faked away) —
// a real 150ms motionDuration keeps the dialog node mounted a beat after
// `isOpen` flips false, which is exactly the window a premature "hidden"
// check could miss.
import { describe, it, expect, afterEach } from 'vitest';

const { default: PromptSegment } = await import('$lib/components/PromptSegment.svelte');
const { createClassComponent } = await import('svelte/legacy');

function segment() {
	return {
		id: 'seg-1',
		content: 'a lighthouse keeper',
		type: 'content',
		chips: {},
		enabled: true,
		name: 'Subject',
		color: '#3B82F6',
		description: 'A quick mood note'
	};
}

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

function mount() {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: PromptSegment as never,
		target,
		props: { segment: segment(), index: 0, total: 3 }
	});
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
});

function detailsButton() {
	return Array.from(document.querySelectorAll('button')).find(
		(b) => b.getAttribute('aria-label') === 'Details'
	) as HTMLButtonElement;
}

function dialog() {
	return Array.from(document.querySelectorAll('[role="dialog"]')).find(
		(el) => el.getAttribute('aria-label') === 'Segment details'
	) as HTMLElement | undefined;
}

function saveButton(scope: HTMLElement) {
	return Array.from(scope.querySelectorAll('button')).find((b) =>
		(b.textContent || '').includes('Save details')
	) as HTMLButtonElement;
}

// BaseModal's own transition timers need real elapsed time, not a
// synchronous flush — this is the exact gap a real click-then-click e2e
// sequence has to survive too.
function wait(ms: number) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

describe('PromptSegmentDetailsModal close loop', () => {
	it('Save details actually removes the dialog before the next Details click, which is not intercepted', async () => {
		mount();

		detailsButton().click();
		await wait(50);
		expect(dialog()).toBeTruthy();

		saveButton(dialog()!).click();
		// BaseModal's exit transition (~150ms) keeps the node mounted a beat
		// after isOpen flips false — the real bug is asserting "closed" before
		// this elapses.
		await wait(250);
		expect(dialog()).toBeUndefined();
		// Not just invisible — actually gone, so it can't intercept a click.
		expect(document.querySelector('[aria-label="Close modal"].fixed.inset-0')).toBeNull();

		// The next "Details" click must reach the button, not a leftover backdrop.
		detailsButton().click();
		await wait(50);
		expect(dialog()).toBeTruthy();
	});
});
