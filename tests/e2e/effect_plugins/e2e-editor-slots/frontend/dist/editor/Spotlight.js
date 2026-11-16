export default class Spotlight {
	constructor({ target }) {
		this.root = document.createElement('div');
		this.root.setAttribute('data-e2e-slot-overlay', 'spotlight');
		this.root.textContent = 'Spotlight marking';
		this.root.style.cssText = 'position:absolute;left:8px;top:8px;padding:2px 8px;background:#1f6feb;color:#fff;font-size:12px;border-radius:4px;pointer-events:none;z-index:5';
		target.appendChild(this.root);
	}

	$set() {}

	$destroy() {
		this.root.remove();
	}
}
