import { browser } from '$app/environment';

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
