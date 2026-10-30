export function emulateSyncFocusOutOnRemove(): () => void {
	const original = Element.prototype.remove;
	Element.prototype.remove = function (this: Element) {
		const active = document.activeElement;
		if (active && this.contains(active)) {
			active.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
		}
		return original.call(this);
	};
	return () => {
		Element.prototype.remove = original;
	};
}
