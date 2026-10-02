export interface PageHeaderState {
	titleHidden: boolean;
	hidden: boolean;
}

export interface PageHeaderControls {
	setTitleHidden: (hidden: boolean) => void;
	setHidden: (hidden: boolean) => void;
	reset: () => void;
}

export function createPageHeaderControls(onChange: (state: PageHeaderState) => void): PageHeaderControls {
	const state: PageHeaderState = { titleHidden: false, hidden: false };

	function apply(key: keyof PageHeaderState, next: boolean) {
		if (state[key] === next) return;
		state[key] = next;
		onChange({ ...state });
	}

	return {
		setTitleHidden: (hidden) => apply('titleHidden', !!hidden),
		setHidden: (hidden) => apply('hidden', !!hidden),
		reset: () => {
			apply('titleHidden', false);
			apply('hidden', false);
		}
	};
}
