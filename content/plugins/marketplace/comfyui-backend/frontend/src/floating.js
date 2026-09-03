// Svelte action for a floating layer (dropdown menu, popover) that must
// render above every ancestor's `overflow: hidden` - including this plugin's
// own scroll containers (`.di-right`, `.di-field-list`, ...) and the host
// admin panel's master-detail scroll region. `overflow: hidden` clips a
// descendant's painting regardless of its own position scheme, so
// `position: fixed` alone does not escape it - the node has to actually move
// out of that ancestor's subtree. This appends `node` to `document.body` and
// positions it `fixed` from the anchor element's own rect, tracking it on
// scroll/resize and closing on the first click outside both the node and its
// anchor.
//
// Usage: <div use:floating={{ anchor: anchorEl, onOutsideClick: () => (open = false) }}>
export function floating(node, params) {
	let { anchor, onOutsideClick, offset = 4 } = params || {};

	node.style.position = 'fixed';
	node.style.margin = '0';
	document.body.appendChild(node);

	function reposition() {
		if (!anchor) return;
		const a = anchor.getBoundingClientRect();
		const n = node.getBoundingClientRect();
		let top = a.bottom + offset;
		if (top + n.height > window.innerHeight && a.top - n.height - offset >= 0) {
			top = a.top - n.height - offset;
		}
		let left = a.left;
		if (left + n.width > window.innerWidth) {
			left = Math.max(8, window.innerWidth - n.width - 8);
		}
		node.style.top = `${Math.max(8, top)}px`;
		node.style.left = `${left}px`;
	}

	reposition();
	window.addEventListener('scroll', reposition, true);
	window.addEventListener('resize', reposition);

	function handleOutsideClick(e) {
		if (node.contains(e.target)) return;
		if (anchor && anchor.contains(e.target)) return;
		onOutsideClick?.();
	}

	// Deferred a task so the click that opened this popover - already
	// mid-dispatch when the action mounts - can't immediately close it again.
	const armTimer = setTimeout(() => document.addEventListener('click', handleOutsideClick, true), 0);

	return {
		update(next) {
			anchor = next?.anchor;
			onOutsideClick = next?.onOutsideClick;
			offset = next?.offset ?? 4;
			reposition();
		},
		destroy() {
			clearTimeout(armTimer);
			window.removeEventListener('scroll', reposition, true);
			window.removeEventListener('resize', reposition);
			document.removeEventListener('click', handleOutsideClick, true);
			node.remove();
		}
	};
}
