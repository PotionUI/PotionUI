export default class FaultyOptions {
	constructor({ target, props }) {
		this.host = props.host;
		this.amount = 10;
		this.root = document.createElement('div');
		this.root.setAttribute('data-e2e-slot-options', 'Faulty');
		const caption = document.createElement('label');
		caption.textContent = 'Faulty amount';
		this.input = document.createElement('input');
		this.input.type = 'number';
		this.input.min = '0';
		this.input.max = '100';
		this.input.value = String(this.amount);
		this.input.setAttribute('aria-label', 'Faulty amount');
		this.input.addEventListener('input', () => {
			this.amount = Number(this.input.value);
			this.host.update(this.draft());
		});
		this.root.append(caption, this.input);
		target.appendChild(this.root);
		this.host.begin(this.draft());
	}

	draft() {
		return {
			tool: 'e2e-editor-slots.faulty',
			icon: 'extension',
			label: 'Faulty',
			summary: 'Strength ' + this.amount,
			params: { v: 1, amount: this.amount }
		};
	}

	$set() {}

	$destroy() {
		this.root.remove();
	}
}
