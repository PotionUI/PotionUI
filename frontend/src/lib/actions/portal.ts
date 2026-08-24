import { browser } from '$app/environment';

// Moves the node to <body> so `position: fixed` stays viewport-relative even
// when an ancestor has a `transform` (e.g. a slide-over panel), which would
// otherwise become the containing block.
export default function portal(node: HTMLElement) {
	if (browser) {
		document.body.appendChild(node);
	}
	return {
		destroy() {
			if (node.parentNode) {
				node.parentNode.removeChild(node);
			}
		}
	};
}
