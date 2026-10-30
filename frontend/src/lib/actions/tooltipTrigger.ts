export interface TooltipTriggerHandlers {
	delay: () => number;
	open: () => void;
	close: () => void;
	focusSelf?: () => boolean;
}

const WINDOW_RESTORE_GRACE_MS = 250;

let windowActive = true;
let restoredUntil = 0;

if (typeof window !== 'undefined') {
	window.addEventListener('blur', () => {
		windowActive = false;
	});
	window.addEventListener('focus', () => {
		windowActive = true;
		restoredUntil = performance.now() + WINDOW_RESTORE_GRACE_MS;
	});
	document.addEventListener('visibilitychange', () => {
		if (document.visibilityState === 'visible') restoredUntil = performance.now() + WINDOW_RESTORE_GRACE_MS;
	});
}

function isKeyboardFocus(target: EventTarget | null): boolean {
	if (!(target instanceof Element)) return false;
	try {
		return target.matches(':focus-visible');
	} catch {
		return true;
	}
}

export function focusIsUserDriven(e: FocusEvent): boolean {
	if (!windowActive || performance.now() < restoredUntil) return false;
	return isKeyboardFocus(e.target);
}

export function watchTooltipTrigger(node: HTMLElement, handlers: TooltipTriggerHandlers): () => void {
	let timer: ReturnType<typeof setTimeout> | undefined;
	let observer: MutationObserver | undefined;
	let watching = false;

	function cancelTimer() {
		clearTimeout(timer);
		timer = undefined;
	}

	function close() {
		cancelTimer();
		stopWatching();
		handlers.close();
	}

	const onWindowBlur = () => close();
	const onVisibility = () => {
		if (document.visibilityState === 'hidden') close();
	};
	const onPointerMove = (e: PointerEvent) => {
		if (!(e.target instanceof Node) || !node.contains(e.target)) close();
	};
	const onPointerDown = () => close();
	const onScroll = (e: Event) => {
		if (e.target instanceof Node && e.target.contains(node)) close();
	};
	const onKeydown = (e: KeyboardEvent) => {
		if (e.key === 'Escape') close();
	};

	function startWatching() {
		if (watching) return;
		watching = true;
		window.addEventListener('blur', onWindowBlur);
		document.addEventListener('visibilitychange', onVisibility);
		document.addEventListener('pointermove', onPointerMove, true);
		document.addEventListener('pointerdown', onPointerDown, true);
		document.addEventListener('scroll', onScroll, true);
		document.addEventListener('keydown', onKeydown, true);
		observer = new MutationObserver(() => {
			if (!node.isConnected) close();
		});
		observer.observe(document.documentElement, { childList: true, subtree: true });
	}

	function stopWatching() {
		if (!watching) return;
		watching = false;
		window.removeEventListener('blur', onWindowBlur);
		document.removeEventListener('visibilitychange', onVisibility);
		document.removeEventListener('pointermove', onPointerMove, true);
		document.removeEventListener('pointerdown', onPointerDown, true);
		document.removeEventListener('scroll', onScroll, true);
		document.removeEventListener('keydown', onKeydown, true);
		observer?.disconnect();
		observer = undefined;
	}

	function schedule(delay: number) {
		cancelTimer();
		startWatching();
		if (delay <= 0) handlers.open();
		else
			timer = setTimeout(() => {
				timer = undefined;
				if (node.isConnected) handlers.open();
			}, delay);
	}

	const onEnter = () => {
		if (document.visibilityState === 'hidden') return;
		schedule(handlers.delay());
	};
	const onFocusIn = (e: FocusEvent) => {
		if (handlers.focusSelf?.() && e.target !== node) return;
		if (!focusIsUserDriven(e)) return;
		schedule(0);
	};
	const onLeave = () => close();
	const onFocusOut = () =>
		queueMicrotask(() => {
			if (!node.contains(document.activeElement)) close();
		});

	node.addEventListener('mouseenter', onEnter);
	node.addEventListener('mouseleave', onLeave);
	node.addEventListener('focusin', onFocusIn);
	node.addEventListener('focusout', onFocusOut);

	return () => {
		close();
		node.removeEventListener('mouseenter', onEnter);
		node.removeEventListener('mouseleave', onLeave);
		node.removeEventListener('focusin', onFocusIn);
		node.removeEventListener('focusout', onFocusOut);
	};
}
