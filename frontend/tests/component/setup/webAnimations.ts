// jsdom implements no Web Animations API at all, and Svelte 5's client
// transition runtime (svelte/internal/client/dom/elements/transitions.js)
// calls `element.animate(...)` directly rather than falling back to a
// rAF-driven CSS approach. Any component test that mounts a component using
// a Svelte `transition:`/`in:`/`out:` directive - not just the one that
// first surfaced this - throws `TypeError: element.animate is not a
// function` from deep inside the transition machinery once that directive
// runs, which vitest reports as an unhandled error rather than a normal
// assertion failure.
//
// This stub is intentionally minimal: it does not interpolate intermediate
// frames (nothing here reads computed style, so there is nothing to
// interpolate), it just finishes asynchronously so `onfinish` handlers -
// which is what Svelte's transition code relies on to swap the dummy
// duration-only animation for the real keyframe one, and finally to clean
// up the transition - still run.
class FakeAnimation {
	playState: 'idle' | 'running' | 'finished' = 'idle';
	currentTime = 0;
	effect: unknown = null;
	onfinish: ((event: unknown) => void) | null = null;
	oncancel: ((event: unknown) => void) | null = null;

	constructor() {
		queueMicrotask(() => {
			if (this.playState === 'idle') this.finish();
		});
	}

	finish() {
		this.playState = 'finished';
		this.onfinish?.({ type: 'finish' });
	}

	cancel() {
		this.playState = 'idle';
		this.oncancel?.({ type: 'cancel' });
	}

	pause() {}
	play() {}
	reverse() {}
	updatePlaybackRate() {}

	get finished() {
		return Promise.resolve(this);
	}
}

if (typeof Element !== 'undefined' && typeof Element.prototype.animate !== 'function') {
	Element.prototype.animate = function animate() {
		return new FakeAnimation() as unknown as Animation;
	};
}
